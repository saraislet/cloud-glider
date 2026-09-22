import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load('bootstrap_handler', 'bootstrap/handler.py')
requester = load('request_bootstrap', 'scripts/request_bootstrap.py')
initializer = load('bootstrap_initializer', 'scripts/initialize_control.py')


class MissingStack(Exception):
    response = {'Error': {'Code': 'ValidationError', 'Message': 'Stack does not exist'}}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        args = initializer.parse_args([
            '--table-name', 'table', '--operator-id', 'operator', '--template-version', 'v1',
            '--bootstrap-version', 'bootstrap-v1', '--template-bucket', 'bucket',
            '--template-key', 'generation/generation.yaml', '--template-s3-version-id', 'template-version',
            '--template-sha256', hashlib.sha256(b'template').hexdigest(), '--template-build-id', 'abcdefg',
            '--agent-artifact-bucket', 'bucket', '--agent-artifact-key', 'generation/agent.tar.gz',
            '--agent-artifact-version-id', 'agent-version', '--agent-artifact-sha256', hashlib.sha256(b'agent').hexdigest()])
        initial = initializer.build_transaction(args, now='now', event_id='id')
        self.control = initial[0]['Put']['Item']
        self.current = initial[1]['Put']['Item']
        self.request = requester.build_transaction('table', self.control, 'operator', 'test-request', 100)[3]['Put']['Item']
        self.event = copy.deepcopy(self.request)
        self.hold = {}
        self.ddb, self.cfn, self.s3, self.ec2 = Mock(), Mock(), Mock(), Mock()
        self.env = {'STATE_TABLE': 'table', 'ENVIRONMENT': 'sandbox', 'ARTIFACT_BUCKET': 'bucket',
                    'SERVICE_ROLE_ARN': 'role', 'GENERATION_PARAMETERS': json.dumps({
                        'ApprovedImageId': 'ami-approved', 'RootDeviceName': '/dev/xvda', 'Owner': 'owner'})}
        self.cfn.describe_stacks.side_effect = MissingStack()
        self.cfn.create_stack.return_value = {'StackId': 'stack-id'}
        self.ddb.transact_get_items.side_effect = lambda **kw: {'Responses': [
            {'Item': copy.deepcopy(v)} for v in (self.control, self.current, self.hold, self.request)]}
        def update(**kw):
            values = kw['ExpressionAttributeValues']
            expected = values.get(':requested', values.get(':creating'))
            if self.request['status'] != expected:
                raise RuntimeError('conditional check failed')
            self.request['status'] = copy.deepcopy(values.get(':submitted', values[':creating']))
        self.ddb.update_item.side_effect = update
        self.ec2.get_paginator.return_value.paginate.return_value = [{'Reservations': []}]
        self.ec2.describe_images.return_value = {'Images': [{'Architecture': 'arm64', 'State': 'available',
                                                            'RootDeviceType': 'ebs', 'RootDeviceName': '/dev/xvda'}]}
        self.s3.get_object.side_effect = lambda **kw: {'VersionId': kw['VersionId'],
            'Body': io.BytesIO(b'template' if kw['VersionId'] == 'template-version' else b'agent')}
        self.clock = patch.object(bootstrap.time, 'time', return_value=101)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def run_request(self):
        bootstrap.process(self.event, self.ddb, self.cfn, self.s3, self.ec2, self.env)

    def existing(self):
        return {'StackId': 'stack-id', 'StackStatus': 'CREATE_COMPLETE', 'RoleARN': 'role',
                'Parameters': [{'ParameterKey': k, 'ParameterValue': v} for k, v in bootstrap.parameters(self.control, self.env).items()],
                'Tags': [{'Key': 'bootstrap-request-id', 'Value': 'test-request'}]}

    def check_first_generation(self, enabled):
        self.control['propagation_enabled'] = {'BOOL': enabled}
        original = copy.deepcopy(self.control)
        self.run_request()
        self.assertEqual(self.control, original)
        self.assertEqual(self.request['status'], {'S': 'SUBMITTED'})
        call = self.cfn.create_stack.call_args.kwargs
        self.assertEqual(call['StackName'], 'cloud-glider-sandbox-gen-000000')
        self.assertEqual(call['OnFailure'], 'DO_NOTHING')
        self.assertIn('?versionId=template-version', call['TemplateURL'])
        self.assertNotIn('Capabilities', call)

    def test_propagation_disabled_allows_first_generation(self):
        self.check_first_generation(False)

    def test_propagation_enabled_allows_first_generation(self):
        self.check_first_generation(True)

    def test_duplicate_event_does_not_create_again_even_after_stack_deleted(self):
        self.run_request()
        self.run_request()
        self.cfn.create_stack.assert_called_once()

    def test_hold_and_missing_or_initialized_current_fail_closed(self):
        for which in ('hold', 'missing_current', 'initialized'):
            with self.subTest(which=which):
                if which == 'hold':
                    self.hold = {'PK': {'S': 'HOLD'}}
                else:
                    self.hold = {}
                    self.current = {} if which == 'missing_current' else {'status': {'S': 'CURRENT'}}
                with self.assertRaises(RuntimeError):
                    self.run_request()
                self.cfn.create_stack.assert_not_called()

    def test_hold_arriving_at_final_read_prevents_creation(self):
        original = self.ddb.update_item.side_effect
        def update(**kw):
            original(**kw)
            self.hold = {'PK': {'S': 'HOLD'}}
        self.ddb.update_item.side_effect = update
        with self.assertRaisesRegex(RuntimeError, 'Emergency hold'):
            self.run_request()
        self.cfn.create_stack.assert_not_called()
        self.assertEqual(self.request['status'], {'S': 'CREATING'})

    def test_propagation_toggle_during_bootstrap_is_not_a_bootstrap_gate(self):
        original = self.ddb.update_item.side_effect
        def update(**kw):
            original(**kw)
            self.control['propagation_enabled'] = {'BOOL': True}
            self.control['updated_at'] = {'S': 'later'}
        self.ddb.update_item.side_effect = update
        self.run_request()
        self.cfn.create_stack.assert_called_once()

    def test_approved_identity_change_at_final_read_blocks_creation(self):
        original = self.ddb.update_item.side_effect
        def update(**kw):
            original(**kw)
            self.control['template_s3_version_id'] = {'S': 'changed'}
        self.ddb.update_item.side_effect = update
        with self.assertRaisesRegex(RuntimeError, 'Approved control changed'):
            self.run_request()
        self.cfn.create_stack.assert_not_called()

    def test_bad_artifact_prevents_claim_and_creation(self):
        self.s3.get_object.return_value = {'VersionId': 'template-version', 'Body': io.BytesIO(b'wrong')}
        self.s3.get_object.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'digest mismatch'):
            self.run_request()
        self.ddb.update_item.assert_not_called()
        self.cfn.create_stack.assert_not_called()

    def test_expired_request_is_rejected(self):
        self.request['valid_until_epoch'] = {'N': '100'}
        with self.assertRaisesRegex(RuntimeError, 'expired'):
            self.run_request()
        self.cfn.create_stack.assert_not_called()

    def test_existing_instance_prevents_bootstrap(self):
        self.ec2.get_paginator.return_value.paginate.return_value = [{'Reservations': [{'Instances': [{'InstanceId': 'i-existing'}]}]}]
        with self.assertRaisesRegex(RuntimeError, 'existing generation'):
            self.run_request()
        self.cfn.create_stack.assert_not_called()

    def test_submission_timeout_reconciles_matching_stack_without_resubmission(self):
        self.cfn.create_stack.side_effect = TimeoutError('response lost')
        with self.assertRaises(TimeoutError):
            self.run_request()
        self.cfn.describe_stacks.side_effect = None
        self.cfn.describe_stacks.return_value = {'Stacks': [self.existing()]}
        self.run_request()
        self.cfn.create_stack.assert_called_once()
        self.assertEqual(self.request['status'], {'S': 'SUBMITTED'})

    def test_claimed_request_without_stack_requires_manual_recovery(self):
        self.request['status'] = {'S': 'CREATING'}
        with self.assertRaisesRegex(RuntimeError, 'Ambiguous bootstrap'):
            self.run_request()
        self.cfn.create_stack.assert_not_called()

    def test_failed_or_conflicting_stack_is_never_adopted_or_deleted(self):
        self.request['status'] = {'S': 'CREATING'}
        for field, value in [('StackStatus', 'CREATE_FAILED'), ('RoleARN', 'other-role'), ('Parameters', []), ('Tags', [])]:
            stack = self.existing()
            stack[field] = value
            self.cfn.describe_stacks.side_effect = None
            self.cfn.describe_stacks.return_value = {'Stacks': [stack]}
            with self.assertRaises(RuntimeError):
                self.run_request()
        self.cfn.create_stack.assert_not_called()
        self.cfn.delete_stack.assert_not_called()
        self.ddb.update_item.assert_not_called()

    def test_cancellation_and_failed_claim_do_not_create(self):
        self.request['status'] = {'S': 'CANCELLED'}
        self.run_request()
        self.cfn.create_stack.assert_not_called()
        self.request['status'] = {'S': 'REQUESTED'}
        self.ddb.update_item.side_effect = RuntimeError('conditional conflict')
        with self.assertRaisesRegex(RuntimeError, 'conditional conflict'):
            self.run_request()
        self.cfn.create_stack.assert_not_called()

    def test_request_transaction_preserves_control_and_requires_uninitialized_no_hold(self):
        transaction = requester.build_transaction('table', self.control, 'operator', 'id', 100)
        self.assertEqual(len(transaction), 5)
        self.assertTrue(all('ConditionCheck' in entry for entry in transaction[:3]))
        self.assertEqual(transaction[3]['Put']['ConditionExpression'], 'attribute_not_exists(PK)')
        names = transaction[0]['ConditionCheck']['ExpressionAttributeNames'].values()
        self.assertNotIn('propagation_enabled', names)
        self.assertEqual(requester.fingerprint(self.control), bootstrap.fingerprint(self.control))
        self.assertNotIn('expires_at', transaction[3]['Put']['Item'])

    def test_ambiguous_uninitialized_current_is_rejected(self):
        self.current['stack_id'] = {'S': 'unexpected-stack'}
        with self.assertRaisesRegex(RuntimeError, 'not uninitialized'):
            self.run_request()
        self.cfn.create_stack.assert_not_called()

    def test_template_supplies_exact_generation_parameter_set(self):
        import re
        template = (ROOT / 'cfn/bootstrap.yaml').read_text()
        static = template.split('GENERATION_PARAMETERS: !Sub >-\n', 1)[1].split('      Tags:', 1)[0]
        env = {**self.env, 'GENERATION_PARAMETERS': static}
        actual = set(bootstrap.parameters(self.control, env))
        generation = (ROOT / 'cfn/generation.yaml').read_text().split('Parameters:\n', 1)[1].split('Rules:', 1)[0]
        expected = set(re.findall(r'^  ([A-Za-z0-9]+):$', generation, flags=re.MULTILINE))
        self.assertEqual(actual, expected)

    def test_embedded_source_is_current(self):
        subprocess.run([sys.executable, str(ROOT / 'scripts/render_bootstrap_template.py'), '--check'], check=True)


if __name__ == '__main__':
    unittest.main()
