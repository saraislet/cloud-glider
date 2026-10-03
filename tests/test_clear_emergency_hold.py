import argparse
import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "clear_emergency_hold.py"
SPEC = importlib.util.spec_from_file_location("clear_emergency_hold", MODULE_PATH)
clear_hold = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(clear_hold)


class ClearEmergencyHoldTests(unittest.TestCase):
    def test_delete_and_audit_are_atomic(self):
        args = argparse.Namespace(
            table_name="cloud-glider-sandbox-state",
            operator_id="arn:aws:iam::111122223333:role/operator",
            reason="incident reconciled",
            environment="sandbox",
        )
        transaction = clear_hold.build_transaction(
            args, now="2026-09-14T00:00:00.000Z", event_id="event-1"
        )
        self.assertEqual(transaction[0]["Delete"]["Key"]["PK"], {"S": "HOLD"})
        self.assertEqual(transaction[0]["Delete"]["Key"]["SK"], {"S": "ACTIVE"})
        self.assertEqual(
            transaction[0]["Delete"]["ConditionExpression"],
            "attribute_exists(PK) AND attribute_exists(SK)",
        )
        self.assertEqual(
            transaction[1]["Put"]["Item"]["action"], {"S": "CLEAR_EMERGENCY_HOLD"}
        )
    def test_recovery_reuses_latest_key_and_rejects_older_feedback(self):
        args = argparse.Namespace(table_name="table", operator_id="operator", reason="resolved", environment="sandbox")
        before = clear_hold.build_transaction(args, now="2026-10-02T01:00:00.000Z", event_id="old")[1]["Put"]
        after = clear_hold.build_transaction(args, now="2026-10-02T02:00:00.000Z", event_id="new")[1]["Put"]
        self.assertEqual((before["Item"]["PK"], before["Item"]["SK"]), (after["Item"]["PK"], after["Item"]["SK"]))
        self.assertEqual(after["Item"]["PK"], {"S": "AUDIT#RECOVERY"})
        self.assertEqual(after["Item"]["SK"], {"S": "LATEST"})
        self.assertIn("occurred_at <= :now", after["ConditionExpression"])



if __name__ == "__main__":
    unittest.main()
