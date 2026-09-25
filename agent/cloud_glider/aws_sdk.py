"""Persistent boto3 clients for the Cloud Glider state machine."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .agent import AgentConfig, SafetyViolation, TransientFailure


IMDS = "http://169.254.169.254/latest"


def _imds(path: str) -> str:
    token_request = urllib.request.Request(
        f"{IMDS}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    try:
        with urllib.request.urlopen(token_request, timeout=2) as response:
            token = response.read().decode()
        request = urllib.request.Request(
            f"{IMDS}/{path}", headers={"X-aws-ec2-metadata-token": token}
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.read().decode()
    except (OSError, urllib.error.URLError) as exc:
        raise TransientFailure(f"IMDSv2 lookup failed for {path}: {exc}") from exc


def _unmarshal(value: dict[str, Any]) -> Any:
    if "S" in value:
        return value["S"]
    if "N" in value:
        number = value["N"]
        return int(number) if "." not in number else float(number)
    if "BOOL" in value:
        return value["BOOL"]
    if "NULL" in value:
        return None
    if "L" in value:
        return [_unmarshal(item) for item in value["L"]]
    if "M" in value:
        return {key: _unmarshal(item) for key, item in value["M"].items()}
    raise ValueError(f"unsupported DynamoDB attribute: {value}")


def _item(raw: dict[str, Any] | None) -> dict[str, Any]:
    return {key: _unmarshal(value) for key, value in (raw or {}).items()}


def _av(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if isinstance(value, list):
        return {"L": [_av(item) for item in value]}
    return {"S": str(value)}


def _ddb_item(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _av(value) for key, value in values.items()}


class AwsSdkGateway:
    def __init__(self, config: AgentConfig, *, session=None):
        self.config = config
        self.instance_id = _imds("meta-data/instance-id")
        document = json.loads(_imds("dynamic/instance-identity/document"))
        self.region = document["region"]
        self.account_id = document["accountId"]

        # Construct clients once, before any future concurrent workers start.
        # Do not freeze credentials: the standard provider chain refreshes role credentials.
        session = session or boto3.Session(region_name=self.region)
        client_config = Config(
            connect_timeout=2,
            read_timeout=5,
            max_pool_connections=10,
            retries={"mode": "standard", "total_max_attempts": 1},
        )
        self._clients = {
            service: session.client(
                service, region_name=self.region, config=client_config
            )
            for service in (
                "dynamodb",
                "cloudformation",
                "ec2",
                "service-quotas",
                "s3",
                "lambda",
            )
        }

    def close(self) -> None:
        for client in self._clients.values():
            client.close()

    def _call(
        self,
        service: str,
        operation: str,
        *,
        allow_failure: bool = False,
        **parameters: Any,
    ) -> dict[str, Any]:
        try:
            return getattr(self._clients[service], operation)(**parameters)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            result = {
                "_code": error.get("Code", "Unknown"),
                "_error": str(exc),
                "_returncode": 1,
            }
            if allow_failure:
                return result
            raise TransientFailure(f"AWS {service}.{operation} failed: {exc}") from exc
        except BotoCoreError as exc:
            # Includes network timeouts and credential refresh failures. Never infer
            # absence, lost ownership, or successful mutation from a transport error.
            raise TransientFailure(f"AWS {service}.{operation} failed: {exc}") from exc

    def _pages(self, operation: str, **parameters: Any):
        while True:
            result = self._call("ec2", operation, **parameters)
            yield result
            token = result.get("NextToken")
            if not token:
                return
            parameters["NextToken"] = token

    def _get(self, pk: str, sk: str) -> dict[str, Any]:
        result = self._call(
            "dynamodb",
            "get_item",
            TableName=self.config.state_table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
            ConsistentRead=True,
        )
        return _item(result.get("Item"))

    def read_control_and_hold(self) -> tuple[dict[str, Any], bool]:
        request = {
            self.config.state_table_name: {
                "Keys": [
                    {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                    {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}},
                ],
                "ConsistentRead": True,
            }
        }
        result = self._call("dynamodb", "batch_get_item", RequestItems=request)
        items = [
            _item(value)
            for value in result.get("Responses", {}).get(
                self.config.state_table_name, []
            )
        ]
        control = next((value for value in items if value.get("PK") == "CONTROL"), {})
        hold = any(
            value.get("PK") == "HOLD" and value.get("SK") == "ACTIVE" for value in items
        )
        if result.get("UnprocessedKeys"):
            raise TransientFailure("control read was incomplete")
        return control, hold

    def read_current(self) -> dict[str, Any]:
        return self._get("CURRENT", "GLOBAL")

    def claim_initial_current(self, identity: dict[str, Any]) -> bool:
        names = {"#status": "status"}
        values = {":uninitialized": {"S": "UNINITIALIZED"}}
        assignments = []
        for index, (key, value) in enumerate(identity.items()):
            name = f"#n{index}"
            marker = f":v{index}"
            names[name] = key
            values[marker] = _av(value)
            assignments.append(f"{name} = {marker}")
        result = self._call(
            "dynamodb",
            "update_item",
            TableName=self.config.state_table_name,
            Key={"PK": {"S": "CURRENT"}, "SK": {"S": "GLOBAL"}},
            UpdateExpression="SET " + ", ".join(assignments),
            ConditionExpression="#status = :uninitialized",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            allow_failure=True,
        )
        if result.get("_code") == "ConditionalCheckFailedException":
            return False
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        return True

    def write_heartbeat(self, state: dict[str, Any]) -> None:
        item = _ddb_item({"PK": f"GEN#{state['generation']}", "SK": "STATE", **state})
        values = {
            ":stack": {"S": state["stack_id"]},
            ":instance": {"S": state["instance_id"]},
        }
        result = self._call(
            "dynamodb",
            "put_item",
            TableName=self.config.state_table_name,
            Item=item,
            ConditionExpression="attribute_not_exists(PK) OR (stack_id = :stack AND instance_id = :instance AND #status <> :error)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={**values, ":error": {"S": "ERROR"}},
            allow_failure=True,
        )
        if result.get("_code") == "ConditionalCheckFailedException":
            raise SafetyViolation(
                "GENERATION_IDENTITY_CONFLICT", "generation heartbeat identity changed"
            )
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])

    def acquire_lease(
        self, owner: str, generation: str, now: int, expires: int
    ) -> bool:
        result = self._call(
            "dynamodb",
            "update_item",
            TableName=self.config.state_table_name,
            Key={"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}},
            UpdateExpression="SET lease_owner = :owner, generation = :generation, expires_at = :expires",
            ConditionExpression="attribute_not_exists(PK) OR expires_at < :now OR lease_owner = :owner",
            ExpressionAttributeValues={
                ":owner": {"S": owner},
                ":generation": {"S": generation},
                ":expires": {"N": str(expires)},
                ":now": {"N": str(now)},
            },
            allow_failure=True,
        )
        if result.get("_code") == "ConditionalCheckFailedException":
            return False
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        return True

    def renew_lease(self, owner: str, expires: int) -> None:
        result = self._call(
            "dynamodb",
            "update_item",
            TableName=self.config.state_table_name,
            Key={"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}},
            UpdateExpression="SET expires_at = :expires",
            ConditionExpression="lease_owner = :owner",
            ExpressionAttributeValues={
                ":owner": {"S": owner},
                ":expires": {"N": str(expires)},
            },
            allow_failure=True,
        )
        if result.get("_code") == "ConditionalCheckFailedException":
            raise SafetyViolation(
                "LEASE_OWNERSHIP_LOST", "propagation lease changed owners"
            )
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])

    def release_lease(self, owner: str) -> None:
        result = self._call(
            "dynamodb",
            "delete_item",
            TableName=self.config.state_table_name,
            Key={"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}},
            ConditionExpression="lease_owner = :owner",
            ExpressionAttributeValues={":owner": {"S": owner}},
            allow_failure=True,
        )
        if (
            result.get("_returncode")
            and result.get("_code") != "ConditionalCheckFailedException"
        ):
            raise TransientFailure(result["_error"])

    @staticmethod
    def _stack(result: dict[str, Any]) -> dict[str, Any]:
        stack = result["Stacks"][0]
        stack["Parameters"] = {
            item["ParameterKey"]: item.get("ParameterValue", "")
            for item in stack.get("Parameters", [])
        }
        stack["Tags"] = {item["Key"]: item["Value"] for item in stack.get("Tags", [])}
        return stack

    def describe_stack(self, stack_id_or_name: str) -> dict[str, Any] | None:
        result = self._call(
            "cloudformation",
            "describe_stacks",
            StackName=stack_id_or_name,
            allow_failure=True,
        )
        if result.get("_returncode"):
            error = result.get("_error", "")
            if result.get("_code") == "ValidationError" and "does not exist" in error:
                return None
            raise TransientFailure(error)
        return self._stack(result)

    def stack_instance_id(self, stack_id: str) -> str:
        result = self._call(
            "cloudformation",
            "describe_stack_resource",
            StackName=stack_id,
            LogicalResourceId="GenerationInstance",
        )
        instance_id = result.get("StackResourceDetail", {}).get("PhysicalResourceId")
        if not instance_id:
            raise TransientFailure("generation instance resource is not yet available")
        return instance_id

    def verify_template_artifact(self, control: dict[str, Any]) -> None:
        result = self._call(
            "s3",
            "get_object",
            Bucket=control["template_s3_bucket"],
            Key=control["template_s3_key"],
            VersionId=control["template_s3_version_id"],
        )
        body = result["Body"]
        digest = hashlib.sha256()
        try:
            for chunk in body.iter_chunks(chunk_size=65536):
                digest.update(chunk)
        except (BotoCoreError, OSError) as exc:
            raise TransientFailure(f"template download incomplete: {exc}") from exc
        finally:
            body.close()
        if digest.hexdigest() != control["template_sha256"]:
            raise SafetyViolation(
                "TEMPLATE_DIGEST_MISMATCH",
                "approved template object digest differs from CONTROL",
            )

    def _template_url(self, specification: dict[str, Any]) -> str:
        bucket = urllib.parse.quote(specification["template_bucket"], safe="")
        key = urllib.parse.quote(specification["template_key"], safe="/")
        version = urllib.parse.quote(specification["template_version_id"], safe="")
        return (
            f"https://{bucket}.s3.{self.region}.amazonaws.com/{key}?versionId={version}"
        )

    @staticmethod
    def _parameters(specification: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"ParameterKey": key, "ParameterValue": value}
            for key, value in sorted(specification["parameters"].items())
        ]

    @staticmethod
    def _tags(specification: dict[str, Any]) -> list[dict[str, str]]:
        tags = dict(specification["tags"])
        owner = specification["parameters"].get("Owner")
        if owner:
            tags["owner"] = owner
        return [{"Key": key, "Value": value} for key, value in sorted(tags.items())]

    def create_stack(self, specification: dict[str, Any]) -> str:
        result = self._call(
            "cloudformation",
            "create_stack",
            StackName=specification["stack_name"],
            TemplateURL=self._template_url(specification),
            Parameters=self._parameters(specification),
            RoleARN=specification["role_arn"],
            OnFailure="DO_NOTHING",
            ClientRequestToken=specification["client_token"],
            Tags=self._tags(specification),
        )
        return result["StackId"]

    def read_generation_state(self, generation: str) -> dict[str, Any] | None:
        result = self._get(f"GEN#{generation}", "STATE")
        return result or None

    def check_capacity(self, max_live_generations: int) -> None:
        instances = self._pages(
            "describe_instances",
            Filters=[
                {"Name": "tag:project", "Values": ["cloud-glider"]},
                {"Name": "tag:environment", "Values": [self.config.environment]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ],
        )
        live = sum(
            len(reservation.get("Instances", []))
            for page in instances
            for reservation in page.get("Reservations", [])
        )
        if live >= max_live_generations:
            raise TransientFailure(
                f"live generation ceiling reached ({live}/{max_live_generations})"
            )
        offerings = self._pages(
            "describe_instance_type_offerings",
            LocationType="region",
            Filters=[{"Name": "instance-type", "Values": ["t4g.micro"]}],
        )
        if not any(page.get("InstanceTypeOfferings") for page in offerings):
            raise TransientFailure("t4g.micro is not offered in this region")
        quota = self._call(
            "service-quotas",
            "get_service_quota",
            ServiceCode="ec2",
            QuotaCode="L-1216C47A",
        )
        quota_value = float(quota.get("Quota", {}).get("Value", 0))
        if quota_value < (live + 1) * 2:
            raise TransientFailure(
                "standard-instance vCPU quota has insufficient headroom"
            )

    def create_preflight(self, specification: dict[str, Any]) -> str:
        result = self._call(
            "cloudformation",
            "create_change_set",
            StackName=specification["stack_name"],
            ChangeSetName=specification["change_set_name"],
            ChangeSetType="CREATE",
            TemplateURL=self._template_url(specification),
            Parameters=self._parameters(specification),
            RoleARN=specification["role_arn"],
            ClientToken=specification["client_token"],
            Description="Cloud Glider unexecuted continuation preflight",
            Tags=self._tags(specification),
        )
        return result["Id"]

    def describe_change_set(self, change_set_id: str) -> dict[str, Any]:
        return self._call(
            "cloudformation", "describe_change_set", ChangeSetName=change_set_id
        )

    def discard_preflight(
        self, change_set_id: str, stack_name: str, role_arn: str
    ) -> None:
        self._call(
            "cloudformation",
            "delete_change_set",
            ChangeSetName=change_set_id,
            allow_failure=True,
        )
        self._call(
            "cloudformation",
            "delete_stack",
            StackName=stack_name,
            RoleARN=role_arn,
            allow_failure=True,
        )

    def handoff(
        self, expected: dict[str, Any], successor: dict[str, Any], audit: dict[str, Any]
    ) -> bool:
        table = self.config.state_table_name
        current_values = {
            ":generation": {"S": expected["generation"]},
            ":stack": {"S": expected["stack_id"]},
            ":instance": {"S": expected["instance_id"]},
            ":current": {"S": "CURRENT"},
        }
        successor_values = {
            f":v{index}": _av(value) for index, value in enumerate(successor.values())
        }
        successor_names = {f"#n{index}": key for index, key in enumerate(successor)}
        assignments = [f"#n{index} = :v{index}" for index in range(len(successor))]
        audit_item = _ddb_item(
            {
                "PK": "AUDIT#PROPAGATION",
                "SK": f"EVENT#{audit['occurred_at']}#{audit['event_id']}",
                "schema_version": "1.0",
                "category": "PROPAGATION",
                "action": "CONDITIONAL_HANDOFF",
                "resource_type": "CURRENT_RECORD",
                "resource_id": "CURRENT/GLOBAL",
                "result": "APPLIED",
                "environment": self.config.environment,
                **audit,
            }
        )
        control_names = {
            f"#c{index}": key
            for index, key in enumerate(sorted(expected["control_identity"]))
        }
        control_values = {
            f":c{index}": _av(expected["control_identity"][key])
            for index, key in enumerate(sorted(expected["control_identity"]))
        }
        control_condition = " AND ".join(
            f"#c{index} = :c{index}" for index in range(len(control_names))
        )
        transaction = [
            {
                "ConditionCheck": {
                    "TableName": table,
                    "Key": {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                    "ConditionExpression": control_condition,
                    "ExpressionAttributeNames": control_names,
                    "ExpressionAttributeValues": control_values,
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
                    "ConditionExpression": "lease_owner = :owner",
                    "ExpressionAttributeValues": {
                        ":owner": {"S": expected["lease_owner"]}
                    },
                }
            },
            {
                "Update": {
                    "TableName": table,
                    "Key": {"PK": {"S": "CURRENT"}, "SK": {"S": "GLOBAL"}},
                    "UpdateExpression": "SET generation = :next_generation, stack_id = :next_stack, instance_id = :next_instance, #status = :current, handoff_token = :token, updated_at = :updated",
                    "ConditionExpression": "generation = :generation AND stack_id = :stack AND instance_id = :instance AND #status = :current",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        **current_values,
                        ":next_generation": {"S": successor["generation"]},
                        ":next_stack": {"S": successor["stack_id"]},
                        ":next_instance": {"S": successor["instance_id"]},
                        ":token": {"S": successor["handoff_token"]},
                        ":updated": {"S": successor["updated_at"]},
                    },
                }
            },
            {
                "Update": {
                    "TableName": table,
                    "Key": {
                        "PK": {"S": f"GEN#{successor['generation']}"},
                        "SK": {"S": "STATE"},
                    },
                    "UpdateExpression": "SET " + ", ".join(assignments),
                    "ConditionExpression": "stack_id = :expected_stack AND instance_id = :expected_instance AND handoff_token = :expected_token",
                    "ExpressionAttributeNames": successor_names,
                    "ExpressionAttributeValues": {
                        **successor_values,
                        ":expected_stack": {"S": successor["stack_id"]},
                        ":expected_instance": {"S": successor["instance_id"]},
                        ":expected_token": {"S": successor["handoff_token"]},
                    },
                }
            },
            {
                "Put": {
                    "TableName": table,
                    "Item": audit_item,
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
        ]
        result = self._call(
            "dynamodb",
            "transact_write_items",
            TransactItems=transaction,
            allow_failure=True,
        )
        if result.get("_code") == "TransactionCanceledException":
            return False
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        return True

    def delete_stack(self, stack_id: str, role_arn: str, token: str) -> None:
        self._call(
            "cloudformation",
            "delete_stack",
            StackName=stack_id,
            RoleARN=role_arn,
            ClientRequestToken=token,
        )

    def invoke_hold(
        self, generation: str, error_code: str, correlation_id: str
    ) -> None:
        payload = json.dumps(
            {
                "generation": generation,
                "error_code": error_code,
                "correlation_id": correlation_id,
            }
        ).encode()
        result = self._call(
            "lambda",
            "invoke",
            FunctionName=self.config.emergency_hold_function_name,
            Payload=payload,
            InvocationType="RequestResponse",
            allow_failure=True,
        )
        body = result.get("Payload")
        try:
            if body is not None:
                body.read()  # Drain successful responses so the connection can be reused.
        except (BotoCoreError, OSError) as exc:
            raise TransientFailure("emergency hold response incomplete") from exc
        finally:
            if body is not None:
                body.close()
        if (
            result.get("_returncode")
            or result.get("FunctionError")
            or result.get("StatusCode") != 200
        ):
            raise TransientFailure("failed to create emergency hold")
