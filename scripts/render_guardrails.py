#!/usr/bin/env python3
"""Render private SCP/RCP candidates locally. Never contacts AWS or attaches policies."""

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {
    "account_id", "organization_id", "environment", "boundary_admin_role_arn",
    "recovery_role_arn", "foundation_role_arn",
}
TOKENS = {
    "AccountId": "account_id", "OrganizationId": "organization_id",
    "Environment": "environment", "BoundaryAdminRoleArn": "boundary_admin_role_arn",
    "RecoveryRoleArn": "recovery_role_arn", "FoundationRoleArn": "foundation_role_arn",
}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != FIELDS:
        raise ValueError("Configuration must contain exactly the fields in config/guardrails.example.json")
    if any(not isinstance(v, str) for v in config.values()):
        raise ValueError("Every configuration value must be a string")
    account = config["account_id"]
    if not re.fullmatch(r"[0-9]{12}", account) or account in {"123456789012", "000000000000"}:
        raise ValueError("Use the independently verified member account, not an example account")
    if not re.fullmatch(r"o-[a-z0-9]{10,32}", config["organization_id"]):
        raise ValueError("Use a verified organization ID")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,15}", config["environment"]):
        raise ValueError("Invalid Cloud Glider environment")
    roles = [config[key] for key in ("boundary_admin_role_arn", "recovery_role_arn", "foundation_role_arn")]
    pattern = r"arn:aws:iam::" + account + r":role/[A-Za-z0-9_+=,.@/-]+"
    for value in roles:
        if not re.fullmatch(pattern, value) or len(value) > 2048:
            raise ValueError("Administration and recovery require exact IAM role ARNs in the member account")
        name = value.rsplit("/", 1)[-1]
        if not name or len(name) > 64:
            raise ValueError("Invalid IAM role name")
        prefix = "cloud-glider-" + config["environment"] + "-"
        if name in {prefix + "agent", prefix + "generation-cfn", prefix + "emergency-hold"} or name.startswith(prefix + "bootstrap-BootstrapRole-"):
            raise ValueError("A runtime role cannot administer guardrails or act as recovery/deployment")
    if len(set(roles)) != len(roles):
        raise ValueError("Boundary administration, recovery, and foundation deployment must be distinct roles")
    return {token: config[field] for token, field in TOKENS.items()}


def substitute(value, tokens):
    if isinstance(value, dict):
        return {key: substitute(child, tokens) for key, child in value.items()}
    if isinstance(value, list):
        return [substitute(child, tokens) for child in value]
    if isinstance(value, str):
        def replace(match):
            if match[1] not in tokens:
                raise ValueError("Unknown policy substitution")
            return tokens[match[1]]
        value = re.sub(r"\$\{([^}]+)\}", replace, value)
        if "${" in value:
            raise ValueError("Unresolved policy substitution")
    return value


def render_policies(config, template_directory=None):
    tokens = validate_config(config)
    directory = template_directory or ROOT / "iam" / "organization"
    results = {}
    for path in sorted(directory.glob("*.json")):
        policy = substitute(json.loads(path.read_text()), tokens)
        statements = policy.get("Statement", [])
        if not statements or any(s.get("Effect") != "Deny" for s in statements):
            raise ValueError("Organization candidates must contain only deny statements")
        is_rcp = path.name.startswith("rcp-")
        if not is_rcp and not path.name.startswith("scp-"):
            raise ValueError("Unknown organization policy type")
        for s in statements:
            if is_rcp and s.get("Principal") != "*":
                raise ValueError("RCP candidates require Principal *")
            if not is_rcp and ("Principal" in s or "NotPrincipal" in s):
                raise ValueError("SCPs must scope callers with aws:PrincipalArn")
        compact = json.dumps(policy, separators=(",", ":")) + "\n"
        if len(compact) > 5120:
            raise ValueError(path.name + " exceeds the 5,120-character Organizations policy limit")
        results[path.name] = compact
    if not results:
        raise ValueError("No organization policy templates found")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Private local configuration; never commit it")
    args = parser.parse_args()
    try:
        results = render_policies(json.loads(args.config.read_text()))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    output = ROOT / ".artifacts" / "guardrails"
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, contents in results.items():
        path = output / name
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(contents)
    print("Rendered " + str(len(results)) + " candidates in .artifacts/guardrails/; no AWS calls made")


if __name__ == "__main__":
    main()
