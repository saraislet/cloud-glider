#!/usr/bin/env python3
"""Write non-secret AWS profiles while preserving existing profile settings."""
import argparse
import configparser
import json
from pathlib import Path
import re
from validate_operator_identities import PROFILE_NAMES


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--rendered-dir", required=True, type=Path)
    p.add_argument("--base-config", type=Path, default=Path.home() / ".aws/config")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    config = json.loads(args.config.read_text())
    arns = json.loads((args.rendered_dir / "role_arns.json").read_text())
    instance = config["sso_instance_arn"].rsplit("/", 1)[1]
    if not re.fullmatch(r"ssoins-[a-f0-9]{16}", instance):
        raise ValueError("Invalid Identity Center instance")
    ini = configparser.RawConfigParser()
    if args.base_config.exists():
        ini.read(args.base_config)

    def add(section, values):
        if ini.has_section(section):
            if dict(ini[section]) != values:
                raise ValueError("Existing section differs; refusing to replace " + section)
        else:
            ini[section] = values

    add("sso-session sarai-glider", {"sso_start_url": "https://identitycenter.amazonaws.com/" + instance,
        "sso_region": config["sso_region"], "sso_registration_scopes": "sso:account:access"})
    for profile, permission_set in (("glider-access", config["access_permission_set"]), ("glider-security-access", config["security_permission_set"])):
        add("profile " + profile, {"sso_session": "sarai-glider", "sso_account_id": config["account_id"], "sso_role_name": permission_set, "region": config["region"]})
    for name, profile in PROFILE_NAMES.items():
        for suffix, source in (("", "glider-security-access" if name == "GliderSecurityAdmin" else "glider-access"), ("-migration", "default")):
            add("profile " + profile + suffix, {"role_arn": arns[name], "source_profile": source,
                "role_session_name": "anna-sarai-" + profile.removeprefix("glider-"), "duration_seconds": "3600", "region": config["region"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as stream:
        ini.write(stream)
    args.output.chmod(0o600)
    print(json.dumps({"profiles": list(PROFILE_NAMES.values()), "migration_suffix": "-migration", "output": str(args.output), "credentials_written": False}))


if __name__ == "__main__":
    main()
