import copy
import json
import unittest
from unittest.mock import patch

import test_cleanup
import test_operator_controls


class FamilyBootstrapTests(unittest.TestCase):
    def fixture(self):
        fixture = test_operator_controls.OperatorControlTests()
        fixture.setUp()
        fixture.control.update(propagation_backend={"S": "ec2"}, configuration_inheritance={"BOOL": True},
            binary_fanout_enabled={"BOOL": True}, approved_account_id={"S": "111122223333"},
            launch_template_id={"S": "lt-" + "a" * 17}, launch_template_version={"S": "1"},
            launch_template_sha256={"S": "c" * 64}, concurrency_model={"S": "EC2_DRY_RUN_THEN_RETIRE"})
        return fixture

    def test_start_pins_configuration_before_bootstrap_submission(self):
        fixture = self.fixture()
        fixture.control["start_requested"] = {"BOOL": True}
        with patch.object(test_cleanup.controller.time, "time", return_value=100):
            self.assertTrue(test_cleanup.controller.consume_command(fixture.ddb, fixture.env))
        request = fixture.request
        self.assertEqual(request["cycle_control"]["M"]["binary_fanout_enabled"], {"BOOL": True})
        params = test_cleanup.controller.parameters(fixture.control, fixture.env, "1", request)
        envelope = json.loads(params["CycleConfiguration"])
        self.assertTrue(envelope["settings"]["binary_fanout_enabled"])
        self.assertEqual(envelope["settings"]["audit_table_name"], fixture.control["audit_table_name"]["S"])
        self.assertEqual(request["cycle_configuration_sha256"]["S"], envelope["sha256"])
        self.assertLessEqual(len(params["CycleConfiguration"]), 4096)
        fixture.control["binary_fanout_enabled"] = {"BOOL": False}
        fixture.control["max_generation"] = {"N": "9"}
        self.assertEqual(test_cleanup.controller.parameters(fixture.control, fixture.env, "1", request), params)

    def test_binary_requires_inherited_ec2_and_valid_depth(self):
        fixture = self.fixture()
        for name, value in (("max_generation", {"N": "10"}), ("binary_fanout_enabled", {"S": "true"})):
            changed = copy.deepcopy(fixture.control)
            changed[name] = value
            with self.assertRaises(RuntimeError):
                test_cleanup.controller.inherited_envelope(changed, "1", True)

    def test_operator_start_delivers_pinned_envelope_to_seed_parameters(self):
        fixture = self.fixture()
        with patch.object(test_cleanup.controller, "verify_ec2_template"):
            fixture.submit('start_requested')
        params = {p['ParameterKey']: p['ParameterValue'] for p in fixture.cfn.create_stack.call_args.kwargs['Parameters']}
        self.assertEqual(params['ConfigurationSha256'], fixture.request['cycle_configuration_sha256']['S'])
        self.assertTrue(json.loads(params['CycleConfiguration'])['settings']['initial_propagation_enabled'])

    def test_cleanup_waits_for_unknown_family_submission_before_deletion(self):
        fixture = test_cleanup.CleanupTests()
        fixture.setUp()
        fixture.ddb.items[("GEN#r0", "SUBMISSION")] = {
            **test_cleanup.controller.key("GEN#r0", "SUBMISSION"),
            "request_id": {"S": "1"}, "settled": {"BOOL": False}}
        fixture.step()
        self.assertEqual(fixture.request["cleanup_status"], {"S": "QUIESCING"})
        fixture.cfn.delete_stack.assert_not_called()
        fixture.ec2.terminate_instances.assert_not_called()

    def test_cleanup_reset_removes_old_configuration_pins(self):
        fixture = test_cleanup.CleanupTests()
        fixture.setUp()
        fixture.request.update(cycle_configuration_sha256={"S": "old"}, cycle_control={"M": {}},
                               initial_propagation_enabled={"BOOL": True})
        fixture.step()
        fixture.stacks['stack-one']['StackStatus'] = 'DELETE_COMPLETE'
        fixture.step()
        self.assertEqual(fixture.request["cleanup_status"], {"S": "COMPLETE"})
        self.assertNotIn("cycle_control", fixture.request)
        self.assertNotIn("cycle_configuration_sha256", fixture.request)
