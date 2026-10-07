#!/usr/bin/env python3
"""Render an additive human-role migration; makes no AWS calls."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re

ROLES = {
    "GliderObserver": "observer",
    "GliderOperator": "operator",
    "GliderStateRecovery": "state-recovery",
    "GliderExceptionalCleanup": "exceptional-cleanup",
    "GliderImageBuilder": "image-builder",
    "GliderRelease": "release",
    "GliderSecurityAdmin": "security-admin",
}


def statement(sid, actions, resources, condition=None):
    result = {"Sid": sid, "Effect": "Allow", "Action": actions, "Resource": resources}
    if condition:
        result["Condition"] = condition
    return result


def document(statements):
    return {"Version": "2012-10-17", "Statement": statements}


def render(config, image_policy):
    account, region, env = config["account_id"], config["region"], config["environment"]
    if not re.fullmatch(r"[0-9]{12}", account) or account == "123456789012":
        raise ValueError("Use a verified account, not the example account")
    if region != "us-west-2" or env != "sandbox":
        raise ValueError("This migration covers only sandbox in us-west-2")
    for field in ("migration_user", "migration_group", "access_permission_set", "security_permission_set"):
        if not re.fullmatch(r"[A-Za-z0-9+=,.@_-]{1,64}", config[field]):
            raise ValueError("Invalid identity name: " + field)
    if config["access_permission_set"] == config["security_permission_set"]:
        raise ValueError("Routine and security access must have separate permission sets")
    sso_region = config["sso_region"]
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]", sso_region):
        raise ValueError("Invalid Identity Center region")
    iam = f"arn:aws:iam::{account}:"
    regional = lambda service: f"arn:aws:{service}:{region}:{account}:"
    base = "cloud-glider-sandbox"
    tables = [regional("dynamodb") + "table/" + base + "-" + n for n in ("state", "generations", "audit")]
    state, generations, audit = tables
    bucket = f"{base}-{account}-{region}-artifacts"
    bucket_arn = "arn:aws:s3:::" + bucket
    role_names = {name: base + "-operator-" + suffix for name, suffix in ROLES.items()}
    role_arns = {name: iam + "role/cloud-glider/operator/" + role for name, role in role_names.items()}
    foundation = iam + "role/" + base + "-foundation-cfn"
    generation_service = iam + "role/" + base + "-generation-cfn"
    regional_condition = {"StringEquals": {"aws:RequestedRegion": region}}
    logs = [regional("logs") + "log-group:/cloud-glider/sandbox/*", regional("logs") + "log-group:/aws/lambda/cloud-glider-sandbox-*"]
    read = [
        statement("ReadGliderTables", ["dynamodb:DescribeTable", "dynamodb:DescribeContinuousBackups", "dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan"], tables),
        statement("ReadGliderStreams", ["dynamodb:DescribeStream", "dynamodb:GetShardIterator", "dynamodb:GetRecords"], [t + "/stream/*" for t in tables]),
        statement("ReadRegionalMetadata", ["ec2:Describe*", "cloudformation:ListStacks", "cloudformation:ValidateTemplate", "logs:DescribeLogGroups", "cloudwatch:DescribeAlarms", "cloudwatch:DescribeAlarmHistory", "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics", "servicequotas:GetServiceQuota", "servicequotas:GetAWSDefaultServiceQuota", "servicequotas:ListServiceQuotas", "cloudtrail:LookupEvents"], "*", regional_condition),
        statement("ReadGliderStacks", ["cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents", "cloudformation:DescribeStackResource", "cloudformation:DescribeStackResources", "cloudformation:DescribeChangeSet", "cloudformation:GetTemplate", "cloudformation:ListStackResources"], regional("cloudformation") + "stack/" + base + "*/*"),
        statement("ReadGliderLogs", ["logs:DescribeLogStreams", "logs:GetLogEvents", "logs:FilterLogEvents"], logs),
        statement("ReadArtifactBucket", ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketLocation", "s3:GetBucketVersioning"], bucket_arn),
        statement("ReadArtifacts", ["s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectAttributes", "s3:GetObjectVersionAttributes"], bucket_arn + "/*"),
        statement("InspectGliderIAM", ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListRoleTags", "iam:GetInstanceProfile", "iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions"], [iam + "role/cloud-glider*", iam + "instance-profile/cloud-glider*", iam + "policy/cloud-glider*", iam + "policy/CloudGlider*", iam + "policy/PassFoundationServiceRoleOnly"]),
    ]
    policies = {name: document(copy.deepcopy(read)) for name in ROLES if name != "GliderImageBuilder"}
    def add(name, *stmts):
        policies[name]["Statement"].extend(stmts)
    def keys(values):
        return {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": values}, "Null": {"dynamodb:LeadingKeys": "false"}}
    audit_write = statement("WriteOperationalAudit", ["dynamodb:PutItem", "dynamodb:ConditionCheckItem"], audit,
                            keys(["AUDIT#PROPAGATION", "AUDIT#RELEASE", "AUDIT#RECOVERY"]))
    add("GliderOperator",
        statement("ConfigureAndRequestOperatorActions", ["dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:ConditionCheckItem"], state, keys(["CONTROL", "BOOTSTRAP", "HOLD"])),
        copy.deepcopy(audit_write))
    attrs = ["PK", "SK", "request_id", "token", "settled", "aborted", "instance_id"]
    add("GliderStateRecovery",
        statement("ReconcileFamilySubmissions", "dynamodb:UpdateItem", generations, {
            "ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["GEN#r*"]},
            "ForAllValues:StringEquals": {"dynamodb:Attributes": attrs},
            "StringEqualsIfExists": {"dynamodb:ReturnValues": "NONE"},
            "Null": {"dynamodb:LeadingKeys": "false", "dynamodb:Attributes": "false"}}),
        statement("RecoverLifecycleAndHold", ["dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:ConditionCheckItem"], state, keys(["CONTROL", "CURRENT", "BOOTSTRAP", "HOLD"])),
        statement("CheckGenerationRecoveryEvidence", "dynamodb:ConditionCheckItem", generations, {"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["GEN#r*"]}, "Null": {"dynamodb:LeadingKeys": "false"}}),
        copy.deepcopy(audit_write))
    disposable = [regional("cloudformation") + "stack/" + base + "-" + suffix + "/*" for suffix in ("gen-*", "ami-smoke-*", "smoke-*", "image-smoke-*")]
    add("GliderExceptionalCleanup",
        statement("TerminateGliderGenerationInstances", "ec2:TerminateInstances", regional("ec2") + "instance/*", {"StringEquals": {"ec2:ResourceTag/project": "cloud-glider", "ec2:ResourceTag/environment": env, "ec2:ResourceTag/purpose": "generation-compute"}}),
        statement("DeleteGliderResidualVolumes", "ec2:DeleteVolume", regional("ec2") + "volume/*", {"StringEquals": {"ec2:ResourceTag/project": "cloud-glider", "ec2:ResourceTag/environment": env}}),
        statement("DeleteDisposableGliderStacks", "cloudformation:DeleteStack", disposable),
        statement("DeleteGenerationAlarms", "cloudwatch:DeleteAlarms", regional("cloudwatch") + "alarm:" + base + "-gen-*"),
        statement("PassCleanupServiceRole", "iam:PassRole", generation_service, {"StringEquals": {"iam:PassedToService": "cloudformation.amazonaws.com"}}),
        copy.deepcopy(audit_write))
    if image_policy.get("Version") != "2012-10-17" or not image_policy.get("Statement"):
        raise ValueError("Supply the inspected image-build role permissions policy")
    # Preserve the deployed builder permissions rather than silently changing its contract.
    policies["GliderImageBuilder"] = copy.deepcopy(image_policy)
    smoke_stack = regional("cloudformation") + "stack/" + base + "-ami-smoke-*/*"
    smoke = [
        statement("ManageIsolatedImageSmokeStacks", ["cloudformation:CreateStack", "cloudformation:DeleteStack", "cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents", "cloudformation:DescribeStackResources", "cloudformation:ListStackResources", "cloudformation:GetTemplate"], smoke_stack),
        statement("ValidateSmokeTemplate", "cloudformation:ValidateTemplate", "*", regional_condition),
        statement("SmokeOwnedCandidateImages", "ec2:RunInstances", f"arn:aws:ec2:{region}::image/*", {"StringEquals": {"ec2:Owner": account, "ec2:ResourceTag/project": "cloud-glider", "ec2:ResourceTag/purpose": ["daemon-image", "agent-image"], "ec2:ResourceTag/approval": "candidate"}}),
        statement("LaunchSmallTaggedSmokeInstance", "ec2:RunInstances", regional("ec2") + "instance/*", {"StringEquals": {"aws:RequestTag/project": "cloud-glider", "aws:RequestTag/purpose": "image-smoke-test", "ec2:InstanceType": "t4g.micro", "ec2:MetadataHttpTokens": "required"}}),
        statement("LaunchSmokeInterfaceAndRoot", "ec2:RunInstances", [regional("ec2") + "network-interface/*", regional("ec2") + "volume/*"], {"StringEquals": {"aws:RequestedRegion": region}, "ForAnyValue:StringEquals": {"aws:CalledVia": "cloudformation.amazonaws.com"}}),
        statement("CleanTaggedSmokeInstances", "ec2:TerminateInstances", regional("ec2") + "instance/*", {"StringEquals": {"ec2:ResourceTag/project": "cloud-glider", "ec2:ResourceTag/purpose": "image-smoke-test"}}),
    ]
    release_stacks = [regional("cloudformation") + "stack/" + n + "/*" for n in
                      (base, base + "-foundation", base + "-bootstrap", base + "-launch-template", base + "-image-permissions")]
    launch_template = config["launch_template_id"]
    if not re.fullmatch(r"lt-[0-9a-f]{17}", launch_template):
        raise ValueError("Supply the inspected persistent launch-template ID")
    launch_stack = regional("cloudformation") + "stack/" + base + "-launch-template/*"
    add("GliderRelease",
        statement("PrepareReleaseChangeSets", "cloudformation:CreateChangeSet", release_stacks, {"ArnEquals": {"cloudformation:RoleARN": foundation}}),
        statement("PrepareExistingLaunchTemplateStackChange", "cloudformation:CreateChangeSet", launch_stack, {"Null": {"cloudformation:RoleARN": "true"}}),
        statement("UpdatePinnedTemplateThroughCloudFormation", ["ec2:CreateLaunchTemplateVersion", "ec2:DeleteLaunchTemplateVersions", "ec2:ModifyLaunchTemplate", "ec2:CreateTags"], regional("ec2") + "launch-template/" + launch_template, {"ForAnyValue:StringEquals": {"aws:CalledVia": "cloudformation.amazonaws.com"}}),
        statement("ExecuteAndDiscardReleaseChangeSets", ["cloudformation:ExecuteChangeSet", "cloudformation:DeleteChangeSet", "cloudformation:ContinueUpdateRollback"], release_stacks),
        statement("PassFoundationServiceRole", "iam:PassRole", foundation, {"StringEquals": {"iam:PassedToService": "cloudformation.amazonaws.com"}}),
        statement("PublishVersionedArtifacts", ["s3:PutObject", "s3:PutObjectTagging", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], [bucket_arn + "/" + prefix + "/*" for prefix in ("generation", "bootstrap", "foundation", "deployment", "observer")]),
        statement("CoordinateIdleReleasePins", ["dynamodb:UpdateItem", "dynamodb:ConditionCheckItem"], state, keys(["CONTROL", "BOOTSTRAP", "CURRENT", "HOLD"])),
        copy.deepcopy(audit_write))
    admin_roles = [iam + "role/" + base + "-*", iam + "role/cloud-glider/" + base + "-*", *role_arns.values()]
    add("GliderSecurityAdmin",
        statement("AdministerGliderRoles", ["iam:CreateRole", "iam:DeleteRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:TagRole", "iam:UntagRole", "iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary"], admin_roles),
        statement("AdministerGliderPolicies", ["iam:CreatePolicy", "iam:DeletePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:TagPolicy", "iam:UntagPolicy"], [iam + "policy/cloud-glider-sandbox-*", iam + "policy/CloudGlider*"]),
        statement("ReadIdentityInventory", ["iam:ListRoles", "iam:ListPolicies", "iam:GetAccountSummary"], "*"),
        statement("ValidateGliderPolicies", ["access-analyzer:ValidatePolicy", "iam:SimulateCustomPolicy"], "*"),
        statement("SimulateGliderRolePolicies", "iam:SimulatePrincipalPolicy", admin_roles))
    resources = {}
    sso_path = "aws-reserved/sso.amazonaws.com/" + (sso_region + "/" if sso_region != "us-east-1" else "")
    for name in ROLES:
        permission_set = config["security_permission_set"] if name == "GliderSecurityAdmin" else config["access_permission_set"]
        trust = document([
            {"Sid": "ExistingUserMigration", "Effect": "Allow", "Principal": {"AWS": iam + "user/" + config["migration_user"]}, "Action": "sts:AssumeRole"},
            {"Sid": "IdentityCenterAccess", "Effect": "Allow", "Principal": {"AWS": iam + "root"}, "Action": "sts:AssumeRole", "Condition": {"ArnLike": {"aws:PrincipalArn": iam + "role/" + sso_path + "AWSReservedSSO_" + permission_set + "_*"}}},
        ])
        length = len(json.dumps(policies[name], separators=(",", ":")))
        if length > 10240:
            raise ValueError(name + " exceeds aggregate role inline-policy quota")
        resources[name] = {"Type": "AWS::IAM::Role", "Properties": {
            "RoleName": role_names[name], "Path": "/cloud-glider/operator/", "MaxSessionDuration": 3600,
            "Description": name + "; additive GliderManager migration, existing access retained",
            "AssumeRolePolicyDocument": trust, "Policies": [{"PolicyName": name, "PolicyDocument": policies[name]}],
            "Tags": [{"Key": "project", "Value": "cloud-glider"}, {"Key": "environment", "Value": env}, {"Key": "purpose", "Value": "operator-access"}],
        }}
    # The preserved builder policy plus its smoke path exceeds the inline quota.
    # Split that existing document into independently named managed policies,
    # preserving every statement, and keep only the smoke supplement inline.
    chunks, chunk = [], []
    for stmt in image_policy["Statement"]:
        if len(json.dumps(document(chunk + [stmt]), separators=(",", ":"))) > 6000:
            if not chunk:
                raise ValueError("An image-build statement exceeds the managed-policy quota")
            chunks.append(chunk)
            chunk = []
        chunk.append(copy.deepcopy(stmt))
    if chunk:
        chunks.append(chunk)
    image_properties = resources["GliderImageBuilder"]["Properties"]
    image_properties["Policies"] = [{"PolicyName": "GliderImageSmoke", "PolicyDocument": document(smoke)}]
    image_properties["ManagedPolicyArns"] = []
    for index, chunk in enumerate(chunks, 1):
        logical = "ImageBuildPolicy" + str(index)
        resources[logical] = {"Type": "AWS::IAM::ManagedPolicy", "Properties": {
            "ManagedPolicyName": base + "-operator-image-build-" + str(index),
            "PolicyDocument": document(chunk)}}
        image_properties["ManagedPolicyArns"].append({"Ref": logical})
    policies["GliderImageBuilderSmoke"] = document(smoke)
    # Add an assumption-only group grant; do not edit any existing grant or membership.
    resources["MigrationAssumeRoles"] = {"Type": "AWS::IAM::GroupPolicy", "Properties": {
        "GroupName": config["migration_group"], "PolicyName": "CloudGliderOperatorRoleMigration",
        "PolicyDocument": document([statement("AssumeReplacementGliderRoles", "sts:AssumeRole", list(role_arns.values()))])}}
    template = {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Additive Cloud Glider human operator identities; retains GliderManager access", "Resources": resources,
                "Outputs": {name: {"Value": {"Fn::GetAtt": [name, "Arn"]}} for name in ROLES}}
    permission_sets = {
        config["access_permission_set"]: document([statement("AssumeRoutineGliderRoles", "sts:AssumeRole", [arn for name, arn in role_arns.items() if name != "GliderSecurityAdmin"])]),
        config["security_permission_set"]: document([statement("AssumeGliderSecurityAdmin", "sts:AssumeRole", role_arns["GliderSecurityAdmin"])])}
    return {"template": template, "policies": policies, "role_arns": role_arns, "permission_sets": permission_sets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-build-policy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = render(json.loads(args.config.read_text()), json.loads(args.image_build_policy.read_text()))
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.output_dir, 0o700)
    for name, content in result.items():
        path = args.output_dir / (name + ".json")
        path.write_text(json.dumps(content, indent=2) + "\n")
        os.chmod(path, 0o600)
    print(json.dumps({"roles": list(ROLES), "template_bytes": len(json.dumps(result["template"])), "output": str(args.output_dir)}))


if __name__ == "__main__":
    main()
