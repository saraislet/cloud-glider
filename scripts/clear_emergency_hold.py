#!/usr/bin/env python3
"""Clear HOLD/ACTIVE as an operator and append an audit record atomically."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any


def av_string(value: str) -> dict[str, str]:
    return {"S": value}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_transaction(args: argparse.Namespace, *, now: str, event_id: str) -> list[dict[str, Any]]:
    audit = {
        "PK": av_string("AUDIT#PROPAGATION"),
        "SK": av_string(f"EVENT#{now}#{event_id}"),
        "schema_version": av_string("1.0"),
        "event_id": av_string(event_id),
        "occurred_at": av_string(now),
        "category": av_string("PROPAGATION"),
        "actor": av_string(args.operator_id),
        "action": av_string("CLEAR_EMERGENCY_HOLD"),
        "resource_type": av_string("HOLD_RECORD"),
        "resource_id": av_string("HOLD/ACTIVE"),
        "result": av_string("APPLIED"),
        "reason": av_string(args.reason),
        "correlation_id": av_string(event_id),
        "environment": av_string(args.environment),
    }
    return [
        {
            "Delete": {
                "TableName": args.table_name,
                "Key": {"PK": av_string("HOLD"), "SK": av_string("ACTIVE")},
                "ConditionExpression": "attribute_exists(PK) AND attribute_exists(SK)",
            }
        },
        {
            "Put": {
                "TableName": args.table_name,
                "Item": audit,
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }
        },
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", required=True)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--environment", default="sandbox")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--profile")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = utc_now()
    event_id = str(uuid.uuid4())
    transaction = build_transaction(args, now=now, event_id=event_id)
    if not args.apply:
        print(json.dumps({"mode": "DRY_RUN", "transact_items": transaction}, indent=2, sort_keys=True))
        return 0

    command = [
        "aws",
        "dynamodb",
        "transact-write-items",
        "--transact-items",
        json.dumps(transaction, separators=(",", ":")),
        "--region",
        args.region,
    ]
    if args.profile:
        command.extend(["--profile", args.profile])
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        print("hold clearance failed; the hold remains active", file=sys.stderr)
        return completed.returncode
    print(json.dumps({"mode": "APPLIED", "event_id": event_id, "occurred_at": now}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
