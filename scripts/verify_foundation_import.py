#!/usr/bin/env python3
"""Read-only check of the dated sandbox recovery change set; never executes it."""

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def validate_change_set(change_set, manifest, stack_id):
    if change_set.get("StackId") != stack_id:
        raise ValueError("Change set belongs to a different stack")
    if (change_set.get("Status"), change_set.get("ExecutionStatus")) != (
        "CREATE_COMPLETE", "AVAILABLE"
    ):
        raise ValueError("Change set is not available; do not execute or recreate blindly")
    expected = {(r["LogicalResourceId"], r["ResourceType"]) for r in manifest}
    changes = [c.get("ResourceChange", {}) for c in change_set.get("Changes", [])]
    actual = {(r.get("LogicalResourceId"), r.get("ResourceType")) for r in changes}
    if len(changes) != len(manifest) or actual != expected:
        raise ValueError("Change set does not match the exact import manifest")
    if any(r.get("Action") != "Import" for r in changes):
        raise ValueError("Change set contains a non-import action")


def aws(region, *args):
    result = subprocess.run(
        ["aws", "cloudformation", *args, "--region", region, "--output", "json"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def main():
    metadata = json.loads((ROOT / "cfn/foundation-recovery-change-set.json").read_text())
    for relative, expected_hash in metadata["FilesSha256"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"Recovery artifact changed after change-set creation: {relative}")
    region, stack_id = metadata["Region"], metadata["StackId"]
    stack = aws(region, "describe-stacks", "--stack-name", stack_id)["Stacks"][0]
    if stack["StackStatus"] != "UPDATE_ROLLBACK_COMPLETE":
        raise ValueError("Stack has changed since recovery preparation; inspect before proceeding")
    source = aws(region, "get-template", "--stack-name", stack_id)["TemplateBody"]
    if not isinstance(source, str) or hashlib.sha256(source.encode()).hexdigest() != metadata["SourceTemplateSha256"]:
        raise ValueError("Deployed template changed since recovery preparation")
    manifest = json.loads((ROOT / "cfn/foundation-recovery-resources.json").read_text())
    change_set = aws(region, "describe-change-set", "--stack-name", stack_id,
                     "--change-set-name", metadata["ChangeSetId"])
    validate_change_set(change_set, manifest, stack_id)
    print(f"Verified {len(manifest)} imports; no create, update, or delete actions.")
    print("This check does not validate live IAM authorization or resource drift.")
    print("Change set remains unexecuted. Follow docs/foundation-recovery.md.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
