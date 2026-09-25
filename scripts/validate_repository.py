#!/usr/bin/env python3
"""Dependency-free checks for safety-critical repository invariants."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRECTORIES = {
    ".git",
    ".artifacts",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "venv",
    "work",
}
CONFLICT_START = "<" * 7
CONFLICT_SEPARATOR = "=" * 7
CONFLICT_END = ">" * 7


def require(text: str, value: str, file_name: str) -> None:
    if value not in text:
        raise SystemExit(f"{file_name}: missing required safety marker: {value}")


def find_conflict_markers(root: Path) -> list[tuple[Path, int]]:
    matches = []
    for path in sorted(root.rglob("*")):
        is_ignored = any(part in IGNORED_DIRECTORIES for part in path.parts)
        if not path.is_file() or is_ignored:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            marker = line.lstrip()
            if (
                marker.startswith(CONFLICT_START)
                or marker == CONFLICT_SEPARATOR
                or marker.startswith(CONFLICT_END)
            ):
                matches.append((path, line_number))
    return matches


def main() -> int:
    conflicts = find_conflict_markers(ROOT)
    if conflicts:
        locations = "\n".join(
            f"- {path.relative_to(ROOT)}:{line}" for path, line in conflicts
        )
        raise SystemExit(f"unresolved merge conflict markers found:\n{locations}")

    foundation_path = ROOT / "cfn" / "foundation.yaml"
    billing_path = ROOT / "cfn" / "billing-alerts.yaml"
    network_path = ROOT / "cfn" / "network.yaml"
    generation_path = ROOT / "cfn" / "generation.yaml"
    contract_path = ROOT / "docs" / "safety-contract.md"
    initializer_path = ROOT / "scripts" / "initialize_control.py"
    clear_hold_path = ROOT / "scripts" / "clear_emergency_hold.py"
    agent_path = ROOT / "agent" / "cloud_glider" / "agent.py"
    gateway_path = ROOT / "agent" / "cloud_glider" / "aws_sdk.py"
    builder_path = ROOT / "scripts" / "build_agent_artifact.py"
    foundation = foundation_path.read_text()
    billing = billing_path.read_text()
    network = network_path.read_text()
    generation = generation_path.read_text()
    contract = contract_path.read_text()
    initializer = initializer_path.read_text()
    clear_hold = clear_hold_path.read_text()
    agent = agent_path.read_text()
    gateway = gateway_path.read_text()
    builder = builder_path.read_text()

    for marker in (
        "PermissionsBoundary:", "ApprovedGenerationTemplateUrl",
        "cloudformation:TemplateUrl", "ec2:CreateAction: RunInstances",
        "RetireGenerationStacksWithApprovedRole",
    ):
        require(foundation, marker, str(foundation_path))
    for marker in ("DeletionPolicy: Retain", "UpdateReplacePolicy: Retain", "PointInTimeRecoveryEnabled: true"):
        require(foundation, marker, str(foundation_path))
    for log_name in ("audit/infrastructure", "audit/iam-account", "audit/network", "audit/propagation"):
        require(foundation, log_name, str(foundation_path))
    for denial in (
        "DenyDirectComputeMutation",
        "DenyDirectNetworkMutation",
        "DenyIamMutation",
        "DenyFoundationDeletion",
    ):
        require(foundation, denial, str(foundation_path))
    for marker in (
        "us-west-2",
        "t4g.micro",
        "EmergencyHoldFunction",
        '"PK": {"S": "HOLD"}',
        '"SK": {"S": "ACTIVE"}',
        '"ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"',
        "CreateButNeverClearEmergencyHold",
    ):
        require(foundation, marker, str(foundation_path))
    if "SET emergency_hold" in foundation:
        raise SystemExit("emergency hold must use HOLD/ACTIVE, not a mutable CONTROL attribute")
    if '"emergency_hold": {"BOOL"' in initializer:
        raise SystemExit("initializer must not create the deprecated emergency_hold attribute")
    for marker in ('av_string("HOLD")', 'av_string("ACTIVE")', "CLEAR_EMERGENCY_HOLD"):
        require(clear_hold, marker, str(clear_hold_path))
    forbidden_resource_types = ("Type: AWS::IAM::", "Type: AWS::EC2::SecurityGroup\n", "Type: AWS::EC2::VPC\n")
    if any(marker in generation for marker in forbidden_resource_types):
        raise SystemExit("generation template must not create IAM or networking resources")
    for marker in (
        "HttpTokens: required",
        "Encrypted: true",
        "AssociatePublicIpAddress: true",
        "AllowedValues: [t4g.micro]",
        "AllowedValues: [arm64]",
        "us-west-2",
        'test "$(uname -m)" = "aarch64"',
    ):
        require(generation, marker, str(generation_path))
    if "AssociatePublicIpAddress: false" in generation:
        raise SystemExit("generation template must assign the approved ephemeral public IPv4 address")
    if "t3.micro" in foundation or "t3.micro" in generation:
        raise SystemExit("stale t3.micro baseline remains in a CloudFormation template")
    for marker in ("Fresh operator control", "No self-escalation", "Bounded concurrency"):
        require(contract, marker, str(contract_path))
    for marker in (
        "AWS/Billing",
        "EstimatedCharges",
        "us-east-1",
        "cloud-glider-billing-alerts",
    ):
        require(billing, marker, str(billing_path))
    for marker in (
        "OperationalAlertsTopic",
        "EmergencyHoldErrorsAlarm",
        "EmergencyHoldThrottlesAlarm",
        "StateTableReadThrottleAlarm",
        "StateTableWriteThrottleAlarm",
        "StateTableSystemErrorsAlarm",
        "ManageGenerationStatusAlarms",
    ):
        require(foundation, marker, str(foundation_path))
    for marker in (
        "OperationalAlertsTopicArn",
        "GenerationStatusCheckAlarm",
        "MetricName: StatusCheckFailed",
        "TreatMissingData: missing",
    ):
        require(generation, marker, str(generation_path))
    for marker in (
        "Type: AWS::EC2::VPC",
        "Type: AWS::EC2::InternetGateway",
        "MapPublicIpOnLaunch: false",
        "DestinationCidrBlock: 0.0.0.0/0",
        "SecurityGroupIngress: []",
        "FromPort: 443",
        "ToPort: 443",
        "GenerationSecurityGroupId",
    ):
        require(network, marker, str(network_path))
    if "MapPublicIpOnLaunch: true" in network:
        raise SystemExit("network subnet must not assign public IPv4 addresses implicitly")
    for marker in (
        "read_control_and_hold",
        "acquire_lease",
        "wait_for_healthy_successor",
        "continuation_preflight",
        "conditional_handoff",
        "delete_stack",
    ):
        require(agent, marker, str(agent_path))
    for marker in (
        'ChangeSetType="CREATE"',
        "transact_write_items",
        "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        "service-quotas",
    ):
        require(gateway, marker, str(gateway_path))
    for marker in ("mtime=0", "bin/cloud-glider", "sha256"):
        require(builder, marker, str(builder_path))

    print("repository safety checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
