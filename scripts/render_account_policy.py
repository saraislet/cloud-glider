#!/usr/bin/env python3
"""Render example IAM policies locally; does not call AWS or apply permissions."""
import argparse
import json
import os
from pathlib import Path
import re

EXAMPLE_ACCOUNT_ID = "123456789012"
ROOT = Path(__file__).resolve().parents[1]


def render_policy(text, account_id):
    if not re.fullmatch(r"[0-9]{12}", account_id or "") or account_id == EXAMPLE_ACCOUNT_ID:
        raise ValueError("AWS_ACCOUNT_ID must be the intended 12-digit account, not the example")
    value = json.loads(text.replace(EXAMPLE_ACCOUNT_ID, account_id))
    if not isinstance(value, dict) or "Statement" not in value:
        raise ValueError("Expected an IAM policy document")
    return json.dumps(value, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", help="JSON filename under iam/")
    args = parser.parse_args()
    if Path(args.policy).name != args.policy or not args.policy.endswith(".json"):
        parser.error("Supply only an IAM JSON filename")
    result = render_policy((ROOT / "iam" / args.policy).read_text(), os.environ.get("AWS_ACCOUNT_ID"))
    output = ROOT / ".artifacts" / "iam" / args.policy
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result)
    print("Rendered .artifacts/iam/" + args.policy + "; review before applying")


if __name__ == "__main__":
    main()
