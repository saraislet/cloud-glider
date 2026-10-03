import copy
import json
import unittest
from unittest.mock import Mock, patch

import test_bootstrap as bootstrap_fixture
from test_cleanup import MemoryDdb, controller, operator


class OperatorControlTests(unittest.TestCase):
    def setUp(self):
        fixture = bootstrap_fixture.BootstrapTests()
        fixture.setUp()
        self.env, self.cfn, self.s3, self.ec2 = fixture.env, fixture.cfn, fixture.s3, fixture.ec2
        self.ddb = MemoryDdb()
        self.ddb.items[('CONTROL', 'GLOBAL')] = copy.deepcopy(fixture.control)
        self.ddb.items[('CURRENT', 'GLOBAL')] = copy.deepcopy(fixture.current)
        request = copy.deepcopy(fixture.request)
        request.update(bootstrap_requested={'BOOL': False}, propagation_enabled={'BOOL': False})
        self.ddb.items[('BOOTSTRAP', 'REQUEST')] = request
        self.cfn.get_paginator.side_effect = lambda op: Mock(paginate=lambda **kw:
            [{'StackSummaries': []}] if op == 'list_stacks' else [{'StackResourceSummaries': []}])
        self.ec2.get_paginator.side_effect = lambda op: Mock(paginate=lambda **kw:
            [{'Reservations': []}] if op == 'describe_instances' else
            ([{'Volumes': []}] if op == 'describe_volumes' else [{'Snapshots': []}]))
        self.ec2.describe_addresses.return_value = {'Addresses': []}

    @property
    def control(self):
        return self.ddb.items[('CONTROL', 'GLOBAL')]

    @property
    def request(self):
        return self.ddb.items.get(('BOOTSTRAP', 'REQUEST'), {})

    def submit(self, field):
        self.control[field] = {'BOOL': True}
        with patch.object(controller.time, 'time', return_value=100):
            controller.run_operator(self.ddb, self.cfn, self.s3, self.ec2, self.env, copy.deepcopy(self.control))

    def test_start_bootstraps_and_enables_propagation_without_manual_checks(self):
        self.submit('start_requested')
        self.assertEqual(self.request['propagation_enabled'], {'BOOL': True})
        self.assertEqual(self.request['status'], {'S': 'SUBMITTED'})
        self.assertEqual(self.control['start_requested'], {'BOOL': False})
        self.assertEqual(self.control['operation_status'], {'S': 'SUBMITTED'})
        self.assertIn('Bootstrap submitted', self.control['last_result']['S'])
        self.assertNotIn(('LOCK', 'PROVISIONING'), self.ddb.items)
        self.cfn.create_stack.assert_called_once()

    def submitted_stack(self):
        return {"StackId": "stack-id", "StackName": "cloud-glider-sandbox-gen-000000", "StackStatus": "CREATE_COMPLETE", "RoleARN": "role",
            "Parameters": [{"ParameterKey": key, "ParameterValue": value} for key, value in controller.parameters(self.control, self.env, "1").items()],
            "Tags": [{"Key": key, "Value": value} for key, value in {"project": "cloud-glider", "environment": "sandbox", "purpose": "generation-stack", "bootstrap-request-id": "1"}.items()]}

    def interrupt_after_submission(self, failure):
        parameters = json.loads(self.env["GENERATION_PARAMETERS"])
        parameters["Environment"] = "sandbox"
        self.env["GENERATION_PARAMETERS"] = json.dumps(parameters)
        with patch.object(controller, "release_submission", side_effect=failure):
            with self.assertRaises(type(failure)):
                self.submit("start_requested")
        self.assertEqual(self.request["status"], {"S": "SUBMITTED"})
        self.assertIn(("LOCK", "PROVISIONING"), self.ddb.items)
        self.cfn.describe_stacks.side_effect = None
        self.cfn.describe_stacks.return_value = {"Stacks": [self.submitted_stack()]}

    def test_timeout_after_submission_reconciles_marker_before_success(self):
        self.interrupt_after_submission(KeyboardInterrupt("Lambda terminated"))
        self.ddb.items.pop(("GEN#000000", "RESOURCE#stack-id"))
        controller.run_operator(self.ddb, self.cfn, self.s3, self.ec2, self.env)
        self.assertNotIn(("LOCK", "PROVISIONING"), self.ddb.items)
        self.assertIn(("GEN#000000", "RESOURCE#stack-id"), self.ddb.items)
        self.assertEqual(self.control["operation_status"], {"S": "SUBMITTED"})
        self.assertEqual(self.control["active_command"], {"S": "NONE"})
        self.cfn.create_stack.assert_called_once()
        self.assertEqual(self.cfn.describe_stacks.call_args.kwargs["StackName"], "stack-id")

    def test_release_failure_keeps_accepted_start_for_bookkeeping_retry(self):
        self.interrupt_after_submission(RuntimeError("temporary storage failure"))
        self.assertEqual(self.control["operation_status"], {"S": "NEEDS_ATTENTION"})
        self.assertEqual(self.control["active_command"], {"S": "START"})
        controller.run_operator(self.ddb, self.cfn, self.s3, self.ec2, self.env)
        self.assertNotIn(("LOCK", "PROVISIONING"), self.ddb.items)
        self.assertEqual(self.request["propagation_enabled"], {"BOOL": True})
        self.assertEqual(self.control["operation_status"], {"S": "SUBMITTED"})
        self.cfn.create_stack.assert_called_once()

    def test_ambiguous_submitted_stack_preserves_marker_and_reports_attention(self):
        self.interrupt_after_submission(KeyboardInterrupt())
        self.cfn.describe_stacks.return_value["Stacks"][0]["RoleARN"] = "foreign-role"
        with self.assertRaisesRegex(RuntimeError, "role mismatch"):
            controller.run_operator(self.ddb, self.cfn, self.s3, self.ec2, self.env)
        self.assertIn(("LOCK", "PROVISIONING"), self.ddb.items)
        self.assertEqual(self.control["operation_status"], {"S": "NEEDS_ATTENTION"})
        self.cfn.create_stack.assert_called_once()

    def test_foreign_marker_is_never_released_by_submitted_retry(self):
        self.interrupt_after_submission(KeyboardInterrupt())
        self.ddb.items[("LOCK", "PROVISIONING")]["request_id"] = {"S": "2"}
        with self.assertRaisesRegex(RuntimeError, "marker is ambiguous"):
            controller.run_operator(self.ddb, self.cfn, self.s3, self.ec2, self.env)
        self.assertIn(("LOCK", "PROVISIONING"), self.ddb.items)
        self.cfn.create_stack.assert_called_once()
        self.assertEqual(self.control["operation_status"], {"S": "NEEDS_ATTENTION"})

    def test_first_start_prepares_missing_lifecycle_automatically(self):
        self.ddb.items.pop(('BOOTSTRAP', 'REQUEST'))
        self.submit('start_requested')
        self.assertEqual(self.request['request_id'], {'S': '1'})
        self.assertEqual(self.control['cycle_initialized'], {'BOOL': True})
        self.assertEqual(self.request['status'], {'S': 'SUBMITTED'})

    def test_hold_rejection_is_visible_and_does_not_poison_first_start(self):
        self.ddb.items.pop(('BOOTSTRAP', 'REQUEST'))
        self.ddb.items[('HOLD', 'ACTIVE')] = controller.key('HOLD', 'ACTIVE')
        self.submit('start_requested')
        self.assertIn('emergency hold', self.control['last_result']['S'])
        self.assertEqual(self.control['operation_status'], {'S': 'REJECTED'})
        self.assertEqual(self.control['cycle_initialized'], {'BOOL': False})
        self.cfn.create_stack.assert_not_called()
        self.ddb.items.pop(('HOLD', 'ACTIVE'))
        self.submit('start_requested')
        self.cfn.create_stack.assert_called_once()

    def test_conflicting_switches_are_rejected_with_same_item_message(self):
        self.control['start_requested'] = {'BOOL': True}
        self.submit('cleanup_requested')
        self.assertIn('choose just one', self.control['last_result']['S'])
        self.assertEqual(self.control['operation_status'], {'S': 'REJECTED'})
        self.assertEqual(self.request['bootstrap_requested'], {'BOOL': False})
        for field in ('start_requested', 'stop_requested', 'cleanup_requested'):
            self.assertEqual(self.control[field], {'BOOL': False})
        self.cfn.create_stack.assert_not_called()
        self.cfn.delete_stack.assert_not_called()

    def test_start_during_cleanup_is_rejected_and_not_queued(self):
        self.request.update(cleanup_requested={'BOOL': True}, cleanup_status={'S': 'QUIESCING'},
                            cleanup_started_at={'N': '100'})
        self.control.update(active_command={'S': 'CLEANUP'}, active_request_id={'S': '1'},
            active_command_sequence={'N': '1'}, command_sequence={'N': '1'}, last_result_sequence={'N': '1'},
            operation_status={'S': 'QUIESCING'})
        self.submit('start_requested')
        self.assertIn('Start rejected: cleanup', self.control['last_result']['S'])
        self.assertEqual(self.control['start_requested'], {'BOOL': False})
        self.assertEqual(self.request['request_id'], {'S': '2'})
        self.assertEqual(self.control['operation_status'], {'S': 'COMPLETE'})
        self.assertEqual(self.request['bootstrap_requested'], {'BOOL': False})
        self.cfn.create_stack.assert_not_called()
        self.assertEqual(self.control['active_command'], {'S': 'NONE'})

    def test_cleanup_empty_environment_finishes_and_reset_message_is_visible(self):
        self.ddb.items.pop(('BOOTSTRAP', 'REQUEST'))
        self.submit('cleanup_requested')
        self.assertEqual(self.control['operation_status'], {'S': 'COMPLETE'})
        self.assertIn('Ready for another start', self.control['last_result']['S'])
        self.assertEqual(self.control['cleanup_requested'], {'BOOL': False})
        self.assertEqual(self.request['request_id'], {'S': '2'})
        self.cfn.delete_stack.assert_not_called()
        self.cfn.create_stack.assert_not_called()

    def test_stop_disables_propagation_without_deletion(self):
        self.request['propagation_enabled'] = {'BOOL': True}
        self.submit('stop_requested')
        self.assertEqual(self.request['propagation_enabled'], {'BOOL': False})
        self.assertEqual(self.control['operation_status'], {'S': 'STOPPED'})
        self.assertIn('Running instances remain', self.control['last_result']['S'])
        self.cfn.delete_stack.assert_not_called()

    def test_start_existing_chain_enables_without_second_bootstrap(self):
        self.request.update(status={'S': 'SUBMITTED'}, stack_id={'S': 'existing-stack'})
        self.ddb.items[('CURRENT', 'GLOBAL')].update(status={'S': 'CURRENT'}, request_id={'S': '1'}, stack_id={'S': 'existing-stack'})
        self.submit('start_requested')
        self.assertEqual(self.request['propagation_enabled'], {'BOOL': True})
        self.assertIn('no second bootstrap', self.control['last_result']['S'])
        self.cfn.create_stack.assert_not_called()

    def test_stale_delivery_does_not_consume_a_new_command(self):
        self.control['stop_requested'] = {'BOOL': True}
        event = copy.deepcopy(self.control)
        controller.consume_command(self.ddb, self.env, event)
        self.control['start_requested'] = {'BOOL': True}
        self.assertFalse(controller.consume_command(self.ddb, self.env, event))
        self.assertEqual(self.control['start_requested'], {'BOOL': True})
        self.assertEqual(self.request['bootstrap_requested'], {'BOOL': False})

    def test_operator_fields_do_not_change_approved_control_fingerprint(self):
        digest = controller.fingerprint(self.control)
        self.control.update(start_requested={'BOOL': True}, operation_status={'S': 'STARTING'},
                            last_result={'S': 'new result'}, command_sequence={'N': '20'})
        self.assertEqual(controller.fingerprint(self.control), digest)
        self.assertEqual(self.request['control_sha256'], {'S': digest})

    def test_bootstrap_failure_is_reported_in_control(self):
        self.s3.get_object.side_effect = RuntimeError('artifact read failed')
        with self.assertRaisesRegex(RuntimeError, 'artifact read failed'):
            self.submit('start_requested')
        self.assertEqual(self.control['operation_status'], {'S': 'NEEDS_ATTENTION'})
        self.assertIn('artifact read failed', self.control['last_result']['S'])
        self.cfn.create_stack.assert_not_called()

    def test_malformed_switch_is_rejected_by_reconciliation(self):
        self.control['start_requested'] = {'S': 'true'}
        controller.consume_command(self.ddb, self.env)
        self.assertIn('use Boolean', self.control['last_result']['S'])
        self.assertEqual(self.control['start_requested'], {'BOOL': False})

    def test_ambiguous_bootstrap_is_not_recreated(self):
        self.request['status'] = {'S': 'CREATING'}
        self.submit('start_requested')
        self.assertIn('ambiguous', self.control['last_result']['S'])
        self.cfn.create_stack.assert_not_called()

    def test_missing_previously_initialized_lifecycle_is_rejected(self):
        self.control['cycle_initialized'] = {'BOOL': True}
        self.ddb.items.pop(('BOOTSTRAP', 'REQUEST'))
        self.submit('start_requested')
        self.assertIn('lifecycle record is missing', self.control['last_result']['S'])
        self.cfn.create_stack.assert_not_called()

    def test_python_start_targets_control_without_status_prerequisites(self):
        command = operator.build_control_request('table', self.control, 'start', 'operator', 100)
        self.assertEqual(command['Update']['Key'], controller.key('CONTROL'))
        self.ddb.update_item(**command['Update'])
        self.assertEqual(self.control['start_requested'], {'BOOL': True})
        self.assertNotIn('READY', command['Update']['ConditionExpression'])


if __name__ == '__main__':
    unittest.main()
