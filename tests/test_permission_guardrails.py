"""Request-fixture regression tests, not an AWS authorization simulator.

Only the policy constructs used by these candidates are evaluated. These tests
cannot establish AWS service condition-key support or replace sandbox API tests.
"""
import fnmatch
import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("render_guardrails", ROOT / "scripts/render_guardrails.py")
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)
ACCOUNT = "111122223333"
ORG = "o-a1b2c3d4e5"
PREFIX = "cloud-glider-sandbox"
ROLE = "arn:aws:iam::" + ACCOUNT + ":role/"
AGENT, GENERATION, HOLD = [ROLE + PREFIX + suffix for suffix in ["-agent", "-generation-cfn", "-emergency-hold"]]
BOOTSTRAP = ROLE + PREFIX + "-bootstrap-BootstrapRole-abc"
ADMIN, RECOVERY, FOUNDATION = [ROLE + name for name in ["BoundaryAdmin", "Recovery", PREFIX + "-foundation-cfn"]]
TABLE = "arn:aws:dynamodb:us-west-2:" + ACCOUNT + ":table/" + PREFIX + "-state"
BUCKET = "arn:aws:s3:::" + PREFIX + "-" + ACCOUNT + "-us-west-2-artifacts"
STACK = "arn:aws:cloudformation:us-west-2:" + ACCOUNT + ":stack/" + PREFIX + "-gen-000001/uuid"
URL = "https://" + BUCKET.split(":::")[1] + ".s3.us-west-2.amazonaws.com/generation/generation.yaml?versionId=approved"
CONFIG = dict(account_id=ACCOUNT, organization_id=ORG, environment="sandbox", boundary_admin_role_arn=ADMIN, recovery_role_arn=RECOVERY, foundation_role_arn=FOUNDATION)
VALUES = {"AWS::AccountId": ACCOUNT, "AWS::Region": "us-west-2", "AWS::Partition": "aws", "Environment": "sandbox", "ApprovedGenerationTemplateUrl": URL, "AllowedImageId": "ami-approved", "AllowedSubnetId": "subnet-approved", "AllowedSecurityGroupId": "sg-approved"}


def array(value):
    return value if isinstance(value, list) else [value]


def resolve(value, values):
    if isinstance(value, dict):
        if set(value) == {"Ref"}:
            return values[value["Ref"]]
        if set(value) == {"Fn::Sub"}:
            return re.sub(r"\$\{([^}]+)\}", lambda m: values[m[1]], value["Fn::Sub"])
        return {key: resolve(child, values) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve(child, values) for child in value]
    return value


def condition_matches(operator, expected, actual):
    if operator == "Null":
        return (actual is None) == (str(expected).lower() == "true")
    if operator.endswith("IfExists"):
        if actual is None:
            return True
        operator = operator[:-8]
    if operator.startswith("ForAnyValue:"):
        return any(condition_matches(operator.split(":", 1)[1], expected, item) for item in ([] if actual is None else array(actual)))
    if operator.startswith("ForAllValues:"):
        return all(condition_matches(operator.split(":", 1)[1], expected, item) for item in ([] if actual is None else array(actual)))
    if operator in {"StringNotEquals", "ArnNotEquals"}:
        return actual not in array(expected)
    if actual is None:
        return False
    if operator in {"StringEquals", "ArnEquals"}:
        return actual in array(expected)
    if operator in {"StringLike", "ArnLike"}:
        return any(fnmatch.fnmatchcase(actual, pattern) for pattern in array(expected))
    if operator == "Bool":
        return str(actual).lower() == str(expected).lower()
    raise AssertionError("Unsupported test condition: " + operator)


def decision(policy, action, resource, context=None):
    context = context or {}
    allowed = False
    for s in policy["Statement"]:
        match = any(fnmatch.fnmatchcase(action.lower(), a.lower()) for a in array(s.get("Action", s.get("NotAction"))))
        if (not match if "Action" in s else match):
            continue
        match = any(fnmatch.fnmatchcase(resource, r) for r in array(s.get("Resource", s.get("NotResource"))))
        if (not match if "Resource" in s else match):
            continue
        if not all(condition_matches(op, wanted, context.get(key)) for op, conditions in s.get("Condition", {}).items() for key, wanted in conditions.items()):
            continue
        if s["Effect"] == "Deny":
            return "explicitDeny"
        allowed = True
    return "allowed" if allowed else "implicitDeny"


class GuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = json.loads((ROOT / "cfn/permission-boundaries.json").read_text())
        cls.boundaries = {name: resolve(resource["Properties"]["PolicyDocument"], VALUES) for name, resource in cls.template["Resources"].items()}
        cls.organization = {name: json.loads(text) for name, text in renderer.render_policies(CONFIG).items()}

    def boundary(self, name, action, resource, context=None):
        return decision(self.boundaries[name + "Boundary"], action, resource, context)

    def scp(self, action, resource, principal=AGENT, **context):
        ctx = {"aws:PrincipalArn": principal, "aws:RequestedRegion": "us-west-2", **context}
        return any(decision(p, action, resource, ctx) == "explicitDeny" for name, p in self.organization.items() if name.startswith("scp-"))

    def test_runtime_templates_require_the_corresponding_boundary(self):
        for filename, roles in [("foundation.yaml", {"AgentRole": "agent", "GenerationServiceRole": "generation", "EmergencyHoldFunctionRole": "hold"}), ("bootstrap.yaml", {"BootstrapRole": "bootstrap"})]:
            text = (ROOT / "cfn" / filename).read_text()
            for logical_id, suffix in roles.items():
                block = text.split("  " + logical_id + ":\n", 1)[1].split("      AssumeRolePolicyDocument:", 1)[0]
                expected = "PermissionsBoundary: !Sub 'arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/cloud-glider-${Environment}-" + suffix + "-boundary'"
                self.assertIn(expected, block)
            self.assertIn("cloudformation:TemplateUrl: !Ref ApprovedGenerationTemplateUrl", text)
        foundation = (ROOT / "cfn/foundation.yaml").read_text()
        self.assertNotIn(".Arn}:log-stream:", foundation)
        hold = foundation.split("- Sid: AssertNoEmergencyHoldAtHandoff", 1)[1].split("- Sid: ReleaseOnlyPropagationLease", 1)[0]
        self.assertIn("- CONTROL", hold)
        self.assertIn("- HOLD", hold)
        self.assertNotIn("dynamodb:UpdateItem", hold)
        self.assertNotIn("dynamodb:PutItem", hold)

    def test_creation_requires_exact_release_and_service_role(self):
        for boundary, stack in [("Agent", STACK), ("Bootstrap", STACK.replace("000001", "000000"))]:
            for url, expected in [(URL, "allowed"), (URL + "-different", "explicitDeny"), (None, "explicitDeny")]:
                ctx = {"cloudformation:RoleARN": GENERATION}
                if url is not None:
                    ctx["cloudformation:TemplateUrl"] = url
                with self.subTest(boundary=boundary, url=url):
                    self.assertEqual(self.boundary(boundary, "cloudformation:CreateStack", stack, ctx), expected)
            ctx = {"cloudformation:TemplateUrl": URL, "cloudformation:RoleARN": FOUNDATION}
            self.assertNotEqual(self.boundary(boundary, "cloudformation:CreateStack", stack, ctx), "allowed")
        self.assertNotEqual(self.boundary("Bootstrap", "cloudformation:CreateStack", STACK, {"cloudformation:RoleARN": GENERATION, "cloudformation:TemplateUrl": URL}), "allowed")

    def test_preflight_requires_release_but_retirement_does_not(self):
        ctx = {"cloudformation:RoleARN": GENERATION, "cloudformation:TemplateUrl": URL}
        self.assertEqual(self.boundary("Agent", "cloudformation:CreateChangeSet", STACK, ctx), "allowed")
        self.assertEqual(self.boundary("Agent", "cloudformation:DeleteStack", STACK, {"cloudformation:RoleARN": GENERATION}), "allowed")
        self.assertEqual(self.boundary("Agent", "cloudformation:DeleteChangeSet", STACK), "allowed")
        self.assertNotEqual(self.boundary("Agent", "cloudformation:DeleteStack", STACK.replace("-gen-000001", "-foundation"), {"cloudformation:RoleARN": GENERATION}), "allowed")

    def test_empty_release_disables_creation_without_blocking_retirement(self):
        p = resolve(self.template["Resources"]["AgentBoundary"]["Properties"]["PolicyDocument"], {**VALUES, "ApprovedGenerationTemplateUrl": ""})
        self.assertEqual(decision(p, "cloudformation:CreateStack", STACK, {"cloudformation:RoleARN": GENERATION, "cloudformation:TemplateUrl": URL}), "explicitDeny")
        self.assertEqual(decision(p, "cloudformation:DeleteStack", STACK, {"cloudformation:RoleARN": GENERATION}), "allowed")

    def test_generation_launch_inputs_and_tagging(self):
        ec2 = "arn:aws:ec2:us-west-2:" + ACCOUNT + ":"
        ctx = {"ec2:InstanceType": "t4g.micro", "ec2:MetadataHttpTokens": "required", "aws:RequestTag/project": "cloud-glider", "aws:RequestTag/environment": "sandbox"}
        self.assertEqual(self.boundary("Generation", "ec2:RunInstances", ec2 + "instance/i-new", ctx), "allowed")
        for key, value in [("ec2:InstanceType", "m5.large"), ("ec2:MetadataHttpTokens", "optional"), ("aws:RequestTag/environment", "other")]:
            self.assertNotEqual(self.boundary("Generation", "ec2:RunInstances", ec2 + "instance/i-new", {**ctx, key: value}), "allowed")
        for resource in [ec2 + "subnet/subnet-approved", ec2 + "security-group/sg-approved", ec2 + "volume/vol-new", ec2 + "network-interface/eni-new", "arn:aws:ec2:us-west-2::image/ami-approved"]:
            self.assertEqual(self.boundary("Generation", "ec2:RunInstances", resource), "allowed")
        self.assertNotEqual(self.boundary("Generation", "ec2:RunInstances", ec2 + "subnet/subnet-unrelated"), "allowed")
        self.assertEqual(self.boundary("Generation", "ec2:CreateTags", ec2 + "instance/i-new", {**ctx, "ec2:CreateAction": "RunInstances"}), "allowed")
        self.assertNotEqual(self.boundary("Generation", "ec2:CreateTags", ec2 + "instance/i-existing", ctx), "allowed")

    def test_agent_cannot_change_operator_state_but_can_check_it(self):
        for key in ["CONTROL", "HOLD", "BOOTSTRAP"]:
            for action in ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"]:
                self.assertEqual(self.boundary("Agent", action, TABLE, {"dynamodb:LeadingKeys": [key]}), "explicitDeny")
        self.assertEqual(self.boundary("Agent", "dynamodb:ConditionCheckItem", TABLE, {"dynamodb:LeadingKeys": ["CONTROL", "HOLD"]}), "allowed")
        for key in ["CURRENT", "GEN#000001", "LOCK", "AUDIT#PROPAGATION"]:
            self.assertEqual(self.boundary("Agent", "dynamodb:UpdateItem", TABLE, {"dynamodb:LeadingKeys": [key]}), "allowed")
        self.assertEqual(self.boundary("Agent", "dynamodb:PutItem", TABLE, {"dynamodb:LeadingKeys": ["CURRENT", "CONTROL"]}), "explicitDeny")
        self.assertEqual(self.boundary("Hold", "dynamodb:PutItem", TABLE, {"dynamodb:LeadingKeys": ["HOLD"]}), "allowed")
        self.assertEqual(self.boundary("Hold", "dynamodb:DeleteItem", TABLE, {"dynamodb:LeadingKeys": ["HOLD"]}), "explicitDeny")

    def test_s3_metadata_reads_and_artifact_immutability(self):
        for action in ["s3:GetBucketLocation", "s3:GetBucketVersioning"]:
            self.assertEqual(self.boundary("Agent", action, BUCKET), "allowed")
        self.assertEqual(self.boundary("Agent", "s3:ListBucket", BUCKET, {"s3:prefix": "generation/"}), "allowed")
        self.assertNotEqual(self.boundary("Agent", "s3:ListBucket", BUCKET, {"s3:prefix": "other/"}), "allowed")
        for action in ["s3:PutObject", "s3:DeleteObjectVersion"]:
            self.assertNotEqual(self.boundary("Agent", action, BUCKET + "/generation/agent.tar.gz"), "allowed")

    def test_foundation_cannot_escape_via_child_roles_or_edit_own_ceiling(self):
        policy_arn = "arn:aws:iam::" + ACCOUNT + ":policy/" + PREFIX + "-agent-boundary"
        for action in ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]:
            self.assertEqual(self.boundary("Foundation", action, AGENT, {"iam:PermissionsBoundary": policy_arn}), "allowed")
            self.assertNotEqual(self.boundary("Foundation", action, AGENT), "allowed")
            self.assertNotEqual(self.boundary("Foundation", action, AGENT, {"iam:PermissionsBoundary": policy_arn.replace("agent-boundary", "generation-boundary")}), "allowed")
        for action, resource in [("iam:CreatePolicyVersion", policy_arn), ("iam:DeleteRolePermissionsBoundary", FOUNDATION), ("iam:PutRolePolicy", FOUNDATION), ("iam:UpdateAssumeRolePolicy", ADMIN), ("dynamodb:UpdateItem", TABLE), ("cloudformation:UpdateStack", STACK)]:
            self.assertNotEqual(self.boundary("Foundation", action, resource), "allowed")

    def test_foundation_profile_precreation_lookup_and_rollback(self):
        profile = "arn:aws:iam::" + ACCOUNT + ":instance-profile/"
        actions = ["iam:GetInstanceProfile", "iam:RemoveRoleFromInstanceProfile", "iam:DeleteInstanceProfile"]
        for action in actions:
            for path in [PREFIX + "-agent", "cloud-glider/" + PREFIX + "-agent"]:
                with self.subTest(action=action, path=path):
                    self.assertEqual(self.boundary("Foundation", action, profile + path), "allowed")
            for path in [PREFIX + "-unrelated", "other/" + PREFIX + "-agent", "cloud-glider/" + PREFIX + "-unrelated"]:
                with self.subTest(action=action, path=path):
                    self.assertNotEqual(self.boundary("Foundation", action, profile + path), "allowed")
        for action in ["iam:CreateInstanceProfile", "iam:AddRoleToInstanceProfile", "iam:TagInstanceProfile", "iam:UntagInstanceProfile"]:
            with self.subTest(action=action):
                self.assertNotEqual(self.boundary("Foundation", action, profile + PREFIX + "-agent"), "allowed")

    def test_runtime_scp_blocks_escalation_and_preserves_expected_passrole(self):
        for principal in [AGENT, GENERATION, HOLD, BOOTSTRAP]:
            for action in ["iam:CreateRole", "iam:PutRolePolicy", "organizations:LeaveOrganization", "sts:AssumeRole", "ec2:CreateNetworkInterface"]:
                self.assertTrue(self.scp(action, AGENT, principal))
        self.assertTrue(self.scp("ec2:RunInstances", "*", AGENT))
        self.assertFalse(self.scp("ec2:RunInstances", "*", GENERATION))
        for principal, target, service in [(AGENT, GENERATION, "cloudformation.amazonaws.com"), (BOOTSTRAP, GENERATION, "cloudformation.amazonaws.com"), (GENERATION, AGENT, "ec2.amazonaws.com")]:
            self.assertFalse(self.scp("iam:PassRole", target, principal, **{"iam:PassedToService": service, "aws:RequestedRegion": "us-east-1"}))
            self.assertTrue(self.scp("iam:PassRole", ADMIN, principal, **{"iam:PassedToService": service}))
            self.assertTrue(self.scp("iam:PassRole", target, principal, **{"iam:PassedToService": "lambda.amazonaws.com"}))
            self.assertTrue(self.scp("iam:PassRole", target, principal))
        self.assertTrue(self.scp("iam:PassRole", GENERATION, HOLD, **{"iam:PassedToService": "cloudformation.amazonaws.com"}))

    def test_runtime_region_deny_does_not_disable_billing_administration(self):
        self.assertTrue(self.scp("cloudformation:CreateStack", STACK, AGENT, **{"aws:RequestedRegion": "us-east-1"}))
        self.assertFalse(self.scp("cloudwatch:PutMetricAlarm", "arn:aws:cloudwatch:us-east-1:" + ACCOUNT + ":alarm:cloud-glider-estimated-charges-15", FOUNDATION, **{"aws:RequestedRegion": "us-east-1"}))

    def test_boundary_protection_and_recovery_are_independent_of_foundation(self):
        boundary = "arn:aws:iam::" + ACCOUNT + ":policy/" + PREFIX + "-agent-boundary"
        for action in ["iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:DeletePolicy"]:
            self.assertTrue(self.scp(action, boundary, FOUNDATION))
            self.assertFalse(self.scp(action, boundary, ADMIN))
            self.assertFalse(self.scp(action, boundary, RECOVERY))
        self.assertTrue(self.scp("iam:CreateRole", AGENT, FOUNDATION))
        self.assertFalse(self.scp("iam:CreateRole", AGENT, FOUNDATION, **{"iam:PermissionsBoundary": boundary}))
        self.assertTrue(self.scp("iam:CreateRole", AGENT, FOUNDATION, **{"iam:PermissionsBoundary": boundary.replace("agent-boundary", "generation-boundary")}))
        self.assertTrue(self.scp("iam:DeleteRolePermissionsBoundary", AGENT, FOUNDATION))
        self.assertTrue(self.scp("iam:UpdateAssumeRolePolicy", FOUNDATION, FOUNDATION))
        self.assertTrue(self.scp("iam:UpdateAssumeRolePolicy", RECOVERY, FOUNDATION))

    def test_protected_resource_changes_need_recovery_not_normal_deployment(self):
        for action, resource in [("cloudtrail:StopLogging", "arn:aws:cloudtrail:us-west-2:" + ACCOUNT + ":trail/" + PREFIX + "-audit"), ("dynamodb:UpdateTimeToLive", TABLE), ("s3:DeleteObjectVersion", BUCKET + "/generation/template"), ("s3:PutBucketPolicy", BUCKET)]:
            self.assertTrue(self.scp(action, resource, FOUNDATION))
            self.assertFalse(self.scp(action, resource, RECOVERY))
        self.assertFalse(self.scp("dynamodb:UpdateItem", TABLE, ROLE + "Operator"))

    def test_rcp_identity_transport_and_service_source_matrix(self):
        p = self.organization["rcp-data-perimeter.json"]
        trusted = {"aws:PrincipalOrgID": ORG, "aws:PrincipalIsAWSService": "false", "aws:SecureTransport": "true"}
        for action, resource in [("s3:GetObject", BUCKET + "/generation/template"), ("dynamodb:GetItem", TABLE), ("logs:GetLogEvents", "arn:aws:logs:us-west-2:" + ACCOUNT + ":log-group:/cloud-glider/sandbox/audit/propagation:log-stream:events")]:
            self.assertNotEqual(decision(p, action, resource, trusted), "explicitDeny")
            self.assertEqual(decision(p, action, resource, {**trusted, "aws:PrincipalOrgID": "o-outsider000"}), "explicitDeny")
            self.assertEqual(decision(p, action, resource, {"aws:PrincipalIsAWSService": "false"}), "explicitDeny")
        self.assertEqual(decision(p, "s3:GetObject", BUCKET + "/generation/template", {**trusted, "aws:SecureTransport": "false"}), "explicitDeny")
        service = {"aws:PrincipalIsAWSService": "true", "aws:SourceAccount": ACCOUNT, "aws:SourceOrgID": ORG}
        self.assertNotEqual(decision(p, "s3:PutObject", BUCKET + "/AWSLogs/event", service), "explicitDeny")
        for changed in [{**service, "aws:SourceOrgID": "o-outsider000"}, {k: v for k,v in service.items() if k != "aws:SourceOrgID"}]:
            self.assertEqual(decision(p, "s3:PutObject", BUCKET + "/AWSLogs/event", changed), "explicitDeny")
        # Unsupported source context is left to the service-specific resource policy.
        self.assertNotEqual(decision(p, "s3:PutObject", BUCKET + "/AWSLogs/event", {"aws:PrincipalIsAWSService": "true"}), "explicitDeny")
        self.assertNotEqual(decision(p, "sts:AssumeRoleWithWebIdentity", FOUNDATION, {}), "explicitDeny")
        self.assertNotEqual(decision(p, "s3:GetObject", "arn:aws:s3:::unrelated/object", {}), "explicitDeny")

    def test_private_renderer_rejects_unsafe_or_incomplete_configuration(self):
        for key, value in [("account_id", "123456789012"), ("organization_id", "REQUIRED"), ("environment", "sandbox*"), ("boundary_admin_role_arn", AGENT), ("recovery_role_arn", ADMIN), ("recovery_role_arn", ROLE + "*"), ("foundation_role_arn", FOUNDATION.replace(ACCOUNT, "999988887777")), ("boundary_admin_role_arn", ADMIN.replace(":iam:", ":sts:"))]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                renderer.render_policies({**CONFIG, key: value})
        with self.assertRaises(ValueError):
            renderer.render_policies({k:v for k,v in CONFIG.items() if k != "recovery_role_arn"})
        with self.assertRaises(ValueError):
            renderer.substitute("${Unknown}", {})

    def test_policy_sizes_include_expanded_cloudformation_parameters(self):
        largest = {**VALUES, "Environment": "environment12345", "ApprovedGenerationTemplateUrl": "x" * self.template["Parameters"]["ApprovedGenerationTemplateUrl"]["MaxLength"], "AllowedImageId": "ami-0123456789abcdef0", "AllowedSubnetId": "subnet-0123456789abcdef0", "AllowedSecurityGroupId": "sg-0123456789abcdef0"}
        for name, resource in self.template["Resources"].items():
            policy = resolve(resource["Properties"]["PolicyDocument"], largest)
            with self.subTest(name=name):
                self.assertLessEqual(len(json.dumps(policy, separators=(",", ":"))), 6144)
        for text in renderer.render_policies(CONFIG).values():
            self.assertLessEqual(len(text), 5120)
            self.assertNotIn("${", text)

    def test_url_parameter_rejects_unversioned_or_insecure_templates(self):
        param = self.template["Parameters"]["ApprovedGenerationTemplateUrl"]
        pattern = param["AllowedPattern"]
        for value in ["", URL]:
            self.assertIsNotNone(re.fullmatch(pattern, value))
        for value in [URL.replace("https:", "http:"), URL.split("?")[0], URL.replace("approved", "null"), URL + "&other=1", URL.replace("us-west-2", "us-east-1")]:
            self.assertIsNone(re.fullmatch(pattern, value))


if __name__ == "__main__":
    unittest.main()
