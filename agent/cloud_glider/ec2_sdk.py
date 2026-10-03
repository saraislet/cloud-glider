"""Direct EC2 lifecycle using the current persistent SDK and lifecycle fences."""

from __future__ import annotations
import hashlib
import json
import re
from typing import Any
import boto3
from botocore.config import Config
from .agent import SafetyViolation, TransientFailure
from .aws_sdk import AwsSdkGateway, _av, _ddb_item, _imds


def _filter(value):
    name, values = value.split(",Values=", 1)
    return {"Name": name.removeprefix("Name="), "Values": values.split(",")}


class Ec2SdkGateway(AwsSdkGateway):
    def __init__(self, config, *, session=None):
        super().__init__(config, session=session)
        self._clients["cloudwatch"] = (
            session or boto3.Session(region_name=self.region)
        ).client(
            "cloudwatch",
            region_name=self.region,
            config=Config(
                connect_timeout=2,
                read_timeout=5,
                max_pool_connections=10,
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )
        self._template_data = None

    def check_capacity(self, max_live_generations: int) -> None:
        instances = self._pages(
            "describe_instances",
            Filters=[
                _filter("Name=tag:project,Values=cloud-glider"),
                _filter(f"Name=tag:environment,Values={self.config.environment}"),
                _filter(
                    "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down"
                ),
            ],
        )
        live = sum(
            (
                len(reservation.get("Instances", []))
                for page in instances
                for reservation in page.get("Reservations", [])
            )
        )
        if live >= max_live_generations:
            raise TransientFailure(
                f"live generation ceiling reached ({live}/{max_live_generations})"
            )
        offerings = self._call(
            "ec2",
            "describe_instance_type_offerings",
            LocationType="region",
            Filters=[_filter("Name=instance-type,Values=t4g.micro")],
        )
        if not offerings.get("InstanceTypeOfferings"):
            raise TransientFailure("t4g.micro is not offered in this region")
        quota = self._call(
            "service-quotas",
            "get_service_quota",
            ServiceCode="ec2",
            QuotaCode="L-1216C47A",
        )
        quota_value = float(quota.get("Quota", {}).get("Value", 0))
        account = self._pages(
            "describe_instances",
            Filters=[
                _filter(
                    "Name=instance-state-name,Values=pending,running,stopping,shutting-down"
                )
            ],
        )
        active = [
            i
            for page in account
            for r in page.get("Reservations", [])
            for i in r.get("Instances", [])
        ]
        types = sorted({i["InstanceType"] for i in active})
        cpus = {}
        for offset in range(0, len(types), 100):
            result = self._call(
                "ec2",
                "describe_instance_types",
                InstanceTypes=[*types[offset : offset + 100]],
            )
            cpus.update(
                {
                    i["InstanceType"]: i["VCpuInfo"]["DefaultVCpus"]
                    for i in result.get("InstanceTypes", [])
                }
            )
        if any((i["InstanceType"] not in cpus for i in active)):
            raise TransientFailure("account capacity lookup incomplete")
        if quota_value < sum((cpus[i["InstanceType"]] for i in active)) + 2:
            raise TransientFailure(
                "standard-instance vCPU quota has insufficient account headroom"
            )
        if self._template_data is None:
            raise TransientFailure("launch template has not been verified")
        subnet_id = self._template_data["NetworkInterfaces"][0]["SubnetId"]
        subnets = self._call("ec2", "describe_subnets", SubnetIds=[subnet_id]).get(
            "Subnets", []
        )
        if len(subnets) != 1 or subnets[0].get("AvailableIpAddressCount", 0) < 1:
            raise TransientFailure("approved subnet lacks IP capacity")

    def describe_instance(self, instance_id: str) -> dict | None:
        result = self._call(
            "ec2", "describe_instances", allow_failure=True, InstanceIds=[instance_id]
        )
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        instances = [
            i for r in result.get("Reservations", []) for i in r.get("Instances", [])
        ]
        if len(instances) != 1 or instances[0]["InstanceId"] != instance_id:
            raise TransientFailure("exact instance lookup was incomplete")
        return self._instance(instances[0])

    @staticmethod
    def _instance(instance: dict) -> dict:
        instance = dict(instance)
        instance["Tags"] = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
        tags = instance["Tags"]
        instance["LaunchTemplate"] = {
            "LaunchTemplateId": tags.get("aws:ec2launchtemplate:id", ""),
            "Version": tags.get("aws:ec2launchtemplate:version", ""),
        }
        return instance

    def verify_launch_template(self, control: dict) -> None:
        result = self._call(
            "ec2",
            "describe_launch_template_versions",
            LaunchTemplateId=control["launch_template_id"],
            Versions=[control["launch_template_version"]],
        )
        versions = result.get("LaunchTemplateVersions", [])
        if (
            len(versions) != 1
            or versions[0].get("LaunchTemplateId") != control["launch_template_id"]
            or str(versions[0].get("VersionNumber"))
            != control["launch_template_version"]
        ):
            raise SafetyViolation(
                "LAUNCH_TEMPLATE_IDENTITY_MISMATCH", "exact numeric version is missing"
            )
        data = versions[0]["LaunchTemplateData"]
        if template_digest(data) != control["launch_template_sha256"]:
            raise SafetyViolation(
                "LAUNCH_TEMPLATE_DIGEST_MISMATCH", "approved template data differs"
            )
        metadata = data.get("MetadataOptions", {})
        networks = data.get("NetworkInterfaces", [])
        disks = data.get("BlockDeviceMappings", [])
        profile = data.get("IamInstanceProfile", {})
        profile_name = f"cloud-glider-{self.config.environment}-agent"
        if (
            data.get("InstanceType") != "t4g.micro"
            or metadata.get("HttpTokens") != "required"
            or metadata.get("HttpEndpoint") != "enabled"
            or (metadata.get("HttpPutResponseHopLimit") != 1)
            or (metadata.get("InstanceMetadataTags") != "enabled")
            or (len(networks) != 1)
            or (networks[0].get("DeviceIndex") != 0)
            or (networks[0].get("AssociatePublicIpAddress") is not True)
            or (networks[0].get("DeleteOnTermination") is not True)
            or (not networks[0].get("SubnetId"))
            or (len(networks[0].get("Groups", [])) != 1)
            or (profile != {"Name": profile_name})
            or (len(disks) != 1)
            or (disks[0].get("Ebs", {}).get("Encrypted") is not True)
            or (disks[0]["Ebs"].get("DeleteOnTermination") is not True)
            or (disks[0]["Ebs"].get("VolumeType") != "gp3")
            or (not 8 <= disks[0]["Ebs"].get("VolumeSize", 0) <= 16)
            or data.get("InstanceMarketOptions")
            or data.get("DisableApiTermination")
            or data.get("Monitoring", {}).get("Enabled", False)
        ):
            raise SafetyViolation(
                "LAUNCH_TEMPLATE_POLICY_MISMATCH", "template violates sandbox policy"
            )
        image = self._call("ec2", "describe_images", ImageIds=[data["ImageId"]]).get(
            "Images", []
        )
        if (
            len(image) != 1
            or image[0].get("Architecture") != "arm64"
            or image[0].get("State") != "available"
            or (image[0].get("RootDeviceType") != "ebs")
            or (image[0].get("RootDeviceName") != disks[0]["DeviceName"])
        ):
            raise TransientFailure("approved AMI unavailable or differs from template")
        self._template_data = data

    def verify_instance(self, instance: dict, specification: dict) -> None:
        if self._template_data is None:
            raise TransientFailure("launch template has not been verified")
        data = self._template_data
        if (
            specification["generation"] != "000000"
            and instance.get("ClientToken") != specification["client_token"]
        ):
            raise SafetyViolation(
                "INSTANCE_SUBMISSION_MISMATCH",
                "instance does not match deterministic launch request",
            )
        expected_template = {
            "LaunchTemplateId": specification["launch_template_id"],
            "Version": specification["launch_template_version"],
        }
        tags = instance.get("Tags", {})
        network = data["NetworkInterfaces"][0]
        metadata = instance.get("MetadataOptions", {})
        if (
            instance.get("LaunchTemplate") != expected_template
            or any((tags.get(k) != v for k, v in specification["tags"].items()))
            or instance.get("ImageId") != data["ImageId"]
            or (instance.get("InstanceType") != "t4g.micro")
            or (instance.get("Architecture") != "arm64")
            or (instance.get("SubnetId") != network["SubnetId"])
            or (
                sorted((g["GroupId"] for g in instance.get("SecurityGroups", [])))
                != sorted(network["Groups"])
            )
            or (
                instance.get("IamInstanceProfile", {}).get("Arn")
                != f"arn:aws:iam::{self.account_id}:instance-profile/cloud-glider/{data['IamInstanceProfile']['Name']}"
            )
            or (metadata.get("HttpTokens") != "required")
            or (metadata.get("HttpEndpoint") != "enabled")
            or (metadata.get("HttpPutResponseHopLimit") != 1)
            or (metadata.get("InstanceMetadataTags") != "enabled")
        ):
            raise SafetyViolation(
                "INSTANCE_IDENTITY_MISMATCH",
                "EC2 identity or launch configuration differs",
            )

    def find_successor(self, specification: dict) -> dict | None:
        result = self._pages(
            "describe_instances",
            Filters=[
                _filter("Name=tag:project,Values=cloud-glider"),
                _filter(f"Name=tag:environment,Values={self.config.environment}"),
                _filter(f"Name=tag:generation,Values={specification['generation']}"),
                _filter(
                    f"Name=tag:bootstrap-request-id,Values={self.config.request_id}"
                ),
            ],
        )
        instances = [
            self._instance(i)
            for page in result
            for r in page.get("Reservations", [])
            for i in r.get("Instances", [])
        ]
        if len(instances) > 1:
            raise SafetyViolation(
                "SUCCESSOR_IDENTITY_CONFLICT", "multiple instances claim one generation"
            )
        if instances and instances[0]["State"]["Name"] in (
            "shutting-down",
            "terminated",
            "stopping",
            "stopped",
        ):
            raise TransientFailure(
                "recorded successor is unavailable; operator inspection required"
            )
        return instances[0] if instances else None

    @staticmethod
    def _run_request(specification: dict) -> dict:
        tags = [
            {"Key": k, "Value": v} for k, v in sorted(specification["tags"].items())
        ]
        return {
            "MinCount": 1,
            "MaxCount": 1,
            "LaunchTemplate": {
                "LaunchTemplateId": specification["launch_template_id"],
                "Version": specification["launch_template_version"],
            },
            "ClientToken": specification["client_token"],
            "TagSpecifications": [
                {"ResourceType": kind, "Tags": tags} for kind in ("instance", "volume")
            ],
        }

    def claim_submission(self, specification):
        digest = template_digest(specification)
        table = self.config.state_table_name
        self._call(
            "dynamodb",
            "transact_write_items",
            TransactItems=[
                self._lifecycle_check(provisioning=True),
                {
                    "ConditionCheck": {
                        "TableName": table,
                        "Key": {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                        "ConditionExpression": "stop_requested = :no AND cleanup_requested = :no",
                        "ExpressionAttributeValues": {":no": _av(False)},
                    }
                },
                {
                    "ConditionCheck": {
                        "TableName": table,
                        "Key": {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}},
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": table,
                        "Item": _ddb_item(
                            {
                                "PK": "LOCK",
                                "SK": "PROVISIONING",
                                "request_id": self.config.request_id,
                                "token": specification["client_token"],
                                "specification_sha256": digest,
                            }
                        ),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": self.config.generation_table_name,
                        "Item": _ddb_item(
                            {
                                "PK": "GEN#" + specification["generation"],
                                "SK": "SUBMISSION",
                                "request_id": self.config.request_id,
                                "token": specification["client_token"],
                                "specification_sha256": digest,
                            }
                        ),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
            ],
        )

    def _record_ec2_submission(self, specification, instance_id):
        self._fenced_write(
            {
                "Put": {
                    "TableName": self.config.generation_table_name,
                    "Item": _ddb_item(
                        {
                            "PK": "GEN#" + specification["generation"],
                            "SK": "RESOURCE#" + instance_id,
                            "request_id": self.config.request_id,
                            "instance_id": instance_id,
                            "client_token": specification["client_token"],
                            "launch_template_id": specification["launch_template_id"],
                            "launch_template_version": specification[
                                "launch_template_version"
                            ],
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(PK) OR (request_id = :id AND instance_id = :instance AND client_token = :token)",
                    "ExpressionAttributeValues": {
                        ":id": _av(self.config.request_id),
                        ":instance": _av(instance_id),
                        ":token": _av(specification["client_token"]),
                    },
                }
            },
            conflict_code="SUBMISSION_IDENTITY_CONFLICT",
        )

    def reconcile_ec2_submission(self, specification, instance):
        self.verify_instance(instance, specification)
        marker = self._get("LOCK", "PROVISIONING")
        if marker and (
            marker.get("request_id") != self.config.request_id
            or marker.get("token") != specification["client_token"]
            or marker.get("specification_sha256") != template_digest(specification)
        ):
            raise TransientFailure(
                "ambiguous provisioning marker requires operator inspection"
            )
        self._record_ec2_submission(specification, instance["InstanceId"])
        if marker:
            self._end_provisioning(specification["client_token"])

    def run_instance(self, specification: dict) -> str:
        result = self._call("ec2", "run_instances", **self._run_request(specification))
        instances = result.get("Instances", [])
        if len(instances) != 1:
            raise TransientFailure("EC2 submission result is ambiguous; inspect intent")
        instance = self._instance(instances[0])
        if not re.fullmatch("i-[0-9a-f]{17}", instance.get("InstanceId", "")):
            raise TransientFailure("EC2 returned an invalid instance ID")
        self._record_ec2_submission(specification, instance["InstanceId"])
        self._end_provisioning(specification["client_token"])
        return instance["InstanceId"]

    def dry_run_instance(self, specification: dict) -> None:
        request = {**self._run_request(specification), "DryRun": True}
        result = self._call("ec2", "run_instances", allow_failure=True, **request)
        if not result.get("_returncode") or result.get("_code") != "DryRunOperation":
            raise TransientFailure(
                "EC2 continuation dry run did not confirm authorization"
            )

    def _status_alarm(self, generation: str, instance_id: str) -> tuple[str, bool]:
        name = f"cloud-glider-{self.config.environment}-gen-{generation}-status-check"
        result = self._call("cloudwatch", "describe_alarms", AlarmNames=[name])
        alarms = result.get("MetricAlarms", [])
        if result.get("CompositeAlarms") or len(alarms) > 1:
            raise SafetyViolation(
                "STATUS_ALARM_IDENTITY_CONFLICT", "alarm identity is ambiguous"
            )
        if alarms and (
            alarms[0].get("AlarmName") != name
            or alarms[0].get("Namespace") != "AWS/EC2"
            or alarms[0].get("MetricName") != "StatusCheckFailed"
            or (
                alarms[0].get("Dimensions")
                != [{"Name": "InstanceId", "Value": instance_id}]
            )
        ):
            raise SafetyViolation(
                "STATUS_ALARM_IDENTITY_CONFLICT",
                "existing alarm belongs to another resource",
            )
        return (name, bool(alarms))

    def ensure_status_alarm(self, generation: str, instance_id: str) -> None:
        name, _ = self._status_alarm(generation, instance_id)
        self._call(
            "cloudwatch",
            "put_metric_alarm",
            **{
                "AlarmName": name,
                "AlarmDescription": f"Generation {generation} EC2 system or instance check failed.",
                "Namespace": "AWS/EC2",
                "MetricName": "StatusCheckFailed",
                "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                "Statistic": "Maximum",
                "Period": 60,
                "EvaluationPeriods": 2,
                "DatapointsToAlarm": 2,
                "Threshold": 1,
                "ComparisonOperator": "GreaterThanOrEqualToThreshold",
                "TreatMissingData": "missing",
                "AlarmActions": [self.config.operational_alerts_topic_arn],
            },
        )

    def delete_status_alarm(self, generation: str, instance_id: str) -> None:
        name, exists = self._status_alarm(generation, instance_id)
        if exists:
            self._call("cloudwatch", "delete_alarms", AlarmNames=[name])

    def mark_retirement_completed(self, current: dict) -> None:
        self._fenced_write(
            {
                "Update": {
                    "TableName": self.config.state_table_name,
                    "Key": {"PK": {"S": "CURRENT"}, "SK": {"S": "GLOBAL"}},
                    "UpdateExpression": "SET retirement_completed = :done",
                    "ConditionExpression": "request_id = :id AND instance_id = :instance AND generation = :generation AND predecessor_instance_id = :parent AND handoff_token = :token AND retirement_authorized = :done",
                    "ExpressionAttributeValues": {
                        ":id": _av(self.config.request_id),
                        ":instance": _av(current["instance_id"]),
                        ":generation": _av(current["generation"]),
                        ":parent": _av(current["predecessor_instance_id"]),
                        ":token": _av(current["handoff_token"]),
                        ":done": _av(True),
                    },
                }
            }
        )

    def terminate_instance(self, instance_id: str) -> None:
        result = self._call("ec2", "terminate_instances", InstanceIds=[instance_id])
        changes = result.get("TerminatingInstances", [])
        if len(changes) != 1 or changes[0].get("InstanceId") != instance_id:
            raise TransientFailure(
                "termination result is ambiguous; preserve retirement state"
            )

    def handoff(self, expected: dict, successor: dict, audit: dict) -> bool:
        table = self.config.state_table_name
        expected = dict(expected)
        expected["control_identity"] = {
            k: v
            for k, v in expected["control_identity"].items()
            if k
            not in (
                "request_id",
                "propagation_enabled",
                "cleanup_requested",
                "cleanup_status",
            )
        }
        names = {
            f"#c{i}": k for i, k in enumerate(sorted(expected["control_identity"]))
        }
        values = {
            f":c{i}": _av(expected["control_identity"][k])
            for i, k in enumerate(sorted(expected["control_identity"]))
        }
        control_condition = " AND ".join((f"#c{i} = :c{i}" for i in range(len(names))))
        control_condition += " AND stop_requested = :no AND cleanup_requested = :no"
        values[":no"] = _av(False)
        candidate = expected["candidate"]
        candidate_names = {f"#s{i}": k for i, k in enumerate(sorted(candidate))}
        candidate_values = {
            f":s{i}": _av(candidate[k]) for i, k in enumerate(sorted(candidate))
        }
        candidate_condition = " AND ".join(
            (f"#s{i} = :s{i}" for i in range(len(candidate_names)))
        )
        transaction = [
            self._lifecycle_check(provisioning=True),
            {
                "ConditionCheck": {
                    "TableName": table,
                    "Key": {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                    "ConditionExpression": control_condition,
                    "ExpressionAttributeNames": names,
                    "ExpressionAttributeValues": values,
                }
            },
            {
                "ConditionCheck": {
                    "TableName": table,
                    "Key": {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}},
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
            {
                "ConditionCheck": {
                    "TableName": table,
                    "Key": {"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}},
                    "ConditionExpression": "lease_owner = :owner AND expires_at >= :now",
                    "ExpressionAttributeValues": {
                        ":owner": {"S": expected["lease_owner"]},
                        ":now": _av(expected["lease_now"]),
                    },
                }
            },
            {
                "Put": {
                    "TableName": table,
                    "Item": _ddb_item({"PK": "CURRENT", "SK": "GLOBAL", **successor}),
                    "ConditionExpression": "generation = :generation AND instance_id = :instance AND #status = :current AND launch_template_id = :lt AND launch_template_version = :version AND request_id = :id",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":id": _av(self.config.request_id),
                        ":generation": _av(expected["generation"]),
                        ":instance": _av(expected["instance_id"]),
                        ":current": _av("CURRENT"),
                        ":lt": _av(expected["launch_template_id"]),
                        ":version": _av(expected["launch_template_version"]),
                    },
                }
            },
            {
                "ConditionCheck": {
                    "TableName": self.config.generation_table_name,
                    "Key": {
                        "PK": {"S": f"GEN#{successor['generation']}"},
                        "SK": {"S": "STATE"},
                    },
                    "ConditionExpression": candidate_condition,
                    "ExpressionAttributeNames": candidate_names,
                    "ExpressionAttributeValues": candidate_values,
                }
            },
            {
                "Put": {
                    "TableName": table,
                    "Item": _ddb_item(
                        {
                            "PK": "AUDIT#PROPAGATION",
                            "SK": "LATEST_HANDOFF",
                            "request_id": self.config.request_id,
                            "schema_version": "1.0",
                            "category": "PROPAGATION",
                            "action": "CONDITIONAL_HANDOFF",
                            "resource_type": "CURRENT_RECORD",
                            "resource_id": "CURRENT/GLOBAL",
                            "result": "APPLIED",
                            "environment": self.config.environment,
                            **audit,
                        }
                    ),
                }
            },
        ]
        result = self._call(
            "dynamodb",
            "transact_write_items",
            allow_failure=True,
            TransactItems=transaction,
        )
        if result.get("_code") == "TransactionCanceledException":
            return False
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        return True

    def write_heartbeat(self, state: dict[str, Any]) -> None:
        item = _ddb_item({"PK": f"GEN#{state['generation']}", "SK": "STATE", **state})
        values = {
            ":instance": {"S": state["instance_id"]},
            ":id": {"S": self.config.request_id},
        }
        self._fenced_write(
            {
                "Put": {
                    "TableName": self.config.generation_table_name,
                    "Item": item,
                    "ConditionExpression": "attribute_not_exists(PK) OR (request_id = :id AND instance_id = :instance AND #status <> :error)",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {**values, ":error": {"S": "ERROR"}},
                }
            },
            conflict_code="GENERATION_IDENTITY_CONFLICT",
        )


def template_digest(data: dict) -> str:
    """Hash the exact EC2-returned LaunchTemplateData (not response metadata)."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_instance_config(static: dict) -> dict:
    static = dict(static)
    static.pop("propagation_backend", None)
    instance_id = _imds("meta-data/instance-id")
    document = json.loads(_imds("dynamic/instance-identity/document"))
    client = boto3.Session(region_name=document["region"]).client(
        "ec2",
        config=Config(
            connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1}
        ),
    )
    try:
        result = client.describe_instances(InstanceIds=[instance_id])
    finally:
        client.close()
    instances = [
        i for r in result.get("Reservations", []) for i in r.get("Instances", [])
    ]
    if len(instances) != 1 or instances[0].get("InstanceId") != instance_id:
        raise TransientFailure("own EC2 identity lookup incomplete")
    instance = Ec2SdkGateway._instance(instances[0])
    tags = instance["Tags"]
    return {
        **static,
        "instance_id": instance_id,
        "request_id": tags.get("bootstrap-request-id", ""),
        "generation": tags.get("generation", ""),
        "predecessor_instance_id": tags.get("predecessor-instance-id", ""),
        "handoff_token": tags.get("handoff-token", ""),
        "launch_template_id": instance["LaunchTemplate"]["LaunchTemplateId"],
        "launch_template_version": instance["LaunchTemplate"]["Version"],
    }
