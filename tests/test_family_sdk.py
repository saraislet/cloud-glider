import importlib.util
import base64
import json
import unittest
from unittest.mock import Mock, patch

from test_family_daemon import family_config
from cloud_glider.daemon import SafetyViolation, TransientFailure

if importlib.util.find_spec("boto3"):
    import boto3
    from botocore.validate import validate_parameters
    from cloud_glider.family_sdk import FamilySdkGateway
    from cloud_glider.aws_sdk import _ddb_item
else:
    boto3 = None


@unittest.skipIf(boto3 is None, "SDK dependencies required")
class FamilySdkTests(unittest.TestCase):
    def setUp(self):
        self.cfg = family_config()
        with patch("cloud_glider.aws_sdk._imds", side_effect=[self.cfg.instance_id,
                   json.dumps({"region": "us-west-2", "accountId": "111122223333"})]):
            self.gateway = FamilySdkGateway(self.cfg, session=boto3.Session(
                aws_access_key_id="testing", aws_secret_access_key="testing"))
        self.addCleanup(self.gateway.close)
        self.calls = []
        self.response = {}

        def call(service, operation, **kw):
            kw.pop("allow_failure", None)
            client = self.gateway._clients[service]
            model = client.meta.service_model.operation_model(client.meta.method_to_api_mapping[operation])
            validate_parameters(kw, model.input_shape)
            self.calls.append((service, operation, kw))
            return self.response
        self.gateway._call = call

    def test_control_read_fetches_only_dynamic_controls_and_family_cancellation(self):
        response = [{"stop_requested": False, "cleanup_requested": False}, {},
            {"request_id": "1", "cycle_configuration_sha256": self.gateway.configuration_sha256,
             "propagation_enabled": True, "cleanup_requested": False}, {}]
        self.response = {"Responses": [{"Item": _ddb_item(r)} for r in response]}
        self.assertFalse(self.gateway.poll_stopped())
        keys = [r["Get"]["Key"] for r in self.calls[0][2]["TransactItems"]]
        self.assertEqual(keys[-1]["SK"], {"S": "STOP"})
        self.assertEqual(len(keys), 4)  # Root has no parent stop record.

    def test_launch_transaction_has_cycle_cleanup_fence_but_no_global_stop_permit(self):
        self.gateway.transact([self.gateway.owner_check()])
        transaction = self.calls[-1][2]["TransactItems"]
        condition = transaction[0]["ConditionCheck"]["ConditionExpression"]
        self.assertIn("cycle_configuration_sha256", condition)
        self.assertIn("cleanup_requested", condition)
        self.assertNotIn("propagation_enabled", condition)
        self.assertNotIn("CONTROL", str(transaction))
        self.assertNotIn("LOCK", str(transaction))

    def test_handoff_checks_fresh_controls_and_full_readiness_snapshot(self):
        state = {"request_id": "1", "ready_at": 100, "instance_id": "i-child",
                 "configuration_sha256": self.gateway.configuration_sha256, "continuation": "BOUNDARY"}
        with patch("cloud_glider.family_sdk.time.time", return_value=100):
            check = self.gateway.readiness_check("r00", state)
        self.assertIn("continuation", check["ConditionCheck"]["ExpressionAttributeNames"].values())
        self.gateway.transact([check], final=True)
        self.assertIn("HOLD", str(self.calls[-1]))
        self.assertIn("stop_requested", str(self.calls[-1]))

    def test_cancellation_and_settlement_remain_cycle_fenced_during_cleanup(self):
        self.gateway.stop_node("r0")
        check = self.calls[-1][2]["TransactItems"][0]["ConditionCheck"]
        self.assertNotIn("cleanup_requested", check["ConditionExpression"])
        self.assertIn("request_id", check["ConditionExpression"])
        self.assertIn("cycle_configuration_sha256", check["ConditionExpression"])

    def test_ec2_request_uses_exact_template_token_and_inherited_user_data(self):
        from cloud_glider.inherited import specification
        spec = specification(self.cfg, "r0", self.cfg.instance_id, "handoff-r0")
        self.response = {"_code": "DryRunOperation"}
        self.gateway.dry_run_child(spec)
        request = self.calls[-1][2]
        self.assertEqual(request["LaunchTemplate"]["Version"], "1")
        self.assertEqual(request["MaxCount"], 1)
        self.assertIn("UserData", request)
        self.assertTrue(request["DryRun"])

    def test_botocore_encodes_user_data_exactly_once(self):
        from cloud_glider.inherited import specification
        request = self.gateway.child_request(specification(self.cfg, "r0", self.cfg.instance_id, "handoff-r0"))
        self.assertTrue(request["UserData"].startswith("#!/bin/bash"))
        client = self.gateway._clients["ec2"]
        model = client.meta.service_model.operation_model("RunInstances")
        client.meta.events.emit("before-parameter-build.ec2.RunInstances", params=request, model=model, context={})
        self.assertTrue(base64.b64decode(request["UserData"]).decode().startswith("#!/bin/bash"))

    def test_lost_launch_response_never_blindly_resubmits(self):
        from cloud_glider.inherited import specification
        spec = specification(self.cfg, "r0", self.cfg.instance_id, "handoff-r0")
        self.gateway.get_node_item = Mock(return_value={"token": spec["client_token"], "request_id": "1"})
        with self.assertRaises(TransientFailure):
            self.gateway.launch_child(spec)
        self.assertFalse(any(op == "run_instances" for _, op, _ in self.calls))

    def test_conflicting_intent_does_not_launch_or_write(self):
        from cloud_glider.inherited import specification
        spec = specification(self.cfg, "r0", self.cfg.instance_id, "handoff-r0")
        self.gateway.get_node_item = Mock(return_value={"token": "other", "request_id": "1"})
        with self.assertRaises(SafetyViolation): self.gateway.launch_child(spec)
        self.assertEqual(self.calls, [])

    def test_initial_node_transaction_does_not_claim_global_current(self):
        self.gateway.read_node = Mock(return_value={})
        self.gateway.describe_instance = Mock(return_value={"ClientToken": "seed-cfn-token"})
        self.gateway.initialize_node()
        transaction = self.calls[-1][2]["TransactItems"]
        self.assertNotIn("CURRENT", str(transaction))
        self.assertIn("GEN#r", str(transaction))

    def proof(self, path="r0"):
        return {"request_id": "1", "instance_id": "i-child", "node_path": path,
            "generation": f"{len(path)-1:06d}", "configuration_sha256": self.gateway.configuration_sha256,
            "predecessor_instance_id": self.cfg.instance_id, "handoff_token": "handoff-" + path,
            "daemon_live": True, "ready_at": 100,
            "continuation": "BOUNDARY" if len(path) == 3 else "DRY_RUN_PASSED"}

    def test_ambiguous_or_stale_child_proof_never_changes_ownership(self):
        for change in ({"ready_at": 84}, {"ready_at": 101}, {"request_id": "2"},
                       {"configuration_sha256": "bad"}, {"daemon_live": False},
                       {"generation": "000002"}, {"continuation": "BOUNDARY"}):
            state = {**self.proof(), **change}
            self.gateway.get_node_item = Mock(return_value=state)
            self.assertIsNone(self.gateway.eligible("r0", "i-child", 100))
        self.assertEqual(self.calls, [])

    def test_running_status_without_functional_evidence_is_not_healthy(self):
        self.gateway.get_node_item = Mock(return_value={})
        self.gateway.describe_instance = Mock(return_value={"State": {"Name": "running"}})
        self.assertIsNone(self.gateway.eligible("r0", "i-child", 100))
        self.gateway.describe_instance.assert_not_called()

    def test_error_evidence_cannot_be_used_for_handoff(self):
        self.gateway.get_node_item = Mock(return_value={**self.proof(), "error_code": "READINESS_ERROR"})
        self.assertIsNone(self.gateway.eligible("r0", "i-child", 100))
        self.assertEqual(self.calls, [])

    def test_family_retirement_checks_both_children_and_durable_exact_plan(self):
        self.gateway.read_node = Mock(side_effect=lambda path: {"instance_id": "i-" + path, "owner": "i-" + path})
        self.gateway.eligible = Mock(side_effect=lambda path, instance, now: {**self.proof(path), "instance_id": instance})
        with patch("cloud_glider.family_sdk.time.time", return_value=100):
            self.assertTrue(self.gateway.authorize_retirement(("r0", "r1"), 100))
        request = self.calls[-1][2]
        self.assertIn("retirement_children", str(request))
        for path in ("GEN#r0", "GEN#r1"):
            self.assertIn(path, str(request))
        with self.assertRaises(SafetyViolation):
            self.gateway.authorize_retirement(("r0",), 100)

    def test_retirement_retry_revalidates_before_termination(self):
        self.gateway.verify_instance = Mock()
        self.gateway.describe_instance = Mock(return_value={})
        self.gateway.read_node = Mock(return_value={"status": "RETIRING", "retirement_children": ["r0", "r1"]})
        self.gateway.authorize_retirement = Mock(return_value=False)
        with self.assertRaises(TransientFailure): self.gateway.retire_self()
        self.assertFalse(any(op == "terminate_instances" for _, op, _ in self.calls))

    def test_single_launch_can_overlap_accepted_retirement_but_own_retirement_waits(self):
        for state, launch_ready, terminated in (("running", False, False),
                ("shutting-down", True, False), ("terminated", True, True)):
            self.gateway.parent_state = Mock(return_value=state)
            self.assertEqual(self.gateway.parent_launch_ready(), launch_ready)
            self.assertEqual(self.gateway.parent_terminated(), terminated)

    def test_child_inventory_and_alarm_settle_before_cleanup_can_proceed(self):
        from cloud_glider.inherited import specification
        spec = specification(self.cfg, "r0", self.cfg.instance_id, "handoff-r0")
        order = []
        self.gateway.transact = Mock(side_effect=lambda *a, **kw: order.append(("transaction", a)))
        self.gateway.ensure_status_alarm = Mock(side_effect=lambda *a: order.append(("alarm", a)))
        self.gateway.record_child(spec, {"InstanceId": "i-child"})
        self.assertEqual([action for action, _ in order], ["transaction", "alarm", "transaction"])
        self.assertIn("settled", str(order[-1]))

    def test_terminated_parent_requires_durable_retirement_receipt(self):
        import dataclasses
        from cloud_glider.inherited import specification
        self.gateway.config = dataclasses.replace(self.cfg, node_path="r0", generation="000001", predecessor_instance_id="i-parent")
        spec = specification(self.gateway.config, "r", "NONE", "OPERATOR_BOOTSTRAP")
        parent = {"InstanceId": "i-parent", "State": {"Name": "terminated"}, "Tags": spec["tags"]}
        receipt = {"request_id": self.cfg.request_id, "instance_id": "i-parent", "owner": "i-parent", "configuration_sha256": self.gateway.configuration_sha256, "status": "RETIRING", "retirement_children": ["r0", "r1"]}
        self.gateway.describe_instance = Mock(return_value=parent)
        self.gateway.read_node = Mock(return_value=receipt)
        self.assertTrue(self.gateway.parent_terminated())
        for field, value in (("request_id", "other"), ("owner", "other"), ("status", "OWNER"), ("retirement_children", ["r0"])):
            self.gateway.read_node.return_value = {**receipt, field: value}
            with self.assertRaises(SafetyViolation): self.gateway.parent_terminated()

    def test_family_hold_is_async_and_requires_acceptance(self):
        self.response = {"StatusCode": 202}
        self.gateway.invoke_hold("000000", "TEST", "id")
        self.assertEqual(self.calls[-1][2]["InvocationType"], "Event")
        self.response = {"StatusCode": 500}
        with self.assertRaises(TransientFailure): self.gateway.invoke_hold("000000", "TEST", "id")
