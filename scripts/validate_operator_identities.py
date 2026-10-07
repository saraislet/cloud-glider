#!/usr/bin/env python3
"""Validate role assumption and non-mutating authorization alongside old access."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


FALSE_CONDITION = "attribute_exists(PK) AND attribute_not_exists(PK)"
PROFILE_NAMES = {
    "GliderObserver": "glider-observe", "GliderOperator": "glider-operate",
    "GliderStateRecovery": "glider-recover", "GliderExceptionalCleanup": "glider-cleanup",
    "GliderImageBuilder": "glider-build", "GliderRelease": "glider-release",
    "GliderSecurityAdmin": "glider-security",
}


def validate_smoke_console_scope(iam, policies, *, account, region):
    """Evaluate the complete proposed builder policy with positive/negative inputs."""
    documents = [json.dumps(policies[name]) for name in ("GliderImageBuilder", "GliderImageBuilderSmoke")]
    resource = "arn:aws:ec2:" + region + ":" + account + ":instance/i-0123456789abcdef0"
    tags = {"project": "cloud-glider", "purpose": "image-smoke-test"}
    cases = [
        ("owned smoke instance", resource, tags, True),
        ("wrong project", resource, {**tags, "project": "unrelated"}, False),
        ("wrong purpose", resource, {**tags, "purpose": "image-build"}, False),
        ("generation compute", resource, {**tags, "purpose": "generation-compute"}, False),
        ("missing project", resource, {"purpose": tags["purpose"]}, False),
        ("missing purpose", resource, {"project": tags["project"]}, False),
        ("missing both tags", resource, {}, False),
        ("other account", resource.replace(":" + account + ":", ":000011112222:"), tags, False),
        ("other Region", resource.replace(":" + region + ":", ":us-east-1:"), tags, False),
        ("volume resource", resource.replace(":instance/i-", ":volume/vol-"), tags, False),
    ]
    results = []
    for label, arn, values, allowed in cases:
        response = iam.simulate_custom_policy(PolicyInputList=documents,
            ActionNames=["ec2:GetConsoleOutput"], ResourceArns=[arn],
            ContextEntries=[{"ContextKeyName": "ec2:ResourceTag/" + key,
                             "ContextKeyValues": [value], "ContextKeyType": "string"}
                            for key, value in values.items()])
        evaluations = response["EvaluationResults"]
        if len(evaluations) != 1 or (evaluations[0]["EvalDecision"] == "allowed") != allowed:
            raise ValueError("Unexpected console scope decision for " + label + ": " + json.dumps(evaluations))
        results.append({"case": label, "decision": evaluations[0]["EvalDecision"]})
    return results


def main():
    import boto3
    from botocore.exceptions import ClientError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--rendered-dir", type=Path, required=True)
    parser.add_argument("--source-profile", default="default")
    parser.add_argument("--task-profiles", action="store_true", help="Use the configured task profiles (SSO path) instead of direct assumption")
    parser.add_argument("--artifact-key", help="Known existing, nonsecret artifact to read without changing it")
    parser.add_argument("--simulate-smoke-console", action="store_true", help="Check proposed console-read scope using IAM simulation; does not prove live deployment")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    role_arns = json.loads((args.rendered_dir / "role_arns.json").read_text())
    region, account = config["region"], config["account_id"]
    original = boto3.Session(profile_name=args.source_profile, region_name=region)
    old = original.client("sts").get_caller_identity()
    if old["Account"] != account:
        raise SystemExit("Wrong source account")
    receipt = {"at": datetime.now(timezone.utc).isoformat(), "source": old,
               "path": "configured-task-profiles" if args.task_profiles else "migration-source-assume-role",
               "roles": {}, "operational_workflows": "NOT_EXERCISED; retain existing access"}
    failed = False

    def record(name, check, operation, expected_codes=None):
        nonlocal failed
        try:
            operation()
            if expected_codes:
                raise AssertionError("Expected a non-mutating authorization result")
            receipt["roles"][name]["checks"].append({"check": check, "result": "PASSED"})
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            passed = bool(expected_codes and code in expected_codes)
            reasons = exc.response.get("CancellationReasons", [])
            if code == "TransactionCanceledException":
                passed = passed and [r.get("Code") for r in reasons] == ["ConditionalCheckFailed"]
            entry = {"check": check, "result": "PASSED" if passed else "FAILED", "code": code}
            if reasons:
                entry["cancellation_reasons"] = reasons
            receipt["roles"][name]["checks"].append(entry)
            failed |= not passed
        except Exception as exc:
            receipt["roles"][name]["checks"].append({"check": check, "result": "FAILED", "error": str(exc)})
            failed = True

    def update_probe(ddb, table, pk, sk, attribute):
        return lambda: ddb.transact_write_items(TransactItems=[{"Update": {
            "TableName": table, "Key": {"PK": {"S": pk}, "SK": {"S": sk}},
            "UpdateExpression": "SET #a = :v", "ExpressionAttributeNames": {"#a": attribute},
            "ExpressionAttributeValues": {":v": {"BOOL": True}}, "ConditionExpression": FALSE_CONDITION}}])

    sessions = {}
    try:
        if args.task_profiles:
            receipt["identity_center_sources"] = {}
            for profile, permission_set in (("glider-access", config["access_permission_set"]),
                                            ("glider-security-access", config["security_permission_set"])):
                identity = boto3.Session(profile_name=profile, region_name=region).client("sts").get_caller_identity()
                if identity["Account"] != account or "/AWSReservedSSO_" + permission_set + "_" not in identity["Arn"]:
                    raise ValueError("Wrong Identity Center source identity: " + profile)
                receipt["identity_center_sources"][profile] = identity
        for name, arn in role_arns.items():
            receipt["roles"][name] = {"arn": arn, "checks": []}
            try:
                if args.task_profiles:
                    session = boto3.Session(profile_name=PROFILE_NAMES[name], region_name=region)
                else:
                    creds = original.client("sts").assume_role(RoleArn=arn, RoleSessionName="anna-sarai-identity-validation", DurationSeconds=3600)["Credentials"]
                    session = boto3.Session(aws_access_key_id=creds["AccessKeyId"], aws_secret_access_key=creds["SecretAccessKey"], aws_session_token=creds["SessionToken"], region_name=region)
                identity = session.client("sts").get_caller_identity()
                if identity["Account"] != account or "/" + arn.rsplit("/", 1)[1] + "/" not in identity["Arn"]:
                    raise ValueError("Wrong assumed identity")
                receipt["roles"][name]["identity"] = identity
                sessions[name] = session
                record(name, "EC2 metadata read", lambda: session.client("ec2").describe_instances(MaxResults=5))
            except Exception as exc:
                receipt["roles"][name]["checks"].append({"check": "actual role assumption", "result": "FAILED", "error": str(exc)})
                failed = True
        table = "cloud-glider-sandbox-state"
        generation = "cloud-glider-sandbox-generations"
        audit = "cloud-glider-sandbox-audit"
        for name, session in sessions.items():
            if name == "GliderImageBuilder":
                continue
            ddb = session.client("dynamodb")
            record(name, "CONTROL read", lambda: ddb.get_item(TableName=table, Key={"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}}, ConsistentRead=True))
        if "GliderObserver" in sessions:
            session = sessions["GliderObserver"]
            ddb, logs = session.client("dynamodb"), session.client("logs")
            for t in (table, generation, audit):
                record("GliderObserver", t + " scan", lambda t=t: ddb.scan(TableName=t, Limit=1))
                stream = ddb.describe_table(TableName=t)["Table"].get("LatestStreamArn")
                if stream:
                    def read_stream(stream=stream):
                        streams = session.client("dynamodbstreams")
                        description = streams.describe_stream(StreamArn=stream, Limit=1)["StreamDescription"]
                        shards = description.get("Shards", [])
                        if shards:
                            iterator = streams.get_shard_iterator(StreamArn=stream, ShardId=shards[0]["ShardId"], ShardIteratorType="LATEST")["ShardIterator"]
                            streams.get_records(ShardIterator=iterator, Limit=1)
                    record("GliderObserver", t + " stream describe/iterator/records", read_stream)
            record("GliderObserver", "bootstrap log filtering", lambda: logs.filter_log_events(logGroupName="/aws/lambda/cloud-glider-sandbox-bootstrap", limit=1))
            record("GliderObserver", "EC2 quota read", lambda: session.client("service-quotas").get_service_quota(ServiceCode="ec2", QuotaCode="L-1216C47A"))
            bucket = "cloud-glider-sandbox-" + account + "-" + region + "-artifacts"
            record("GliderObserver", "artifact listing", lambda: session.client("s3").list_objects_v2(Bucket=bucket, MaxKeys=1))
            def read_artifact():
                response = session.client("s3").get_object(Bucket=bucket, Key=args.artifact_key, Range="bytes=0-0")
                response["Body"].read()
                response["Body"].close()
            if args.artifact_key:
                record("GliderObserver", "artifact object read", read_artifact)
            else:
                receipt["roles"]["GliderObserver"]["checks"].append({"check": "artifact object read", "result": "NOT_TESTED", "reason": "No known artifact key supplied"})
            record("GliderObserver", "CONTROL mutation denied", update_probe(ddb, table, "CONTROL", "GLOBAL", "start_requested"), {"AccessDeniedException"})
        probes = [("GliderOperator", table, "CONTROL", "GLOBAL", "stop_requested"),
                  ("GliderStateRecovery", generation, "GEN#r", "SUBMISSION", "settled"),
                  ("GliderRelease", table, "CONTROL", "GLOBAL", "updated_at")]
        for name, t, pk, sk, attr in probes:
            if name in sessions:
                record(name, "conditionally failing write authorization", update_probe(sessions[name].client("dynamodb"), t, pk, sk, attr), {"TransactionCanceledException"})
        if "GliderStateRecovery" in sessions:
            ddb = sessions["GliderStateRecovery"].client("dynamodb")
            record("GliderStateRecovery", "generation ownership write denied", update_probe(ddb, generation, "GEN#r", "NODE", "owner"), {"AccessDeniedException"})
            record("GliderStateRecovery", "hold deletion authorization", lambda: ddb.transact_write_items(TransactItems=[{"Delete": {"TableName": table, "Key": {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}}, "ConditionExpression": FALSE_CONDITION}}]), {"TransactionCanceledException"})
        if "GliderExceptionalCleanup" in sessions:
            record("GliderExceptionalCleanup", "generation repair denied", update_probe(sessions["GliderExceptionalCleanup"].client("dynamodb"), generation, "GEN#r", "SUBMISSION", "settled"), {"AccessDeniedException"})
            ec2 = sessions["GliderExceptionalCleanup"].client("ec2")
            matches = [i for page in ec2.get_paginator("describe_instances").paginate(Filters=[{"Name": "tag:project", "Values": ["cloud-glider"]}, {"Name": "tag:environment", "Values": ["sandbox"]}, {"Name": "tag:purpose", "Values": ["generation-compute"]}, {"Name": "instance-state-name", "Values": ["pending", "running", "stopped"]}]) for r in page["Reservations"] for i in r["Instances"]]
            if matches:
                record("GliderExceptionalCleanup", "termination DryRun", lambda: ec2.terminate_instances(InstanceIds=[matches[0]["InstanceId"]], DryRun=True), {"DryRunOperation"})
            else:
                receipt["roles"]["GliderExceptionalCleanup"]["checks"].append({"check": "termination DryRun", "result": "NOT_TESTED", "reason": "No live matching generation; no test instance launched"})
        if "GliderImageBuilder" in sessions:
            policy = json.loads((args.rendered_dir / "policies.json").read_text())["GliderImageBuilder"]
            inputs = next(s for s in policy["Statement"] if s.get("Sid") == "LaunchWithApprovedInputs")["Resource"]
            source_image = next(x.rsplit("/", 1)[1] for x in inputs if ":image/" in x)
            subnet = next(x.rsplit("/", 1)[1] for x in inputs if ":subnet/" in x)
            group = next(x.rsplit("/", 1)[1] for x in inputs if ":security-group/" in x)
            profile = next(s for s in policy["Statement"] if s.get("Sid") == "ReadBuilderProfile")["Resource"].rsplit("/", 1)[1]
            tags = [{"Key": "project", "Value": "cloud-glider"}, {"Key": "purpose", "Value": "image-build"}]
            record("GliderImageBuilder", "approved builder launch DryRun", lambda: sessions["GliderImageBuilder"].client("ec2").run_instances(
                ImageId=source_image, InstanceType="t4g.micro", MinCount=1, MaxCount=1, DryRun=True,
                IamInstanceProfile={"Name": profile}, MetadataOptions={"HttpTokens": "required"},
                NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": subnet, "Groups": [group], "AssociatePublicIpAddress": True, "DeleteOnTermination": True}],
                BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 8, "VolumeType": "gp3", "Iops": 3000, "Encrypted": True, "DeleteOnTermination": True}}],
                TagSpecifications=[{"ResourceType": resource, "Tags": tags} for resource in ("instance", "volume", "network-interface")]), {"DryRunOperation"})
        if "GliderRelease" in sessions:
            record("GliderRelease", "template validation", lambda: sessions["GliderRelease"].client("cloudformation").validate_template(TemplateBody=json.dumps({"Resources": {"Handle": {"Type": "AWS::CloudFormation::WaitConditionHandle"}}})))
        if "GliderSecurityAdmin" in sessions:
            expected = json.loads((args.rendered_dir / "policies.json").read_text())
            aa = sessions["GliderSecurityAdmin"].client("accessanalyzer")
            for name, policy in expected.items():
                def validate(policy=policy):
                    findings = [f for page in aa.get_paginator("validate_policy").paginate(policyDocument=json.dumps(policy), policyType="IDENTITY_POLICY") for f in page["findings"] if f["findingType"] == "ERROR"]
                    if findings:
                        raise ValueError(json.dumps(findings))
                record("GliderSecurityAdmin", "policy validation " + name, validate)
            if args.simulate_smoke_console:
                def console_scope():
                    receipt["smoke_console_simulation"] = validate_smoke_console_scope(
                        sessions["GliderSecurityAdmin"].client("iam"), expected, account=account, region=region)
                record("GliderSecurityAdmin", "proposed builder console scope simulation", console_scope)
        record(next(iter(receipt["roles"])), "existing source still works", lambda: original.client("sts").get_caller_identity())
    finally:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, default=str) + "\n")
        args.receipt.chmod(0o600)
    print(json.dumps({"authorization_checks": "FAILED" if failed else "PASSED", "receipt": str(args.receipt), "existing_access": "RETAIN", "operational_workflows": receipt["operational_workflows"]}))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
