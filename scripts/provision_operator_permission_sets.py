#!/usr/bin/env python3
"""Provision additive Identity Center assumption paths; preserve GliderManager."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def provision(session, plan, *, apply=False):
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != plan["management_account"]:
        raise ValueError("Identity Center administration requires the verified management account")
    sso = session.client("sso-admin", region_name=plan["sso_region"])
    instances = [i for page in sso.get_paginator("list_instances").paginate() for i in page["Instances"]]
    instance = next(i for i in instances if i["InstanceArn"] == plan["instance_arn"])
    if instance["OwnerAccountId"] != plan["management_account"]:
        raise ValueError("Unexpected Identity Center owner")
    user = session.client("identitystore", region_name=plan["sso_region"]).describe_user(IdentityStoreId=instance["IdentityStoreId"], UserId=plan["user_id"])
    if user["UserName"] != plan["username"]:
        raise ValueError("Unexpected assignment recipient")
    existing = {sso.describe_permission_set(InstanceArn=plan["instance_arn"], PermissionSetArn=a)["PermissionSet"]["Name"]: a
                for page in sso.get_paginator("list_permission_sets").paginate(InstanceArn=plan["instance_arn"]) for a in page["PermissionSets"]}
    result = {"identity": identity, "at": datetime.now(timezone.utc).isoformat(), "mode": "APPLY" if apply else "PREVIEW", "permission_sets": {}, "existing_GliderManager": "UNCHANGED"}
    for name, policy in plan["permission_sets"].items():
        arn = existing.get(name)
        if arn:
            actual = sso.get_inline_policy_for_permission_set(InstanceArn=plan["instance_arn"], PermissionSetArn=arn).get("InlinePolicy", "")
            if not actual or json.loads(actual) != policy:
                raise ValueError("Existing permission set differs; refusing to overwrite " + name)
            if any(page["AttachedManagedPolicies"] for page in sso.get_paginator("list_managed_policies_in_permission_set").paginate(InstanceArn=plan["instance_arn"], PermissionSetArn=arn)):
                raise ValueError("Unexpected managed permissions on " + name)
            if any(page["CustomerManagedPolicyReferences"] for page in sso.get_paginator("list_customer_managed_policy_references_in_permission_set").paginate(InstanceArn=plan["instance_arn"], PermissionSetArn=arn)):
                raise ValueError("Unexpected customer-managed permissions on " + name)
        elif apply:
            arn = sso.create_permission_set(InstanceArn=plan["instance_arn"], Name=name, Description="Assumption-only Cloud Glider human task roles; existing access retained", SessionDuration="PT1H", Tags=[{"Key": "project", "Value": "cloud-glider"}, {"Key": "purpose", "Value": "operator-access"}])["PermissionSet"]["PermissionSetArn"]
            sso.put_inline_policy_to_permission_set(InstanceArn=plan["instance_arn"], PermissionSetArn=arn, InlinePolicy=json.dumps(policy))
        status = {"arn": arn, "recipient": plan["username"], "account": plan["target_account"]}
        if apply:
            if json.loads(sso.get_inline_policy_for_permission_set(InstanceArn=plan["instance_arn"], PermissionSetArn=arn)["InlinePolicy"]) != policy:
                raise ValueError("Policy readback differs")
            assignments = [x for page in sso.get_paginator("list_account_assignments").paginate(InstanceArn=plan["instance_arn"], AccountId=plan["target_account"], PermissionSetArn=arn) for x in page["AccountAssignments"]]
            if not any(x["PrincipalType"] == "USER" and x["PrincipalId"] == plan["user_id"] for x in assignments):
                status["assignment"] = sso.create_account_assignment(InstanceArn=plan["instance_arn"], TargetId=plan["target_account"], TargetType="AWS_ACCOUNT", PermissionSetArn=arn, PrincipalType="USER", PrincipalId=plan["user_id"])["AccountAssignmentCreationStatus"]
            else:
                status["assignment"] = {"Status": "ALREADY_ASSIGNED"}
                status["provisioning"] = sso.provision_permission_set(InstanceArn=plan["instance_arn"], PermissionSetArn=arn, TargetType="AWS_ACCOUNT", TargetId=plan["target_account"])["PermissionSetProvisioningStatus"]
        result["permission_sets"][name] = status
    return result


def main():
    import boto3
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    result = provision(boto3.Session(profile_name=args.profile, region_name=plan["sso_region"]), plan, apply=args.apply)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2, default=str) + "\n")
    args.receipt.chmod(0o600)
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
