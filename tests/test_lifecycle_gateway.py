import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
try:
    from cloud_glider.aws_sdk import AwsSdkGateway
except ModuleNotFoundError as exc:
    if exc.name not in ("boto3", "botocore"):
        raise
    AwsSdkGateway = None
from cloud_glider.agent import SafetyViolation, TransientFailure
from test_agent import config, control


@unittest.skipIf(AwsSdkGateway is None, "Install agent/requirements.txt for SDK tests")
class GatewayLifecycleTests(unittest.TestCase):
    def gateway(self):
        gateway = object.__new__(AwsSdkGateway)
        gateway.config = config()
        gateway.region = "us-west-2"
        gateway._call = Mock(return_value={})
        return gateway

    def specification(self):
        return {
            "client_token": "create-1",
            "generation": "000002",
            "stack_name": "cloud-glider-sandbox-gen-000002",
            "role_arn": "role",
            "template_bucket": "bucket",
            "template_key": "generation/template.yaml",
            "template_version_id": "version",
            "parameters": {"Owner": "owner", "RequestId": "1"},
            "tags": {"project": "cloud-glider", "bootstrap-request-id": "1"},
        }

    def test_provisioning_marker_precedes_aws_and_inventory_precedes_marker_release(
        self,
    ):
        gateway = self.gateway()

        def run(*args, **kw):
            return (
                {"StackId": "stack-id"}
                if args[:2] == ("cloudformation", "create_stack")
                else {}
            )

        gateway._call.side_effect = run
        self.assertEqual(gateway.create_stack(self.specification()), "stack-id")
        calls = gateway._call.call_args_list
        self.assertEqual(
            [call.args[:2] for call in calls],
            [
                ("dynamodb", "transact_write_items"),
                ("cloudformation", "create_stack"),
                ("dynamodb", "put_item"),
                ("dynamodb", "delete_item"),
            ],
        )
        transaction = calls[0].kwargs["TransactItems"]
        lifecycle = transaction[0]["ConditionCheck"]
        self.assertIn("propagation_enabled = :yes", lifecycle["ConditionExpression"])
        self.assertIn("cleanup_requested = :no", lifecycle["ConditionExpression"])
        self.assertEqual(
            transaction[3]["Put"]["ConditionExpression"], "attribute_not_exists(PK)"
        )
        self.assertNotIn("expires_at", transaction[3]["Put"]["Item"])
        inventory = calls[2].kwargs["Item"]
        self.assertEqual(inventory["request_id"], {"S": "1"})
        self.assertEqual(inventory["stack_id"], {"S": "stack-id"})

    def test_generation_reads_and_inventory_use_separate_table(self):
        gateway = self.gateway()
        gateway._call.return_value = {"Item": {"request_id": {"S": "1"}}}
        gateway.read_generation_state("000002")
        self.assertEqual(
            gateway._call.call_args.kwargs["TableName"],
            gateway.config.generation_table_name,
        )
        gateway._record_submission(self.specification(), "stack-id")
        self.assertEqual(
            gateway._call.call_args.kwargs["TableName"],
            gateway.config.generation_table_name,
        )
        gateway.read_current()
        self.assertEqual(
            gateway._call.call_args.kwargs["TableName"], gateway.config.state_table_name
        )

    def test_cross_table_handoffs_reuse_latest_audit_and_fence_stale_retry(self):
        import copy
        import dataclasses
        from cloud_glider.aws_sdk import _ddb_item
        from test_cleanup import MemoryDdb

        db = MemoryDdb()
        gateway = self.gateway()
        gateway.config = dataclasses.replace(gateway.config, state_table_name="table")
        approved = control()
        raw = {
            k: v
            for k, v in approved.items()
            if k
            not in (
                "request_id",
                "propagation_enabled",
                "cleanup_requested",
                "cleanup_status",
            )
        }
        raw.update(
            PK="CONTROL", SK="GLOBAL", stop_requested=False, cleanup_requested=False
        )
        db.items[("CONTROL", "GLOBAL")] = _ddb_item(raw)
        db.items[("BOOTSTRAP", "REQUEST")] = _ddb_item(
            {
                "PK": "BOOTSTRAP",
                "SK": "REQUEST",
                "request_id": "1",
                "propagation_enabled": True,
                "cleanup_requested": False,
                "cleanup_status": "IDLE",
            }
        )
        db.items[("LOCK", "PROPAGATION")] = _ddb_item(
            {"PK": "LOCK", "SK": "PROPAGATION", "lease_owner": "owner"}
        )
        current = {
            "generation": "000001",
            "request_id": "1",
            "stack_id": "stack-one",
            "instance_id": "instance-one",
            "status": "CURRENT",
        }
        db.items[("CURRENT", "GLOBAL")] = _ddb_item(
            {"PK": "CURRENT", "SK": "GLOBAL", **current}
        )

        def call(service, operation, **kw):
            assert service == "dynamodb" and operation == "transact_write_items"
            try:
                db.transact_write_items(TransactItems=kw["TransactItems"])
                return {}
            except RuntimeError as exc:
                if str(exc) != "conditional conflict":
                    raise
                return {"_code": "TransactionCanceledException", "_returncode": 1}

        gateway._call.side_effect = call
        first_expected = {
            **current,
            "lease_owner": "owner",
            "control_identity": approved,
        }
        for generation in ("000002", "000003"):
            successor = {
                "generation": generation,
                "request_id": "1",
                "stack_id": "stack-" + generation,
                "instance_id": "instance-" + generation,
                "handoff_token": "token",
                "status": "CURRENT",
                "updated_at": generation,
            }
            db.items[("GEN#" + generation, "STATE")] = _ddb_item(
                {
                    "PK": "GEN#" + generation,
                    "SK": "STATE",
                    **successor,
                    "status": "CANDIDATE",
                }
            )
            expected = {**current, "lease_owner": "owner", "control_identity": approved}
            self.assertTrue(
                gateway.handoff(
                    expected,
                    successor,
                    {"occurred_at": generation, "event_id": generation},
                )
            )
            current = successor
        latest = copy.deepcopy(db.items[("AUDIT#PROPAGATION", "LATEST_HANDOFF")])
        self.assertEqual(latest["event_id"], {"S": "000003"})
        self.assertEqual(
            len([key for key in db.items if key[0] == "AUDIT#PROPAGATION"]), 1
        )
        self.assertFalse(
            gateway.handoff(
                first_expected, current, {"occurred_at": "old", "event_id": "old"}
            )
        )
        self.assertEqual(db.items[("AUDIT#PROPAGATION", "LATEST_HANDOFF")], latest)

    def test_submission_timeout_keeps_marker(self):
        gateway = self.gateway()

        def run(*args, **kw):
            if args[:2] == ("cloudformation", "create_stack"):
                raise TransientFailure("response lost")
            return {}

        gateway._call.side_effect = run
        with self.assertRaises(TransientFailure):
            gateway.create_stack(self.specification())
        self.assertEqual(len(gateway._call.call_args_list), 2)
        self.assertFalse(
            any(
                call.args[:2] == ("dynamodb", "delete_item")
                for call in gateway._call.call_args_list
            )
        )

    def test_failed_marker_claim_prevents_any_aws_creation(self):
        gateway = self.gateway()
        gateway._call.side_effect = TransientFailure("lifecycle changed")
        with self.assertRaises(TransientFailure):
            gateway.create_stack(self.specification())
        gateway._call.assert_called_once()
        self.assertEqual(
            gateway._call.call_args.args[:2], ("dynamodb", "transact_write_items")
        )

    def test_heartbeat_conflict_is_terminal_when_lifecycle_remains_valid(self):
        gateway = self.gateway()
        gateway._call.return_value = {
            "_code": "TransactionCanceledException",
            "_error": "TransactionCanceledException",
            "_cancellation_reasons": [
                {"Code": "None"},
                {"Code": "ConditionalCheckFailed"},
            ],
            "_returncode": 1,
        }
        gateway._get = Mock(
            return_value={
                "request_id": "1",
                "cleanup_requested": False,
                "cleanup_status": "IDLE",
            }
        )
        with self.assertRaises(SafetyViolation) as caught:
            gateway.write_heartbeat(
                {"generation": "000001", "stack_id": "stack", "instance_id": "instance"}
            )
        self.assertEqual(caught.exception.code, "GENERATION_IDENTITY_CONFLICT")

    def test_cleanup_fencing_does_not_report_identity_conflict(self):
        gateway = self.gateway()
        gateway._call.return_value = {
            "_code": "TransactionCanceledException",
            "_error": "TransactionCanceledException",
            "_cancellation_reasons": [
                {"Code": "None"},
                {"Code": "ConditionalCheckFailed"},
            ],
            "_returncode": 1,
        }
        gateway._get = Mock(
            return_value={
                "request_id": "1",
                "cleanup_requested": True,
                "cleanup_status": "QUIESCING",
            }
        )
        with self.assertRaises(TransientFailure):
            gateway.write_heartbeat(
                {"generation": "000001", "stack_id": "stack", "instance_id": "instance"}
            )

    def test_transient_or_unknown_cancellations_do_not_raise_safety_violation(self):
        cases = [[]] + [
            [{"Code": code}, {"Code": "None"}]
            for code in (
                "TransactionConflict",
                "ProvisionedThroughputExceeded",
                "ThrottlingError",
            )
        ]
        for reasons in cases:
            for method in ("heartbeat", "renew"):
                with self.subTest(reasons=reasons, method=method):
                    gateway = self.gateway()
                    gateway._call.return_value = {
                        "_code": "TransactionCanceledException",
                        "_cancellation_reasons": reasons,
                        "_returncode": 1,
                    }
                    gateway._get = Mock()
                    with self.assertRaises(TransientFailure):
                        if method == "heartbeat":
                            gateway.write_heartbeat(
                                {
                                    "generation": "000001",
                                    "stack_id": "stack",
                                    "instance_id": "instance",
                                }
                            )
                        else:
                            gateway.renew_lease("owner", 61)
                    gateway._get.assert_not_called()

    def test_failed_lifecycle_condition_is_not_an_identity_violation(self):
        gateway = self.gateway()
        gateway._call.return_value = {
            "_code": "TransactionCanceledException",
            "_cancellation_reasons": [
                {"Code": "ConditionalCheckFailed"},
                {"Code": "None"},
            ],
            "_returncode": 1,
        }
        gateway._get = Mock(
            return_value={
                "request_id": "1",
                "cleanup_requested": False,
                "cleanup_status": "IDLE",
            }
        )
        with self.assertRaises(TransientFailure):
            gateway.renew_lease("owner", 61)

    def test_confirmed_lease_condition_failure_remains_terminal(self):
        gateway = self.gateway()
        gateway._call.return_value = {
            "_code": "TransactionCanceledException",
            "_cancellation_reasons": [
                {"Code": "None"},
                {"Code": "ConditionalCheckFailed"},
            ],
            "_returncode": 1,
        }
        gateway._get = Mock(
            return_value={
                "request_id": "1",
                "cleanup_requested": False,
                "cleanup_status": "IDLE",
            }
        )
        with self.assertRaises(SafetyViolation) as caught:
            gateway.renew_lease("owner", 61)
        self.assertEqual(caught.exception.code, "LEASE_OWNERSHIP_LOST")

    def test_control_read_uses_only_lifecycle_propagation_switch(self):
        gateway = self.gateway()
        approved = control()
        for field in (
            "request_id",
            "propagation_enabled",
            "cleanup_requested",
            "cleanup_status",
        ):
            approved.pop(field)

        def av(value):
            from cloud_glider.aws_sdk import _av

            return {key: _av(item) for key, item in value.items()}

        lifecycle = {
            "schema_version": "2",
            "request_id": "1",
            "propagation_enabled": False,
            "cleanup_requested": False,
            "cleanup_status": "IDLE",
        }
        gateway._call.return_value = {
            "Responses": [{"Item": av(approved)}, {}, {"Item": av(lifecycle)}]
        }
        merged, hold = gateway.read_control_and_hold()
        self.assertFalse(merged["propagation_enabled"])
        self.assertFalse(hold)
        approved["propagation_enabled"] = True
        gateway._call.return_value["Responses"][0]["Item"] = av(approved)
        with self.assertRaises(SafetyViolation):
            gateway.read_control_and_hold()

    def test_pending_operator_stop_or_cleanup_closes_successor_gate(self):
        from cloud_glider.aws_sdk import _av

        for field in ("stop_requested", "cleanup_requested"):
            with self.subTest(field=field):
                gateway = self.gateway()
                approved = {"PK": "CONTROL", field: True}
                lifecycle = {
                    "schema_version": "2",
                    "request_id": "1",
                    "propagation_enabled": True,
                    "cleanup_requested": False,
                    "cleanup_status": "IDLE",
                }
                gateway._call.return_value = {
                    "Responses": [
                        {"Item": {k: _av(v) for k, v in approved.items()}},
                        {},
                        {"Item": {k: _av(v) for k, v in lifecycle.items()}},
                    ]
                }
                merged, hold = gateway.read_control_and_hold()
                self.assertFalse(merged["propagation_enabled"])
                self.assertFalse(hold)

    def test_stale_cycle_read_stops_worker(self):
        gateway = self.gateway()
        gateway._call.return_value = {
            "Responses": [
                {"Item": {}},
                {},
                {"Item": {"schema_version": {"S": "2"}, "request_id": {"S": "2"}}},
            ]
        }
        with self.assertRaises(TransientFailure):
            gateway.read_control_and_hold()


if __name__ == "__main__":
    unittest.main()
