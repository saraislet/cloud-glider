"""Failure-path checks for conservative trial completion claims."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import trial_receipt as receipt


class TrialReceiptTests(unittest.TestCase):
    def observations(self):
        scope = dict(account='123456789012', region='us-west-2', role_arn='arn:aws:iam::123456789012:role/task',
                     approved_image_id='ami-1', daemon_sha256='digest', task='Anna Sarai', trial='1', kind='smoke', started_at=receipt.now(),
                     ownership_reviewed_at=receipt.now(), no_other_operator_work=True,
                     stacks=['arn:aws:cloudformation:us-west-2:123456789012:stack/smoke/id'],
                     retained_control_plane=['shared subnet and security group'], coverage='exact isolated smoke stack')
        before = dict(scope=scope, errors=[], finished_at=receipt.now(), instances={'i-1': {'InstanceId': 'i-1', 'ImageId': 'ami-1'}},
                      stacks={scope['stacks'][0]: {'Stacks': [{'StackId': scope['stacks'][0]}]}},
                      console={'i-1': {'decoded': '{"result":"CLOUD_GLIDER_AMI_SMOKE_PASS","daemon_sha256":"digest"}'}},
                      metrics={'i-1': {k: {'Datapoints': [1]} for k in receipt.METRICS}})
        after = dict(scope=scope, errors=[], started_at=receipt.now(),
                     stacks={scope['stacks'][0]: {'Stacks': [{'StackId': scope['stacks'][0], 'StackStatus': 'DELETE_COMPLETE'}]}},
                     instances={'i-1': {'State': {'Name': 'terminated'}}},
                     volumes={'vol-1': {'absent': True}}, network_interfaces={}, addresses={}, state=[])
        return before, after

    def test_smoke_complete_requires_actual_readback(self):
        before, after = self.observations()
        self.assertEqual(receipt.evaluate(before, after)['status'], 'VERIFIED')
        after['instances']['i-1']['State']['Name'] = 'stopped'
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_failure_and_missing_console_override_pass(self):
        for console in ('', 'CLOUD_GLIDER_AMI_SMOKE_PASS CLOUD_GLIDER_AMI_SMOKE_FAIL'):
            before, after = self.observations()
            before['console']['i-1']['decoded'] = console
            self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_denial_residue_metrics_stack_and_scope_never_complete(self):
        for field in ('errors', 'volumes', 'stacks', 'metrics', 'scope'):
            before, after = self.observations()
            if field == 'errors': after[field] = [{'error': 'AccessDenied'}]
            if field == 'volumes': after[field]['vol-1'] = {'State': 'available'}
            if field == 'stacks': after[field] = {}
            if field == 'metrics': before[field]['i-1']['CPUSurplusCreditsCharged'] = {'Datapoints': []}
            if field == 'scope': after[field] = {**after[field], 'trial': '2'}
            self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE', field)

    def test_stale_or_conflicting_operator_review(self):
        before, _ = self.observations()
        scope = before['scope']
        receipt.validate_scope(scope)
        scope['ownership_reviewed_at'] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        with self.assertRaises(ValueError): receipt.validate_scope(scope)
        scope['ownership_reviewed_at'] = receipt.now()
        scope['no_other_operator_work'] = False
        with self.assertRaises(ValueError): receipt.validate_scope(scope)

    def test_wrong_account_before_any_resource_client(self):
        before, _ = self.observations()
        class Session:
            region_name = 'us-west-2'
            def client(self, service):
                self.assertion = service
                if service != 'sts': raise AssertionError('Resource read before account verification')
                return self
            def get_caller_identity(self):
                return {'Account': '000000000000', 'Arn': 'unexpected'}
        with self.assertRaises(ValueError): receipt.collect(Session(), before['scope'])

    def test_stop_hold_or_failed_handoff_never_complete(self):
        before, after = self.observations()
        before['scope']['kind'] = 'propagation'
        # No lifecycle/state completion: disabled propagation or HOLD alone is insufficient.
        for state in ([], [{'PK': {'S': 'HOLD'}, 'SK': {'S': 'ACTIVE'}}],
                      [{'PK': {'S': 'LOCK'}, 'SK': {'S': 'PROVISIONING'}}]):
            after['state'] = state
            self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_mocked_capture_tracks_attachments_and_verifies_exact_absence(self):
        import base64
        before, _ = self.observations()
        scope = before['scope']
        class Missing(Exception):
            def __init__(self, code):
                self.response = {'Error': {'Code': code}}
        class Paginator:
            def paginate(self, **kwargs):
                return [{'StackResourceSummaries': [{'ResourceType': 'AWS::EC2::Instance', 'PhysicalResourceId': 'i-1'}]}]
        class AWS:
            deleted = False
            denied = False
            calls = []
            def client(self, service):
                self.calls.append(service)
                return self
            region_name = 'us-west-2'
            def get_caller_identity(self):
                return {'Account': scope['account'], 'Arn': 'arn:aws:sts::123456789012:assumed-role/task/session'}
            def describe_stacks(self, **kwargs):
                return {'Stacks': [{'StackId': scope['stacks'][0], 'StackStatus': 'DELETE_COMPLETE' if self.deleted else 'CREATE_COMPLETE'}]}
            def get_paginator(self, method):
                if method != 'list_stack_resources': raise AssertionError(method)
                return Paginator()
            def describe_instances(self, **kwargs):
                return {'Reservations': [{'Instances': [{'InstanceId': 'i-1', 'ImageId': 'ami-1',
                    'State': {'Name': 'terminated' if self.deleted else 'stopped'},
                    'BlockDeviceMappings': [] if self.deleted else [{'Ebs': {'VolumeId': 'vol-1'}}],
                    'NetworkInterfaces': [] if self.deleted else [{'NetworkInterfaceId': 'eni-1'}]}]}]}
            def describe_addresses(self, **kwargs):
                if 'Filters' in kwargs: return {'Addresses': [{'AllocationId': 'eipalloc-1'}]}
                if self.deleted: raise Missing('InvalidAllocationID.NotFound')
                return {'Addresses': [{'AllocationId': 'eipalloc-1'}]}
            def describe_volumes(self, **kwargs):
                if self.denied: raise Missing('UnauthorizedOperation')
                if self.deleted: raise Missing('InvalidVolume.NotFound')
                return {'Volumes': [{'VolumeId': 'vol-1', 'State': 'in-use'}]}
            def describe_network_interfaces(self, **kwargs):
                if self.deleted: raise Missing('InvalidNetworkInterfaceID.NotFound')
                return {'NetworkInterfaces': [{'NetworkInterfaceId': 'eni-1'}]}
            def describe_instance_status(self, **kwargs): return {'InstanceStatuses': []}
            def get_console_output(self, **kwargs):
                return {'Output': base64.b64encode(b'{"result":"CLOUD_GLIDER_AMI_SMOKE_PASS","daemon_sha256":"digest"}').decode()}
            def get_metric_statistics(self, **kwargs): return {'Datapoints': [{'Sum': 1}]}
        aws = AWS()
        captured = receipt.collect(aws, scope)
        self.assertEqual(aws.calls[0], 'sts')
        self.assertEqual(set(captured['volumes']), {'vol-1'})
        self.assertEqual(set(captured['addresses']), {'eipalloc-1'})
        self.assertFalse(captured['errors'])
        aws.deleted = True
        verified = receipt.collect(aws, scope, captured)
        self.assertEqual(receipt.evaluate(captured, verified)['status'], 'VERIFIED')
        # Duplicate verification is read-only and preserves the same resource targets.
        retry = receipt.collect(aws, scope, captured)
        self.assertEqual(receipt.evaluate(captured, retry)['status'], 'VERIFIED')
        aws.denied = True
        denied = receipt.collect(aws, scope, captured)
        self.assertEqual(receipt.evaluate(captured, denied)['status'], 'INCOMPLETE')
        self.assertIsNone(denied['volumes']['vol-1'])

    def test_scope_pins_console_substrings_and_lost_readback(self):
        before, after = self.observations()
        before['console']['i-1']['decoded'] = 'printed text containing CLOUD_GLIDER_AMI_SMOKE_PASS'
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')
        before, after = self.observations()
        before['volumes'] = {'vol-omitted': {}}
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')
        before, after = self.observations()
        before['instances']['i-1']['ImageId'] = 'ami-wrong'
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_unsupported_resource_retention_and_old_inventory(self):
        before, after = self.observations()
        before['unsupported_resources'] = [{'ResourceType': 'AWS::EC2::SecurityGroup'}]
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')
        before, after = self.observations()
        after['scope']['retained_exceptions'] = [{'resource': 'vol-1', 'reason': 'inspection'}]
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')
        before, after = self.observations()
        before['finished_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_incomplete_propagation_evidence_cannot_be_promoted(self):
        before, after = self.observations()
        before['scope']['kind'] = 'propagation'
        before['evidence'] = {'validated': True, 'bundle': {}}
        self.assertFalse(receipt.validate_evidence({}, before['scope'], before)['validated'])
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_propagation_verified_cleanup_requires_complete_cycle_and_timing(self):
        import hashlib
        import json
        before, after = self.observations()
        scope = before['scope']
        scope.update(kind='propagation', launch_template_id='lt-1', launch_template_version='1',
                     source_commit='commit', daemon_sha256='digest')
        before['instances']['i-1']['Tags'] = [{'Key': k, 'Value': v} for k, v in {
            'bootstrap-request-id': '1', 'aws:ec2launchtemplate:id': 'lt-1', 'aws:ec2launchtemplate:version': '1'}.items()]
        def row(pk, fields):
            return {'PK': {'S': pk}, 'SK': {'S': 'REQUEST' if pk == 'BOOTSTRAP' else 'GLOBAL'}, **fields}
        after['state'] = [row('CONTROL', {k: {'BOOL': False} for k in
            ('propagation_enabled', 'start_requested', 'stop_requested', 'cleanup_requested')}),
            row('CURRENT', {'status': {'S': 'UNINITIALIZED'}}),
            row('BOOTSTRAP', {'propagation_enabled': {'BOOL': False}, 'cleanup_requested': {'BOOL': False},
                 'bootstrap_requested': {'BOOL': False}, 'cleanup_status': {'S': 'COMPLETE'},
                 'cleanup_target_request_id': {'S': '1'}, 'request_id': {'S': '2'}, 'status': {'S': 'READY'}})]
        after['state'][0].update(active_command={'S': 'NONE'}, operation_status={'S': 'COMPLETE'})
        identity = dict(instance_id='i-1', generation='000000', request_id='1', source_commit='commit',
                        daemon_sha256='digest', environment='sandbox', boot_id='boot')
        records = []
        for phase, context in [('entrypoint_started', ''), ('runtime_imports', ''), ('ec2_runtime_imports', ''),
                               ('sdk_initialization', ''), ('baked_image_verification', 'user_data'),
                               ('baked_image_verification', 'service_pre')]:
            n = len(records) + 1
            records.append({**identity, 'producer_id': 'p', 'sequence': n, 'record_id': 'p:' + str(n),
                            'event': 'boot_timing', 'phase': phase, 'boot_context': context})
        digest = hashlib.sha256()
        for r in records: digest.update(json.dumps(r, sort_keys=True, separators=(',', ':')).encode() + b'\n')
        records.append({**identity, 'producer_id': 'p', 'event': 'timing_collection_complete', 'complete': True,
                        'dropped': 0, 'upload_errors': 0, 'accepted': 6, 'uploaded': 6, 'sha256': digest.hexdigest()})
        observation = dict(instance_id='i-1', request_id='1', source='private raw log', details={'observed': 'test fixture'}, observed_at=receipt.now(), reviewed_by=scope['task'])
        bundle = {'expected': [identity], 'timing_records': records,
                  'observations': {k: [observation] for k in ('health', 'identity', 'failure')}}
        before['evidence'] = receipt.validate_evidence(bundle, scope, before)
        self.assertTrue(before['evidence']['validated'])
        self.assertEqual(receipt.evaluate(before, after)['status'], 'VERIFIED')
        self.assertEqual(receipt.evaluate(before, after)['trial_outcome'], 'REVIEW_REQUIRED')
        after['state'].append({'PK': {'S': 'GEN#r0'}, 'SK': {'S': 'SUBMISSION'}, 'settled': {'BOOL': False}})
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')
        after['state'].pop()
        bundle['timing_records'][0]['phase'] = 'corrupted'
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_alarm_or_skipped_stack_resources_remain_incomplete(self):
        before, after = self.observations()
        before['alarms'] = {'trial-status': {'MetricAlarms': [{}]}}
        after['alarms'] = {'trial-status': {'MetricAlarms': [{}]}}
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')
        after['alarms']['trial-status'] = {'MetricAlarms': [], 'CompositeAlarms': []}
        self.assertEqual(receipt.evaluate(before, after)['status'], 'VERIFIED')
        after['errors'] = [{'operation': 'stack-resources', 'error': 'DELETE_SKIPPED'}]
        self.assertEqual(receipt.evaluate(before, after)['status'], 'INCOMPLETE')

    def test_preflight_rejects_existing_trial_residue(self):
        before, capture = self.observations()
        capture.update(snapshots={}, nat_gateways={}, vpc_endpoints={})
        self.assertEqual(receipt.preflight(capture)['status'], 'VERIFIED')
        capture['volumes']['vol-1'] = {'State': 'available'}
        self.assertEqual(receipt.preflight(capture)['status'], 'INCOMPLETE')

    def test_private_exclusive_receipt_and_escape_rejected(self):
        root = SCRIPTS.parent / '.artifacts'
        with tempfile.TemporaryDirectory(dir=root if root.exists() else SCRIPTS.parent) as directory:
            path = Path(directory) / 'receipt.json'
            if not path.is_relative_to(root):
                with self.assertRaises(ValueError): receipt.private_save(path, {})
            else:
                relative = path.with_name('relative.json').relative_to(SCRIPTS.parent)
                receipt.private_save(relative, {})
                self.assertEqual((SCRIPTS.parent / relative).stat().st_mode & 0o777, 0o600)
                receipt.private_save(path, {})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError): receipt.private_save(path, {})
        with self.assertRaises(ValueError): receipt.private_save('/tmp/public-receipt.json', {})


if __name__ == '__main__':
    unittest.main()
