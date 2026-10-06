import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_audit_migration import prepare, batches
from request_bootstrap import fingerprint
from test_daemon import config as cfn_config, control as cfn_control, FakeGateway, FakeClock, Daemon
from test_ec2_daemon import config as ec2_config, control as ec2_control, Daemon as Ec2Daemon
from cloud_glider.daemon import SafetyViolation
from unittest.mock import Mock


def fixture():
    control = {'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}, 'environment': {'S': 'sandbox'},
               'start_requested': {'BOOL': False}, 'stop_requested': {'BOOL': False},
               'cleanup_requested': {'BOOL': False}, 'active_command': {'S': 'NONE'}, 'command_sequence': {'N': '9'}}
    return {'control': control,
            'current': {'PK': {'S': 'CURRENT'}, 'SK': {'S': 'GLOBAL'}, 'status': {'S': 'UNINITIALIZED'}},
            'request': {'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'}, 'request_id': {'S': '12'},
                        'status': {'S': 'READY'}, 'schema_version': {'S': '2'}, 'cleanup_status': {'S': 'COMPLETE'},
                        'propagation_enabled': {'BOOL': False}, 'bootstrap_requested': {'BOOL': False},
                        'cleanup_requested': {'BOOL': False}, 'control_sha256': {'S': fingerprint(control)}},
            'locks': [], 'generation_items': [],
            'audit_items': [{'PK': {'S': pk}, 'SK': {'S': 'EVENT#1'}, 'nested': {'M': {'value': {'N': '3'}}}}
                            for pk in ('AUDIT#PROPAGATION', 'AUDIT#RELEASE')]}


class AuditMigrationTests(unittest.TestCase):
    def test_copy_preserves_all_fields_and_cleanup_is_withheld(self):
        snapshot = fixture()
        before = copy.deepcopy(snapshot)
        result = prepare(snapshot, 'sandbox')
        self.assertEqual(snapshot, before)
        self.assertEqual(result['manifest']['item_count'], 2)
        writes = result['copy_requests'][0]['TransactItems'][1::2]
        self.assertEqual([entry['Put']['Item'] for entry in writes], snapshot['audit_items'])
        self.assertTrue(all(entry['Put']['TableName'] == 'cloud-glider-sandbox-audit' for entry in writes))
        self.assertEqual(result['removal_requests'], [])
        self.assertIsNone(result['cutover_request'])
        tx = prepare(snapshot, 'sandbox', snapshot['audit_items'])['cutover_request']['TransactItems']
        self.assertEqual(tx[0]['Put']['Item']['command_sequence'], {'N': '9'})
        self.assertEqual(tx[1]['Put']['Item']['request_id'], {'S': '12'})
        self.assertEqual(tx[1]['Put']['Item']['control_sha256'], {'S': fingerprint(tx[0]['Put']['Item'])})

    def test_retry_skips_verified_copies_and_removal_checks_both_tables(self):
        snapshot = fixture()
        result = prepare(snapshot, 'sandbox', snapshot['audit_items'], prepare_removal=True)
        self.assertEqual(result['copy_requests'], [])
        actions = result['removal_requests'][0]['TransactItems']
        self.assertEqual(actions[0]['ConditionCheck']['TableName'], 'cloud-glider-sandbox-audit')
        self.assertEqual(actions[1]['Delete']['TableName'], 'cloud-glider-sandbox-state')
        self.assertIn('nested', actions[1]['Delete']['ExpressionAttributeNames'].values())

    def test_collision_missing_copy_duplicates_and_wrong_partition_fail(self):
        snapshot = fixture()
        changed = copy.deepcopy(snapshot['audit_items'])
        changed[0]['nested'] = {'S': 'changed'}
        with self.assertRaises(ValueError): prepare(snapshot, 'sandbox', changed)
        with self.assertRaises(ValueError): prepare(snapshot, 'sandbox', [], prepare_removal=True)
        with self.assertRaises(ValueError): prepare(snapshot, 'sandbox', prepare_removal=True)
        for items in (snapshot['audit_items'] * 2, [{'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}}]):
            with self.assertRaises(ValueError): prepare({**snapshot, 'audit_items': items}, 'sandbox')

    def test_enabled_busy_owned_and_stale_snapshot_fail(self):
        for section, field, value in (
            ('request', 'propagation_enabled', {'BOOL': True}),
            ('request', 'bootstrap_requested', {'BOOL': True}),
            ('request', 'cleanup_requested', {'BOOL': True}),
            ('request', 'control_sha256', {'S': 'stale'}),
            ('current', 'status', {'S': 'CURRENT'}),
            ('control', 'start_requested', {'BOOL': True}),
            ('control', 'environment', {'S': 'other'}),
        ):
            with self.subTest(field=field):
                snapshot = fixture(); snapshot[section][field] = value
                with self.assertRaises(ValueError): prepare(snapshot, 'sandbox')
        for field in ('locks', 'generation_items'):
            with self.assertRaises(ValueError): prepare({**fixture(), field: [{}]}, 'sandbox')

    def test_batches_bound_actions_bytes_and_keep_pairs(self):
        pairs = [[{'ConditionCheck': {'id': n}}, {'Delete': {'id': n}}] for n in range(51)]
        requests = list(batches(pairs))
        self.assertEqual([len(r['TransactItems']) for r in requests], [100, 2])
        with self.assertRaises(ValueError): list(batches([[{'payload': 'x' * 3_000_001}]]))

    def test_cli_requires_attestations_and_has_no_apply_mode(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_audit_migration.py'),
                                 '--snapshot', '/nonexistent'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Attest', result.stderr)

    def test_both_backends_pin_destination_and_reject_mismatched_control(self):
        for cfg, ctrl in ((cfn_config(), cfn_control()), (ec2_config(), ec2_control())):
            self.assertEqual(cfg.audit_table_name, 'cloud-glider-sandbox-audit')
        for cls, cfg, ctrl in ((Daemon, cfn_config(), cfn_control), (Ec2Daemon, ec2_config(), ec2_control)):
            daemon = object.__new__(cls); daemon.config = cfg
            with self.assertRaises(SafetyViolation) as raised:
                daemon._validated_control(ctrl(audit_table_name='foreign'))
            self.assertEqual(raised.exception.code, 'AUDIT_TABLE_MISMATCH')
            with self.assertRaises(SafetyViolation):
                missing = ctrl(); missing.pop('audit_table_name'); daemon._validated_control(missing)


class ReleaseAuditPreparationTests(unittest.TestCase):
    def test_release_requests_preserve_contents_and_reject_wrong_partition(self):
        from prepare_release_audit import prepare as release_request
        item = {'PK': {'S': 'AUDIT#RELEASE'}, 'SK': {'S': 'RELEASE#1'},
                'previous_control': {'M': fixture()['control']}}
        request = release_request(item, 'sandbox')
        self.assertEqual(request['TransactItems'][0]['Put']['TableName'], 'cloud-glider-sandbox-audit')
        self.assertEqual(request['TransactItems'][0]['Put']['Item'], item)
        with self.assertRaises(ValueError): release_request({**item, 'PK': {'S': 'CONTROL'}}, 'sandbox')
        with self.assertRaises(ValueError): release_request({**item, 'environment': {'S': 'other'}}, 'sandbox')


class EmergencyHoldAuditTests(unittest.TestCase):
    def test_hold_generation_error_and_audit_share_one_transaction(self):
        import importlib.util
        if importlib.util.find_spec('boto3') is None:
            self.skipTest('Install daemon/requirements.txt for SDK tests')
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        block = (ROOT / 'cfn/foundation.yaml').read_text().split('  EmergencyHoldFunction:\n', 1)[1]
        source = block.split('        ZipFile: |\n', 1)[1].split('\n  EmergencyHoldErrorsAlarm:', 1)[0]
        source = '\n'.join(line[10:] if line.startswith('          ') else line for line in source.splitlines())
        db = Mock()
        class Canceled(Exception): pass
        db.exceptions.TransactionCanceledException = Canceled
        db.get_item.return_value = {}
        namespace = {}
        with patch('boto3.client', return_value=db):
            exec(compile(source, '<emergency-hold-lambda>', 'exec'), namespace)
        event = {'request_id': '1', 'generation': '000001', 'error_code': 'HEALTH_INVALID', 'correlation_id': 'id'}
        env = {'TABLE_NAME': 'cloud-glider-sandbox-state', 'GENERATION_TABLE': 'cloud-glider-sandbox-generations',
               'AUDIT_TABLE': 'cloud-glider-sandbox-audit', 'ENVIRONMENT': 'sandbox'}
        context = SimpleNamespace(invoked_function_arn='arn:aws:lambda:us-west-2:111122223333:function:hold')
        with patch.dict(os.environ, env):
            result = namespace['handler'](event, context)
            tx = db.transact_write_items.call_args.kwargs['TransactItems']
            self.assertEqual([next(iter(entry.values()))['TableName'] for entry in tx],
                             [env['TABLE_NAME'], env['TABLE_NAME'], env['GENERATION_TABLE'], env['AUDIT_TABLE']])
            self.assertTrue(result['hold_created'])
            self.assertEqual(tx[-1]['Put']['Item']['SK'], {'S': 'LATEST_HOLD'})
            db.get_item.return_value = {'Item': {'PK': {'S': 'HOLD'}}}
            self.assertFalse(namespace['handler'](event, context)['hold_created'])
            self.assertIn('ConditionCheck', db.transact_write_items.call_args.kwargs['TransactItems'][1])
            db.transact_write_items.side_effect = Canceled('audit transaction rejected')
            with self.assertRaises(Canceled): namespace['handler'](event, context)
