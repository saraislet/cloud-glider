import copy
import hashlib
import json
import unittest

from scripts.request_bootstrap import fingerprint
from scripts.verify_sandbox_release import smoke_result, smoke_signal_result, verify_pins


class SandboxReleaseTests(unittest.TestCase):
    def setUp(self):
        self.launch = {'ImageId': 'ami-test'}
        self.config = {'daemon_artifact_sha256': 'digest', 'template_build_id': 'commit',
                       'daemon_artifact_bucket': 'bucket', 'daemon_artifact_key': 'key',
                       'daemon_artifact_version_id': 'version', 'template_bucket': 'bucket',
                       'template_key': 'seed', 'template_s3_version_id': 'seed-version',
                       'template_sha256': 'seed-digest'}
        self.control = {'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'},
                        'active_command': {'S': 'NONE'},
                        'launch_template_sha256': {'S': hashlib.sha256(json.dumps(
                            self.launch, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}}
        for field, value in self.config.items():
            self.control[{'template_bucket': 'template_s3_bucket',
                          'template_key': 'template_s3_key'}.get(field, field)] = {'S': value}
        for field in ('start_requested', 'stop_requested', 'cleanup_requested'):
            self.control[field] = {'BOOL': False}
        self.request = {'status': {'S': 'READY'}, 'cleanup_status': {'S': 'COMPLETE'},
                        'control_sha256': {'S': fingerprint(self.control)}}
        for field in ('bootstrap_requested', 'propagation_enabled', 'cleanup_requested'):
            self.request[field] = {'BOOL': False}
        self.current = {'status': {'S': 'UNINITIALIZED'}}
        self.mapping = {'State': 'Enabled', 'LastProcessingResult': 'OK'}

    def check(self, hold=None):
        verify_pins(self.control, self.request, self.current, hold or {}, self.launch,
                    self.config, self.mapping, 'ami-test', 'digest', 'commit')

    def test_consistent_idle_release(self):
        self.check()

    def test_incomplete_or_live_release_rejected(self):
        cases = [('mapping', 'State', 'Disabled'), ('mapping', 'LastProcessingResult', 'Error'),
                 ('launch', 'ImageId', 'ami-old'), ('config', 'daemon_artifact_sha256', 'old'),
                 ('config', 'template_s3_version_id', 'old'),
                 ('current', 'status', {'S': 'RUNNING'}),
                 ('request', 'propagation_enabled', {'BOOL': True}),
                 ('request', 'cleanup_status', {'S': 'NEEDS_ATTENTION'}),
                 ('request', 'control_sha256', {'S': 'old'}),
                 ('control', 'launch_template_sha256', {'S': 'old'}),
                 ('control', 'start_requested', {'BOOL': True})]
        for obj, field, value in cases:
            with self.subTest(obj=obj, field=field):
                original = copy.deepcopy(getattr(self, obj))
                getattr(self, obj)[field] = value
                with self.assertRaises(ValueError):
                    self.check()
                setattr(self, obj, original)
        with self.assertRaises(ValueError):
            self.check({'active': True})

    def test_smoke_requires_matching_explicit_result_and_contract(self):
        result = {'result': 'CLOUD_GLIDER_AMI_SMOKE_PASS', 'daemon_sha256': 'digest',
                  'ec2_contract': 'EC2_BAKED_CONTRACT_PASS', 'free_bytes': 384 * 1024 * 1024}
        self.assertEqual(smoke_result('[boot] ' + json.dumps(result), 'digest'), result)
        for field, value in [('daemon_sha256', 'old'), ('ec2_contract', 'FAIL'), ('free_bytes', 1)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                smoke_result(json.dumps({**result, field: value}), 'digest')
        for text in ('CREATE_COMPLETE', 'CLOUD_GLIDER_AMI_SMOKE_PASS',
                     json.dumps(result) + '\nCLOUD_GLIDER_AMI_SMOKE_FAIL'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                smoke_result(text, 'digest')

class SmokeSignalTests(unittest.TestCase):
    def test_retained_signal_requires_explicit_matching_guest_result(self):
        result = {'result': 'CLOUD_GLIDER_AMI_SMOKE_PASS', 'daemon_sha256': 'digest',
                  'ec2_contract': 'EC2_BAKED_CONTRACT_PASS', 'free_bytes': 500*1024*1024}
        def stack(signals):
            return {'Outputs': [{'OutputKey': 'SmokeResultData', 'OutputValue': json.dumps(signals)}]}
        self.assertEqual(smoke_signal_result(stack({'runtime': json.dumps(result)}), 'digest'), result)
        for bad in ({}, {'other': json.dumps(result)}, {'runtime': json.dumps({**result, 'daemon_sha256': 'foreign'})},
                    {'runtime': json.dumps({**result, 'result': 'CLOUD_GLIDER_AMI_SMOKE_FAIL'})}):
            with self.assertRaises(ValueError): smoke_signal_result(stack(bad), 'digest')
        with self.assertRaises(ValueError): smoke_signal_result({}, 'digest')
