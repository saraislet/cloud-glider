#!/usr/bin/env python3
"""Dependency-free checks for safety-critical repository invariants."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(text: str, value: str, file_name: str) -> None:
    if value not in text:
        raise SystemExit(f"{file_name}: missing required safety marker: {value}")


def main() -> int:
    foundation_path = ROOT / "cfn" / "foundation.yaml"
    generation_path = ROOT / "cfn" / "generation.yaml"
    contract_path = ROOT / "docs" / "safety-contract.md"
    initializer_path = ROOT / "scripts" / "initialize_control.py"
    clear_hold_path = ROOT / "scripts" / "clear_emergency_hold.py"
    foundation = foundation_path.read_text()
    generation = generation_path.read_text()
    contract = contract_path.read_text()
    initializer = initializer_path.read_text()
    clear_hold = clear_hold_path.read_text()

    for marker in ("DeletionPolicy: Retain", "UpdateReplacePolicy: Retain", "PointInTimeRecoveryEnabled: true"):
        require(foundation, marker, str(foundation_path))
    for log_name in ("audit/infrastructure", "audit/iam-account", "audit/network", "audit/propagation"):
        require(foundation, log_name, str(foundation_path))
    for denial in ("DenyDirectComputeMutation", "DenyIamMutation", "DenyFoundationDeletion"):
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
        "AssociatePublicIpAddress: false",
        "AllowedValues: [t4g.micro]",
        "AllowedValues: [arm64]",
        "us-west-2",
        'test "$(uname -m)" = "aarch64"',
    ):
        require(generation, marker, str(generation_path))
    if "t3.micro" in foundation or "t3.micro" in generation:
        raise SystemExit("stale t3.micro baseline remains in a CloudFormation template")
    for marker in ("Fresh operator control", "No self-escalation", "Bounded concurrency"):
        require(contract, marker, str(contract_path))

    print("repository safety checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
