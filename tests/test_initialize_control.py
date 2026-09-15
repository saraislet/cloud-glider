import argparse
import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "initialize_control.py"
SPEC = importlib.util.spec_from_file_location("initialize_control", MODULE_PATH)
initialize_control = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(initialize_control)


class InitializeControlTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "table_name": "cloud-glider-sandbox-state",
            "operator_id": "arn:aws:iam::111122223333:role/operator",
            "template_version": "v1",
            "bootstrap_version": "bootstrap-v1",
            "template_bucket": "cloud-glider-artifacts",
            "template_key": "generation/template.yaml",
            "template_s3_version_id": "version-1",
            "template_sha256": "a" * 64,
            "template_build_id": "abcdef123456",
            "environment": "sandbox",
            "max_generation": 2,
            "max_live_generations": 3,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_initial_state_is_disabled_and_bounded(self):
        transaction = initialize_control.build_transaction(
            self.args(), now="2026-08-24T00:00:00.000Z", event_id="event-1"
        )
        control = transaction[0]["Put"]["Item"]
        self.assertEqual(control["propagation_enabled"], {"BOOL": False})
        self.assertNotIn("emergency_hold", control)
        self.assertEqual(control["max_generation"], {"N": "2"})
        self.assertEqual(control["max_live_generations"], {"N": "3"})
        self.assertEqual(control["approved_region"], {"S": "us-west-2"})
        self.assertEqual(control["approved_architecture"], {"S": "arm64"})
        self.assertEqual(control["approved_instance_types"]["L"], [{"S": "t4g.micro"}])
        self.assertEqual(control["readiness_required_heartbeats"], {"N": "2"})
        self.assertEqual(control["heartbeat_interval_seconds"], {"N": "5"})
        self.assertEqual(control["readiness_poll_seconds"], {"N": "2"})
        self.assertEqual(control["readiness_timeout_seconds"], {"N": "600"})
        self.assertEqual(control["template_s3_bucket"], {"S": "cloud-glider-artifacts"})
        self.assertEqual(control["template_s3_key"], {"S": "generation/template.yaml"})
        self.assertEqual(control["template_build_id"], {"S": "abcdef123456"})
        self.assertEqual(control["desired_bootstrap_version"], {"S": "bootstrap-v1"})

    def test_initialization_does_not_create_a_hold(self):
        transaction = initialize_control.build_transaction(
            self.args(), now="2026-08-24T00:00:00.000Z", event_id="event-1"
        )
        keys = [operation["Put"]["Item"]["PK"]["S"] for operation in transaction]
        self.assertNotIn("HOLD", keys)

    def test_transaction_refuses_overwrite(self):
        transaction = initialize_control.build_transaction(
            self.args(), now="2026-08-24T00:00:00.000Z", event_id="event-1"
        )
        for operation in transaction:
            self.assertEqual(
                operation["Put"]["ConditionExpression"],
                "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )

    def test_audit_record_is_transactional(self):
        transaction = initialize_control.build_transaction(
            self.args(), now="2026-08-24T00:00:00.000Z", event_id="event-1"
        )
        audit = transaction[2]["Put"]["Item"]
        self.assertEqual(audit["PK"], {"S": "AUDIT#PROPAGATION"})
        self.assertEqual(audit["actor"], {"S": self.args().operator_id})
        self.assertEqual(audit["action"], {"S": "INITIALIZE_CONTROL_STATE"})

    def test_rejects_changed_concurrency_ceiling(self):
        with self.assertRaises(ValueError):
            initialize_control.build_transaction(
                self.args(max_live_generations=4),
                now="2026-08-24T00:00:00.000Z",
                event_id="event-1",
            )

    def test_rejects_invalid_digest(self):
        with self.assertRaises(ValueError):
            initialize_control.build_transaction(
                self.args(template_sha256="not-a-digest"),
                now="2026-08-24T00:00:00.000Z",
                event_id="event-1",
            )


if __name__ == "__main__":
    unittest.main()
