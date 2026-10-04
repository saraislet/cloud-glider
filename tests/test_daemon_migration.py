import copy
import importlib.util
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_daemon_migration import build_transaction
from request_bootstrap import fingerprint


class DaemonMigrationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            'control': {'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'},
                'start_requested': {'BOOL': False}, 'stop_requested': {'BOOL': False},
                'cleanup_requested': {'BOOL': False}, 'active_command': {'S': 'NONE'},
                'command_sequence': {'N': '7'}, 'propagation_backend': {'S': 'ec2'},
                **{'agent_artifact_' + k: {'S': 'a' * 64 if k == 'sha256' else 'old'}
                   for k in ('bucket', 'key', 'version_id', 'sha256')}},
            'current': {'PK': {'S': 'CURRENT'}, 'SK': {'S': 'GLOBAL'}, 'status': {'S': 'UNINITIALIZED'}},
            'request': {'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'},
                'schema_version': {'S': '2'}, 'status': {'S': 'READY'}, 'request_id': {'S': '11'},
                'bootstrap_requested': {'BOOL': False}, 'propagation_enabled': {'BOOL': False},
                'cleanup_requested': {'BOOL': False}, 'cleanup_status': {'S': 'COMPLETE'}},
        }
        self.snapshot['request']['control_sha256'] = {'S': fingerprint(self.snapshot['control'])}
        self.approved = {k.replace('agent_artifact_', 'daemon_artifact_'): copy.deepcopy(v)
                         for k, v in self.snapshot['control'].items()}
        self.approved['daemon_artifact_sha256'] = {'S': 'b' * 64}

    def build(self):
        return build_transaction('state', self.snapshot, self.approved, 'operator', 'event')

    def test_refreshes_fingerprint_preserves_counters_and_checks_locks(self):
        before = copy.deepcopy(self.snapshot)
        txn = self.build()
        self.assertEqual(txn[1]['Put']['Item']['control_sha256'], {'S': fingerprint(self.approved)})
        self.assertEqual(txn[1]['Put']['Item']['request_id'], {'S': '11'})
        self.assertEqual(txn[0]['Put']['Item']['command_sequence'], {'N': '7'})
        self.assertEqual([x['ConditionCheck']['Key']['SK']['S'] for x in txn[3:5]],
                         ['PROPAGATION', 'PROVISIONING'])
        self.assertTrue(all('ConditionExpression' in next(iter(x.values())) for x in txn))
        self.assertEqual(self.snapshot, before)
        self.assertNotIn('HOLD', str(txn))

    def test_rejects_enabled_gates_pending_commands_live_owner_and_cleanup(self):
        for section, field, value in (
            ('control', 'start_requested', {'BOOL': True}),
            ('control', 'active_command', {'S': 'START'}),
            ('request', 'propagation_enabled', {'BOOL': True}),
            ('request', 'bootstrap_requested', {'BOOL': True}),
            ('request', 'cleanup_status', {'S': 'NEEDS_ATTENTION'}),
            ('current', 'instance_id', {'S': 'i-live'}),
        ):
            with self.subTest(field=field):
                saved = copy.deepcopy(self.snapshot)
                self.snapshot[section][field] = value
                with self.assertRaises(ValueError): self.build()
                self.snapshot = saved

    def test_rejects_backend_or_counter_changes(self):
        for field, value in (('propagation_backend', {'S': 'cloudformation'}),
                             ('command_sequence', {'N': '0'})):
            with self.subTest(field=field):
                saved = copy.deepcopy(self.approved)
                self.approved[field] = value
                with self.assertRaises(ValueError): self.build()
                self.approved = saved

    def test_rejects_repeat_or_partial_migration(self):
        self.snapshot['control']['daemon_artifact_key'] = {'S': 'new'}
        with self.assertRaises(ValueError): self.build()

    def test_rejects_invalid_digest(self):
        self.approved['daemon_artifact_sha256'] = {'S': 'invalid'}
        with self.assertRaises(ValueError): self.build()

    def test_rejects_stale_approval(self):
        self.snapshot['request']['control_sha256'] = {'S': 'stale'}
        with self.assertRaises(ValueError): self.build()

    @unittest.skipUnless(importlib.util.find_spec("boto3"), "boto3 is optional for dependency-free checks")
    def test_transaction_matches_sdk_model(self):
        import boto3
        from botocore.validate import validate_parameters
        client = boto3.client('dynamodb', region_name='us-west-2',
                              aws_access_key_id='testing', aws_secret_access_key='testing')
        self.addCleanup(client.close)
        validate_parameters({'TransactItems': self.build()},
                            client.meta.service_model.operation_model('TransactWriteItems').input_shape)
