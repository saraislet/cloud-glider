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


if __name__ == "__main__":
    unittest.main()
