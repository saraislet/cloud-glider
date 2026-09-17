#!/usr/bin/env python3
"""Create Cloud Glider's initial DynamoDB records without overwriting state."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
DEFAULTS_FILE = Path(__file__).resolve().parents[1] / "config" / "runtime-defaults.json"


def load_defaults() -> dict[str, Any]:
    with DEFAULTS_FILE.open(encoding="utf-8") as defaults_file:
        return json.load(defaults_file)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def av_string(value: str) -> dict[str, str]:
    return {"S": value}


def build_transaction(args: argparse.Namespace, *, now: str, event_id: str) -> list[dict[str, Any]]:
    defaults = load_defaults()
    if not SHA256_RE.fullmatch(args.template_sha256):
        raise ValueError("--template-sha256 must be exactly 64 hexadecimal characters")
    if not SHA256_RE.fullmatch(args.agent_artifact_sha256):
        raise ValueError("--agent-artifact-sha256 must be exactly 64 hexadecimal characters")
    if args.max_live_generations != 3:
        raise ValueError("the safety contract fixes --max-live-generations at 3")

    control = {
        "PK": av_string("CONTROL"),
        "SK": av_string("GLOBAL"),
        "propagation_enabled": {"BOOL": False},
        "desired_template_version": av_string(args.template_version),
        "desired_bootstrap_version": av_string(args.bootstrap_version),
        "template_s3_bucket": av_string(args.template_bucket),
        "template_s3_key": av_string(args.template_key),
        "template_s3_version_id": av_string(args.template_s3_version_id),
        "template_sha256": av_string(args.template_sha256.lower()),
        "template_build_id": av_string(args.template_build_id),
        "agent_artifact_bucket": av_string(args.agent_artifact_bucket),
        "agent_artifact_key": av_string(args.agent_artifact_key),
        "agent_artifact_version_id": av_string(args.agent_artifact_version_id),
        "agent_artifact_sha256": av_string(args.agent_artifact_sha256.lower()),
        "max_generation": {"N": str(args.max_generation)},
        "max_live_generations": {"N": "3"},
        "concurrency_model": av_string(defaults["concurrency_model"]),
        "approved_region": av_string(defaults["aws_region"]),
        "approved_architecture": av_string(defaults["image_architecture"]),
        "approved_instance_types": {"L": [av_string(defaults["instance_type"])]},
        "readiness_poll_seconds": {"N": str(defaults["readiness_poll_seconds"])},
        "readiness_required_heartbeats": {"N": str(defaults["readiness_required_heartbeats"])},
        "heartbeat_interval_seconds": {"N": str(defaults["heartbeat_interval_seconds"])},
        "readiness_timeout_seconds": {"N": str(defaults["readiness_timeout_seconds"])},
        "environment": av_string(args.environment),
        "updated_at": av_string(now),
        "updated_by": av_string(args.operator_id),
    }
    current = {
        "PK": av_string("CURRENT"),
        "SK": av_string("GLOBAL"),
        "status": av_string("UNINITIALIZED"),
        "environment": av_string(args.environment),
        "updated_at": av_string(now),
        "updated_by": av_string(args.operator_id),
    }
    audit = {
        "PK": av_string("AUDIT#PROPAGATION"),
        "SK": av_string(f"EVENT#{now}#{event_id}"),
        "schema_version": av_string("1.0"),
        "event_id": av_string(event_id),
        "occurred_at": av_string(now),
        "category": av_string("PROPAGATION"),
        "actor": av_string(args.operator_id),
        "action": av_string("INITIALIZE_CONTROL_STATE"),
        "resource_type": av_string("AWS::DynamoDB::Table"),
        "resource_id": av_string(args.table_name),
        "result": av_string("APPLIED"),
        "reason": av_string("initial sandbox safety baseline"),
        "correlation_id": av_string(event_id),
        "environment": av_string(args.environment),
        "template_s3_bucket": av_string(args.template_bucket),
        "template_s3_key": av_string(args.template_key),
        "template_s3_version_id": av_string(args.template_s3_version_id),
        "template_sha256": av_string(args.template_sha256.lower()),
        "template_build_id": av_string(args.template_build_id),
        "agent_artifact_bucket": av_string(args.agent_artifact_bucket),
        "agent_artifact_key": av_string(args.agent_artifact_key),
        "agent_artifact_version_id": av_string(args.agent_artifact_version_id),
        "agent_artifact_sha256": av_string(args.agent_artifact_sha256.lower()),
    }

    condition = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    return [
        {"Put": {"TableName": args.table_name, "Item": control, "ConditionExpression": condition}},
        {"Put": {"TableName": args.table_name, "Item": current, "ConditionExpression": condition}},
        {"Put": {"TableName": args.table_name, "Item": audit, "ConditionExpression": condition}},
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = load_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--operator-id", required=True, help="Operator ARN or stable identity")
    parser.add_argument("--template-version", required=True)
    parser.add_argument("--bootstrap-version", required=True)
    parser.add_argument("--template-bucket", required=True)
    parser.add_argument("--template-key", required=True)
    parser.add_argument("--template-s3-version-id", required=True)
    parser.add_argument("--template-sha256", required=True)
    parser.add_argument("--template-build-id", required=True, help="Git commit or immutable build ID")
    parser.add_argument("--agent-artifact-bucket", required=True)
    parser.add_argument("--agent-artifact-key", required=True)
    parser.add_argument("--agent-artifact-version-id", required=True)
    parser.add_argument("--agent-artifact-sha256", required=True)
    parser.add_argument("--environment", default="sandbox")
    parser.add_argument("--max-generation", type=int, default=defaults["max_generation"])
    parser.add_argument("--max-live-generations", type=int, default=defaults["max_live_generations"])
    parser.add_argument("--region", default=defaults["aws_region"])
    parser.add_argument("--profile")
    parser.add_argument("--apply", action="store_true", help="Execute instead of printing a dry run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = utc_now()
    event_id = str(uuid.uuid4())
    try:
        transaction = build_transaction(args, now=now, event_id=event_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.apply:
        print(json.dumps({"mode": "DRY_RUN", "transact_items": transaction}, indent=2, sort_keys=True))
        return 0

    command = [
        "aws",
        "dynamodb",
        "transact-write-items",
        "--transact-items",
        json.dumps(transaction, separators=(",", ":")),
    ]
    if args.region:
        command.extend(["--region", args.region])
    if args.profile:
        command.extend(["--profile", args.profile])

    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        print("initialization failed; CloudTrail remains the authoritative failure record", file=sys.stderr)
        return completed.returncode

    print(json.dumps({"mode": "APPLIED", "event_id": event_id, "occurred_at": now}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
