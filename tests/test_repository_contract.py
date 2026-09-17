import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_repository.py"
SPEC = importlib.util.spec_from_file_location("validate_repository", MODULE_PATH)
validate_repository = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(validate_repository)


class RepositoryContractTests(unittest.TestCase):
    def test_dependency_free_validator(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_repository.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_validator_rejects_merge_conflict_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conflicted = root / "conflicted.md"
            conflicted.write_text(
                "before\n"
                + "<" * 7
                + " HEAD\nours\n"
                + "=" * 7
                + "\ntheirs\n"
                + ">" * 7
                + " branch\nafter\n",
                encoding="utf-8",
            )

            self.assertEqual(
                validate_repository.find_conflict_markers(root),
                [(conflicted, 2), (conflicted, 4), (conflicted, 6)],
            )
            with mock.patch.object(validate_repository, "ROOT", root):
                with self.assertRaisesRegex(SystemExit, "conflicted.md:2"):
                    validate_repository.main()

    def test_generation_uses_ephemeral_public_ipv4(self):
        generation = (ROOT / "cfn" / "generation.yaml").read_text(encoding="utf-8")
        self.assertIn("AssociatePublicIpAddress: true", generation)
        self.assertIn("DeleteOnTermination: true", generation)
        self.assertNotIn("AssociatePublicIpAddress: false", generation)

    def test_agent_cannot_mutate_networking_directly(self):
        foundation = (ROOT / "cfn" / "foundation.yaml").read_text(encoding="utf-8")
        self.assertIn("DenyDirectNetworkMutation", foundation)
        for action in (
            "ec2:AllocateAddress",
            "ec2:AssociateAddress",
            "ec2:CreateNetworkInterface",
            "ec2:DeleteNetworkInterface",
            "ec2:DisassociateAddress",
            "ec2:ModifyNetworkInterfaceAttribute",
            "ec2:ReleaseAddress",
        ):
            self.assertIn(action, foundation)

    def test_public_ip_launch_is_projected_to_network_audit(self):
        foundation = (ROOT / "cfn" / "foundation.yaml").read_text(encoding="utf-8")
        self.assertIn("RunInstancesNetworkAuditLog", foundation)
        self.assertIn("Arn: !GetAtt NetworkAuditLogGroup.Arn", foundation)

    def test_cost_defaults_cover_public_ipv4_baseline(self):
        defaults = json.loads(
            (ROOT / "config" / "runtime-defaults.json").read_text(encoding="utf-8")
        )
        self.assertEqual(defaults["monthly_budget_usd"], 20)
        self.assertEqual(defaults["actual_budget_alerts_usd"], [10, 15, 20])
        self.assertEqual(defaults["forecast_budget_alert_usd"], 20)

    def test_alarm_templates_cover_billing_and_operations(self):
        billing = (ROOT / "cfn" / "billing-alerts.yaml").read_text(encoding="utf-8")
        foundation = (ROOT / "cfn" / "foundation.yaml").read_text(encoding="utf-8")
        generation = (ROOT / "cfn" / "generation.yaml").read_text(encoding="utf-8")

        self.assertIn("AWS/Billing", billing)
        self.assertIn("us-east-1", billing)
        self.assertIn("OperationalAlertsTopic", foundation)
        self.assertIn("EmergencyHoldErrorsAlarm", foundation)
        self.assertIn("StateTableWriteThrottleAlarm", foundation)
        self.assertIn("StateTableSystemErrorsAlarm", foundation)
        self.assertIn("GenerationStatusCheckAlarm", generation)
        self.assertIn("TreatMissingData: missing", generation)

    def test_network_template_is_bounded_and_no_ingress(self):
        network = (ROOT / "cfn" / "network.yaml").read_text(encoding="utf-8")

        self.assertIn("SecurityGroupIngress: []", network)
        self.assertIn("FromPort: 443", network)
        self.assertIn("ToPort: 443", network)
        self.assertIn("MapPublicIpOnLaunch: false", network)
        self.assertNotIn("MapPublicIpOnLaunch: true", network)
        self.assertNotIn("AWS::EC2::NatGateway", network)

    def test_agent_hold_access_is_condition_check_only(self):
        foundation = (ROOT / "cfn" / "foundation.yaml").read_text(encoding="utf-8")
        statement = foundation.split("- Sid: AssertNoEmergencyHoldAtHandoff", 1)[1].split(
            "- Sid: ReleaseOnlyPropagationLease", 1
        )[0]
        self.assertIn("Action: dynamodb:ConditionCheckItem", statement)
        self.assertIn("- HOLD", statement)
        self.assertNotIn("dynamodb:PutItem", statement)
        self.assertNotIn("dynamodb:UpdateItem", statement)
        self.assertNotIn("dynamodb:DeleteItem", statement)


if __name__ == "__main__":
    unittest.main()
