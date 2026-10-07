"""Contract checks for the additive human-role migration."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("operator_identities", Path(__file__).resolve().parents[1] / "scripts/render_operator_identities.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class OperatorIdentityTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(account_id="111122223333", region="us-west-2", environment="sandbox",
                           migration_user="GliderManager", migration_group="Gliders",
                           access_permission_set="GliderAccess", security_permission_set="GliderSecurityAccess",
                           sso_region="us-east-1", launch_template_id="lt-0123456789abcdef0")
        self.image = module.document([module.statement("ExistingBuilder", "ec2:DescribeInstances", "*")])
        self.result = module.render(self.config, self.image)

    def actions(self, name):
        return {a for s in self.result["policies"][name]["Statement"]
                for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}

    def test_migration_does_not_replace_existing_entities_or_remove_grants(self):
        resources = self.result["template"]["Resources"]
        self.assertEqual(sum(r["Type"] == "AWS::IAM::Role" for r in resources.values()), 7)
        self.assertNotIn("AWS::IAM::User", {r["Type"] for r in resources.values()})
        self.assertNotIn("AWS::IAM::Group", {r["Type"] for r in resources.values()})
        policy = resources["MigrationAssumeRoles"]["Properties"]["PolicyDocument"]
        self.assertEqual(policy["Statement"][0]["Action"], "sts:AssumeRole")
        self.assertEqual(set(policy["Statement"][0]["Resource"]), set(self.result["role_arns"].values()))
        self.assertTrue(all(r["Properties"]["Path"] == "/cloud-glider/operator/"
                            for r in resources.values() if r["Type"] == "AWS::IAM::Role"))

    def test_no_task_role_can_assume_another_task_role(self):
        for name in module.ROLES:
            self.assertNotIn("sts:AssumeRole", self.actions(name))

    def test_observer_and_operator_have_no_direct_cleanup_or_iam_writes(self):
        for name in ("GliderObserver", "GliderOperator"):
            actions = self.actions(name)
            for forbidden in ("ec2:RunInstances", "ec2:TerminateInstances", "cloudformation:DeleteStack", "iam:PutRolePolicy", "iam:CreatePolicyVersion"):
                self.assertNotIn(forbidden, actions)
        self.assertFalse(any(a.startswith("dynamodb:") and a.endswith(("PutItem", "UpdateItem", "DeleteItem")) for a in self.actions("GliderObserver")))

    def test_cleanup_cannot_mutate_state_or_launch(self):
        actions = self.actions("GliderExceptionalCleanup")
        self.assertNotIn("ec2:RunInstances", actions)
        self.assertNotIn("ec2:CreateTags", actions)
        self.assertNotIn("dynamodb:UpdateItem", actions)
        self.assertNotIn("dynamodb:DeleteItem", actions)
        deletes = next(s for s in self.result["policies"]["GliderExceptionalCleanup"]["Statement"] if s["Sid"] == "DeleteDisposableGliderStacks")
        self.assertTrue(all("-gen-" in a or "smoke-" in a for a in deletes["Resource"]))

    def test_state_recovery_preserves_the_accepted_submission_policy(self):
        s = next(s for s in self.result["policies"]["GliderStateRecovery"]["Statement"] if s["Sid"] == "ReconcileFamilySubmissions")
        self.assertEqual(s["Action"], "dynamodb:UpdateItem")
        self.assertEqual(s["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"], ["GEN#r*"])
        self.assertNotIn("owner", s["Condition"]["ForAllValues:StringEquals"]["dynamodb:Attributes"])
        self.assertIn("token", s["Condition"]["ForAllValues:StringEquals"]["dynamodb:Attributes"])
        self.assertNotIn("ec2:TerminateInstances", self.actions("GliderStateRecovery"))

    def test_security_access_is_not_granted_to_the_routine_permission_set(self):
        sets = self.result["permission_sets"]
        security = self.result["role_arns"]["GliderSecurityAdmin"]
        self.assertNotIn(security, sets["GliderAccess"]["Statement"][0]["Resource"])
        self.assertEqual(sets["GliderSecurityAccess"]["Statement"][0]["Resource"], security)
        for name in module.ROLES:
            trust = self.result["template"]["Resources"][name]["Properties"]["AssumeRolePolicyDocument"]
            pattern = trust["Statement"][1]["Condition"]["ArnLike"]["aws:PrincipalArn"]
            self.assertIn("AWSReservedSSO_GliderSecurityAccess_" if name == "GliderSecurityAdmin" else "AWSReservedSSO_GliderAccess_", pattern)
            self.assertNotIn("sso.amazonaws.com/us-east-1/", pattern)

    def test_image_policy_statements_are_preserved_and_fit_managed_quota(self):
        self.image = module.document([module.statement("Existing" + str(i), "ec2:DescribeInstances", "arn:aws:ec2:us-west-2:111122223333:instance/" + "x" * 100) for i in range(40)])
        original = copy.deepcopy(self.image)
        result = module.render(self.config, self.image)
        managed = [r["Properties"]["PolicyDocument"] for r in result["template"]["Resources"].values() if r["Type"] == "AWS::IAM::ManagedPolicy"]
        self.assertEqual([s for d in managed for s in d["Statement"]], original["Statement"])
        self.assertEqual(self.image, original)
        self.assertTrue(all(len(json.dumps(d, separators=(",", ":"))) <= 6144 for d in managed))

    def test_smoke_console_access_has_exact_instance_and_tag_scope(self):
        smoke = self.result["template"]["Resources"]["GliderImageBuilder"]["Properties"]["Policies"][0]["PolicyDocument"]
        reads = [s for s in smoke["Statement"] if s["Action"] == "ec2:GetConsoleOutput"]
        self.assertEqual(reads, [{
            "Sid": "ReadOwnedImageSmokeConsole", "Effect": "Allow",
            "Action": "ec2:GetConsoleOutput",
            "Resource": "arn:aws:ec2:us-west-2:111122223333:instance/*",
            "Condition": {"StringEquals": {"ec2:ResourceTag/project": "cloud-glider",
                                           "ec2:ResourceTag/purpose": "image-smoke-test"}},
        }])

    def test_invalid_scope_and_same_security_permission_set_fail(self):
        for overrides in ({"account_id": "123456789012"}, {"region": "us-east-1"}, {"environment": "production"}, {"security_permission_set": "GliderAccess"}):
            with self.assertRaises(ValueError):
                module.render({**self.config, **overrides}, self.image)


if __name__ == "__main__":
    unittest.main()
