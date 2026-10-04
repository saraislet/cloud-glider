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

from .daemon import DaemonConfig, SafetyViolation, TransientFailure

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
    if value is None:
        return {"NULL": True}
    if isinstance(value, dict):
        return {"M": {key: _av(item) for key, item in value.items()}}
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
    def __init__(self, config: DaemonConfig, *, session=None, services=None):
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
                services
                or (
                    "dynamodb",
                    "cloudformation",
                    "ec2",
                    "service-quotas",
                    "s3",
                    "lambda",
                )
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
                "_cancellation_reasons": exc.response.get("CancellationReasons", []),
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
            TableName=(
                self.config.generation_table_name
                if pk.startswith("GEN#")
                else self.config.state_table_name
            ),
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
            ConsistentRead=True,
        )
        return _item(result.get("Item"))

    def read_control_and_hold(self) -> tuple[dict[str, Any], bool]:
        control, hold, _ = self._read_control_snapshot()
        return control, hold

    def read_cycle_snapshot(self) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        return self._read_control_snapshot(include_current=True)

    def _read_control_snapshot(self, *, include_current=False):
        keys = [("CONTROL", "GLOBAL"), ("HOLD", "ACTIVE"), ("BOOTSTRAP", "REQUEST")]
        if include_current:
            keys.append(("CURRENT", "GLOBAL"))
        result = self._call(
            "dynamodb",
            "transact_get_items",
            TransactItems=[
                {
                    "Get": {
                        "TableName": self.config.state_table_name,
                        "Key": {"PK": {"S": pk}, "SK": {"S": sk}},
                    }
                }
                for pk, sk in keys
            ],
        )
        if len(result.get("Responses", [])) != len(keys):
            raise TransientFailure("control transaction was incomplete")
        records = [_item(entry.get("Item")) for entry in result["Responses"]]
        control, hold, lifecycle = records[:3]
        if "propagation_enabled" in control or lifecycle.get("schema_version") != "2":
            raise SafetyViolation(
                "LIFECYCLE_MIGRATION_REQUIRED", "legacy or missing lifecycle control"
            )
        if lifecycle.get("request_id") != self.config.request_id:
            raise TransientFailure("stale chain identity")
        pending_stop = any(
            (
                control.get(field) is True
                for field in ("stop_requested", "cleanup_requested")
            )
        )
        for field in (
            "request_id",
            "propagation_enabled",
            "cleanup_requested",
            "cleanup_status",
        ):
            if field not in lifecycle:
                raise SafetyViolation(
                    "LIFECYCLE_SCHEMA_INVALID", "missing lifecycle gate"
                )
            control[field] = lifecycle[field]
        if pending_stop:
            control["propagation_enabled"] = False
        return control, bool(hold), records[3] if include_current else {}

    def _lifecycle_check(self, *, provisioning=False):
        values = {
            ":id": {"S": self.config.request_id},
            ":no": {"BOOL": False},
            ":idle": {"S": "IDLE"},
            ":complete": {"S": "COMPLETE"},
        }
        condition = "request_id = :id AND cleanup_requested = :no AND cleanup_status IN (:idle, :complete)"
        if provisioning:
            condition += " AND propagation_enabled = :yes"
            values[":yes"] = {"BOOL": True}
        return {
            "ConditionCheck": {
                "TableName": self.config.state_table_name,
                "Key": {"PK": {"S": "BOOTSTRAP"}, "SK": {"S": "REQUEST"}},
                "ConditionExpression": condition,
                "ExpressionAttributeValues": values,
            }
        }

    def _fenced_write(self, operation, conflict_code=None, *, return_conflict=False):
        result = self._call(
            "dynamodb",
            "transact_write_items",
            allow_failure=True,
            TransactItems=[self._lifecycle_check(), operation],
        )
        if result.get("_code") == "TransactionCanceledException":
            reasons = result.get("_cancellation_reasons", [])
            codes = tuple(reason.get("Code") or "None" for reason in reasons)
            # Cancellation alone is not proof of lost ownership. Contention,
            # throttling, and missing/ambiguous reasons defer this attempt.
            confirmed_condition = (
                len(codes) == 2
                and all(code in ("None", "ConditionalCheckFailed") for code in codes)
                and "ConditionalCheckFailed" in codes
            )
            if not confirmed_condition:
                raise TransientFailure(
                    "transaction cancelled; retry after rereading state"
                )
            if return_conflict:
                return False
            lifecycle = self._get("BOOTSTRAP", "REQUEST")
            if (
                codes == ("None", "ConditionalCheckFailed")
                and conflict_code
                and lifecycle.get("request_id") == self.config.request_id
                and lifecycle.get("cleanup_requested") is False
                and lifecycle.get("cleanup_status") in ("IDLE", "COMPLETE")
            ):
                raise SafetyViolation(
                    conflict_code, "coordination state condition changed"
                )
            raise TransientFailure("lifecycle or state condition changed")
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        return True

    def _begin_provisioning(self, specification):
        # Non-expiring marker: a lost AWS response requires reconciliation.
        token = specification["client_token"]
        table = self.config.state_table_name
        transaction = [
            self._lifecycle_check(provisioning=True),
            {
                "ConditionCheck": {
                    "TableName": table,
                    "Key": {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                    "ConditionExpression": "stop_requested = :no AND cleanup_requested = :no",
                    "ExpressionAttributeValues": {":no": {"BOOL": False}},
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
                            "token": token,
                            "stack_name": specification["stack_name"],
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
        ]
        self._call("dynamodb", "transact_write_items", TransactItems=transaction)
        return token

    def reconcile_submission(self, specification, stack):
        marker = self._get("LOCK", "PROVISIONING")
        if not marker:
            return
        if (
            marker.get("request_id") != self.config.request_id
            or marker.get("token") != specification["client_token"]
            or marker.get("stack_name") != specification["stack_name"]
            or (stack.get("RoleARN") != specification["role_arn"])
            or (
                stack.get("Tags", {}).get("bootstrap-request-id")
                != self.config.request_id
            )
            or (
                stack.get("StackStatus")
                not in ("CREATE_IN_PROGRESS", "CREATE_COMPLETE")
            )
        ):
            raise TransientFailure(
                "ambiguous provisioning marker requires operator inspection"
            )
        self._record_submission(specification, stack["StackId"])
        self._end_provisioning(specification["client_token"])

    def _record_submission(self, specification, stack_id):
        # Persist the exact stack ARN before releasing the submission marker.
        item = _ddb_item(
            {
                "PK": f"GEN#{specification['generation']}",
                "SK": "RESOURCE#" + stack_id,
                "request_id": self.config.request_id,
                "stack_id": stack_id,
            }
        )
        self._call(
            "dynamodb",
            "put_item",
            TableName=self.config.generation_table_name,
            Item=item,
            ConditionExpression="attribute_not_exists(PK) OR request_id = :id",
            ExpressionAttributeValues={":id": {"S": self.config.request_id}},
        )

    def _end_provisioning(self, token):
        self._call(
            "dynamodb",
            "delete_item",
            TableName=self.config.state_table_name,
            Key={"PK": {"S": "LOCK"}, "SK": {"S": "PROVISIONING"}},
            ConditionExpression="request_id = :id AND #token = :token",
            ExpressionAttributeNames={"#token": "token"},
            ExpressionAttributeValues={
                ":id": {"S": self.config.request_id},
                ":token": {"S": token},
            },
        )

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
        operation = {
            "Update": {
                "TableName": self.config.state_table_name,
                "Key": {"PK": {"S": "CURRENT"}, "SK": {"S": "GLOBAL"}},
                "UpdateExpression": "SET " + ", ".join(assignments),
                "ConditionExpression": "#status = :uninitialized",
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": values,
            }
        }
        return self._fenced_write(operation, return_conflict=True)

    def write_heartbeat(self, state: dict[str, Any]) -> None:
        item = _ddb_item({"PK": f"GEN#{state['generation']}", "SK": "STATE", **state})
        values = {
            ":stack": {"S": state["stack_id"]},
            ":instance": {"S": state["instance_id"]},
        }
        self._fenced_write(
            {
                "Put": {
                    "TableName": self.config.generation_table_name,
                    "Item": item,
                    "ConditionExpression": "attribute_not_exists(PK) OR (stack_id = :stack AND instance_id = :instance AND #status <> :error)",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {**values, ":error": {"S": "ERROR"}},
                }
            },
            conflict_code="GENERATION_IDENTITY_CONFLICT",
        )

    def acquire_lease(
        self, owner: str, generation: str, now: int, expires: int
    ) -> bool:
        return self._fenced_write(
            {
                "Update": {
                    "TableName": self.config.state_table_name,
                    "Key": {"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}},
                    "UpdateExpression": "SET lease_owner = :owner, generation = :generation, expires_at = :expires, request_id = :id",
                    "ConditionExpression": "attribute_not_exists(PK) OR expires_at < :now OR lease_owner = :owner",
                    "ExpressionAttributeValues": {
                        ":owner": {"S": owner},
                        ":generation": {"S": generation},
                        ":expires": {"N": str(expires)},
                        ":now": {"N": str(now)},
                        ":id": {"S": self.config.request_id},
                    },
                }
            },
            return_conflict=True,
        )

    def renew_lease(self, owner: str, expires: int) -> None:
        self._fenced_write(
            {
                "Update": {
                    "TableName": self.config.state_table_name,
                    "Key": {"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}},
                    "UpdateExpression": "SET expires_at = :expires",
                    "ConditionExpression": "lease_owner = :owner AND request_id = :id",
                    "ExpressionAttributeValues": {
                        ":owner": {"S": owner},
                        ":expires": {"N": str(expires)},
                        ":id": {"S": self.config.request_id},
                    },
                }
            },
            conflict_code="LEASE_OWNERSHIP_LOST",
        )

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
        token = self._begin_provisioning(specification)
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
        self._record_submission(specification, result["StackId"])
        self._end_provisioning(token)
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

    def handoff(
        self, expected: dict[str, Any], successor: dict[str, Any], audit: dict[str, Any]
    ) -> bool:
        table = self.config.state_table_name
        current_values = {
            ":request_id": {"S": self.config.request_id},
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
                "SK": "LATEST_HANDOFF",
                "schema_version": "1.0",
                "category": "PROPAGATION",
                "request_id": self.config.request_id,
                "action": "CONDITIONAL_HANDOFF",
                "resource_type": "CURRENT_RECORD",
                "resource_id": "CURRENT/GLOBAL",
                "result": "APPLIED",
                "environment": self.config.environment,
                **audit,
            }
        )
        control_identity = {
            key: value
            for key, value in expected["control_identity"].items()
            if key
            not in (
                "request_id",
                "propagation_enabled",
                "cleanup_requested",
                "cleanup_status",
            )
        }
        control_names = {
            f"#c{index}": key for index, key in enumerate(sorted(control_identity))
        }
        control_values = {
            f":c{index}": _av(control_identity[key])
            for index, key in enumerate(sorted(control_identity))
        }
        control_condition = " AND ".join(
            f"#c{index} = :c{index}" for index in range(len(control_names))
        )
        control_condition += (
            " AND stop_requested = :operator_no AND cleanup_requested = :operator_no"
        )
        control_values[":operator_no"] = {"BOOL": False}
        transaction = [
            self._lifecycle_check(provisioning=True),
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
                    "UpdateExpression": "SET request_id = :request_id, generation = :next_generation, stack_id = :next_stack, instance_id = :next_instance, #status = :current, handoff_token = :token, updated_at = :updated",
                    "ConditionExpression": "request_id = :request_id AND generation = :generation AND stack_id = :stack AND instance_id = :instance AND #status = :current",
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
                    "TableName": self.config.generation_table_name,
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
                "request_id": self.config.request_id,
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
