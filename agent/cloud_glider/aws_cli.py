"""AWS CLI v2 gateway for the Cloud Glider state machine."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

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


class AwsCliGateway:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.instance_id = _imds("meta-data/instance-id")
        document = json.loads(_imds("dynamic/instance-identity/document"))
        self.region = document["region"]
        self.account_id = document["accountId"]

    def _run(self, *arguments: str, allow_failure: bool = False) -> dict[str, Any]:
        command = ["aws", *arguments, "--region", self.region, "--output", "json", "--no-cli-pager"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode:
            if allow_failure:
                return {"_error": completed.stderr.strip(), "_returncode": completed.returncode}
            raise TransientFailure(f"AWS CLI failed ({arguments[0]}): {completed.stderr.strip()}")
        return json.loads(completed.stdout) if completed.stdout.strip() else {}

    def _get(self, pk: str, sk: str) -> dict[str, Any]:
        result = self._run(
            "dynamodb", "get-item", "--table-name", self.config.state_table_name,
            "--key", json.dumps({"PK": {"S": pk}, "SK": {"S": sk}}), "--consistent-read"
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
        result = self._run("dynamodb", "batch-get-item", "--request-items", json.dumps(request))
        items = [_item(value) for value in result.get("Responses", {}).get(self.config.state_table_name, [])]
        control = next((value for value in items if value.get("PK") == "CONTROL"), {})
        hold = any(value.get("PK") == "HOLD" and value.get("SK") == "ACTIVE" for value in items)
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
        result = self._run(
            "dynamodb", "update-item", "--table-name", self.config.state_table_name,
            "--key", json.dumps({"PK": {"S": "CURRENT"}, "SK": {"S": "GLOBAL"}}),
            "--update-expression", "SET " + ", ".join(assignments),
            "--condition-expression", "#status = :uninitialized",
            "--expression-attribute-names", json.dumps(names),
            "--expression-attribute-values", json.dumps(values), allow_failure=True,
        )
        if "ConditionalCheckFailedException" in result.get("_error", ""):
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
        result = self._run(
            "dynamodb", "put-item", "--table-name", self.config.state_table_name,
            "--item", json.dumps(item),
            "--condition-expression", "attribute_not_exists(PK) OR (stack_id = :stack AND instance_id = :instance AND #status <> :error)",
            "--expression-attribute-names", json.dumps({"#status": "status"}),
            "--expression-attribute-values", json.dumps({**values, ":error": {"S": "ERROR"}}), allow_failure=True,
        )
        if "ConditionalCheckFailedException" in result.get("_error", ""):
            raise SafetyViolation("GENERATION_IDENTITY_CONFLICT", "generation heartbeat identity changed")
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])

    def acquire_lease(self, owner: str, generation: str, now: int, expires: int) -> bool:
        result = self._run(
            "dynamodb", "update-item", "--table-name", self.config.state_table_name,
            "--key", json.dumps({"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}}),
            "--update-expression", "SET lease_owner = :owner, generation = :generation, expires_at = :expires",
            "--condition-expression", "attribute_not_exists(PK) OR expires_at < :now OR lease_owner = :owner",
            "--expression-attribute-values", json.dumps({
                ":owner": {"S": owner}, ":generation": {"S": generation},
                ":expires": {"N": str(expires)}, ":now": {"N": str(now)},
            }), allow_failure=True,
        )
        if "ConditionalCheckFailedException" in result.get("_error", ""):
            return False
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        return True

    def renew_lease(self, owner: str, expires: int) -> None:
        result = self._run(
            "dynamodb", "update-item", "--table-name", self.config.state_table_name,
            "--key", json.dumps({"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}}),
            "--update-expression", "SET expires_at = :expires",
            "--condition-expression", "lease_owner = :owner",
            "--expression-attribute-values", json.dumps({":owner": {"S": owner}, ":expires": {"N": str(expires)}}),
            allow_failure=True,
        )
        if "ConditionalCheckFailedException" in result.get("_error", ""):
            raise SafetyViolation("LEASE_OWNERSHIP_LOST", "propagation lease changed owners")
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])

    def release_lease(self, owner: str) -> None:
        result = self._run(
            "dynamodb", "delete-item", "--table-name", self.config.state_table_name,
            "--key", json.dumps({"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}}),
            "--condition-expression", "lease_owner = :owner",
            "--expression-attribute-values", json.dumps({":owner": {"S": owner}}), allow_failure=True,
        )
        if result.get("_returncode") and "ConditionalCheckFailedException" not in result.get("_error", ""):
            raise TransientFailure(result["_error"])

    @staticmethod
    def _stack(result: dict[str, Any]) -> dict[str, Any]:
        stack = result["Stacks"][0]
        stack["Parameters"] = {
            item["ParameterKey"]: item.get("ParameterValue", "") for item in stack.get("Parameters", [])
        }
        stack["Tags"] = {item["Key"]: item["Value"] for item in stack.get("Tags", [])}
        return stack

    def describe_stack(self, stack_id_or_name: str) -> dict[str, Any] | None:
        result = self._run("cloudformation", "describe-stacks", "--stack-name", stack_id_or_name, allow_failure=True)
        if result.get("_returncode"):
            error = result.get("_error", "")
            if "does not exist" in error:
                return None
            raise TransientFailure(error)
        return self._stack(result)

    def stack_instance_id(self, stack_id: str) -> str:
        result = self._run("cloudformation", "describe-stack-resource", "--stack-name", stack_id, "--logical-resource-id", "GenerationInstance")
        instance_id = result.get("StackResourceDetail", {}).get("PhysicalResourceId")
        if not instance_id:
            raise TransientFailure("generation instance resource is not yet available")
        return instance_id

    def verify_template_artifact(self, control: dict[str, Any]) -> None:
        with tempfile.TemporaryDirectory(prefix="cloud-glider-template-") as directory:
            target = Path(directory) / "generation.yaml"
            self._run(
                "s3api", "get-object", "--bucket", control["template_s3_bucket"],
                "--key", control["template_s3_key"], "--version-id", control["template_s3_version_id"], str(target)
            )
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != control["template_sha256"]:
                raise SafetyViolation("TEMPLATE_DIGEST_MISMATCH", "approved template object digest differs from CONTROL")

    def _template_url(self, specification: dict[str, Any]) -> str:
        bucket = urllib.parse.quote(specification["template_bucket"], safe="")
        key = urllib.parse.quote(specification["template_key"], safe="/")
        version = urllib.parse.quote(specification["template_version_id"], safe="")
        return f"https://{bucket}.s3.{self.region}.amazonaws.com/{key}?versionId={version}"

    @staticmethod
    def _parameters(specification: dict[str, Any]) -> str:
        return json.dumps([
            {"ParameterKey": key, "ParameterValue": value}
            for key, value in sorted(specification["parameters"].items())
        ])

    @staticmethod
    def _tags(specification: dict[str, Any]) -> str:
        tags = dict(specification["tags"])
        owner = specification["parameters"].get("Owner")
        if owner:
            tags["owner"] = owner
        return json.dumps([{"Key": key, "Value": value} for key, value in sorted(tags.items())])

    def create_stack(self, specification: dict[str, Any]) -> str:
        result = self._run(
            "cloudformation", "create-stack", "--stack-name", specification["stack_name"],
            "--template-url", self._template_url(specification), "--parameters", self._parameters(specification),
            "--role-arn", specification["role_arn"], "--on-failure", "DO_NOTHING",
            "--client-request-token", specification["client_token"], "--tags", self._tags(specification),
        )
        return result["StackId"]

    def read_generation_state(self, generation: str) -> dict[str, Any] | None:
        result = self._get(f"GEN#{generation}", "STATE")
        return result or None

    def check_capacity(self, max_live_generations: int) -> None:
        instances = self._run(
            "ec2", "describe-instances", "--filters",
            "Name=tag:project,Values=cloud-glider",
            f"Name=tag:environment,Values={self.config.environment}",
            "Name=instance-state-name,Values=pending,running,stopping,stopped",
        )
        live = sum(len(reservation.get("Instances", [])) for reservation in instances.get("Reservations", []))
        if live >= max_live_generations:
            raise TransientFailure(f"live generation ceiling reached ({live}/{max_live_generations})")
        offerings = self._run(
            "ec2", "describe-instance-type-offerings", "--location-type", "region",
            "--filters", "Name=instance-type,Values=t4g.micro",
        )
        if not offerings.get("InstanceTypeOfferings"):
            raise TransientFailure("t4g.micro is not offered in this region")
        quota = self._run("service-quotas", "get-service-quota", "--service-code", "ec2", "--quota-code", "L-1216C47A")
        quota_value = float(quota.get("Quota", {}).get("Value", 0))
        if quota_value < (live + 1) * 2:
            raise TransientFailure("standard-instance vCPU quota has insufficient headroom")

    def create_preflight(self, specification: dict[str, Any]) -> str:
        result = self._run(
            "cloudformation", "create-change-set", "--stack-name", specification["stack_name"],
            "--change-set-name", specification["change_set_name"], "--change-set-type", "CREATE",
            "--template-url", self._template_url(specification), "--parameters", self._parameters(specification),
            "--role-arn", specification["role_arn"], "--client-token", specification["client_token"],
            "--description", "Cloud Glider unexecuted continuation preflight", "--tags", self._tags(specification),
        )
        return result["Id"]

    def describe_change_set(self, change_set_id: str) -> dict[str, Any]:
        return self._run("cloudformation", "describe-change-set", "--change-set-name", change_set_id)

    def discard_preflight(self, change_set_id: str, stack_name: str, role_arn: str) -> None:
        self._run("cloudformation", "delete-change-set", "--change-set-name", change_set_id, allow_failure=True)
        self._run("cloudformation", "delete-stack", "--stack-name", stack_name, "--role-arn", role_arn, allow_failure=True)

    def handoff(self, expected: dict[str, Any], successor: dict[str, Any], audit: dict[str, Any]) -> bool:
        table = self.config.state_table_name
        current_values = {
            ":generation": {"S": expected["generation"]},
            ":stack": {"S": expected["stack_id"]},
            ":instance": {"S": expected["instance_id"]},
            ":current": {"S": "CURRENT"},
        }
        successor_values = {f":v{index}": _av(value) for index, value in enumerate(successor.values())}
        successor_names = {f"#n{index}": key for index, key in enumerate(successor)}
        assignments = [f"#n{index} = :v{index}" for index in range(len(successor))]
        audit_item = _ddb_item({
            "PK": "AUDIT#PROPAGATION", "SK": f"EVENT#{audit['occurred_at']}#{audit['event_id']}",
            "schema_version": "1.0", "category": "PROPAGATION", "action": "CONDITIONAL_HANDOFF",
            "resource_type": "CURRENT_RECORD", "resource_id": "CURRENT/GLOBAL", "result": "APPLIED",
            "environment": self.config.environment, **audit,
        })
        control_names = {f"#c{index}": key for index, key in enumerate(sorted(expected["control_identity"]))}
        control_values = {
            f":c{index}": _av(expected["control_identity"][key])
            for index, key in enumerate(sorted(expected["control_identity"]))
        }
        control_condition = " AND ".join(
            f"#c{index} = :c{index}" for index in range(len(control_names))
        )
        transaction = [
            {"ConditionCheck": {"TableName": table, "Key": {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                "ConditionExpression": control_condition,
                "ExpressionAttributeNames": control_names,
                "ExpressionAttributeValues": control_values}},
            {"ConditionCheck": {"TableName": table, "Key": {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}},
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}},
            {"ConditionCheck": {"TableName": table, "Key": {"PK": {"S": "LOCK"}, "SK": {"S": "PROPAGATION"}},
                "ConditionExpression": "lease_owner = :owner",
                "ExpressionAttributeValues": {":owner": {"S": expected["lease_owner"]}}}},
            {"Update": {"TableName": table, "Key": {"PK": {"S": "CURRENT"}, "SK": {"S": "GLOBAL"}},
                "UpdateExpression": "SET generation = :next_generation, stack_id = :next_stack, instance_id = :next_instance, #status = :current, handoff_token = :token, updated_at = :updated",
                "ConditionExpression": "generation = :generation AND stack_id = :stack AND instance_id = :instance AND #status = :current",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {**current_values, ":next_generation": {"S": successor["generation"]},
                    ":next_stack": {"S": successor["stack_id"]}, ":next_instance": {"S": successor["instance_id"]},
                    ":token": {"S": successor["handoff_token"]}, ":updated": {"S": successor["updated_at"]}}}},
            {"Update": {"TableName": table,
                "Key": {"PK": {"S": f"GEN#{successor['generation']}"}, "SK": {"S": "STATE"}},
                "UpdateExpression": "SET " + ", ".join(assignments),
                "ConditionExpression": "stack_id = :expected_stack AND instance_id = :expected_instance AND handoff_token = :expected_token",
                "ExpressionAttributeNames": successor_names,
                "ExpressionAttributeValues": {**successor_values, ":expected_stack": {"S": successor["stack_id"]},
                    ":expected_instance": {"S": successor["instance_id"]}, ":expected_token": {"S": successor["handoff_token"]}}}},
            {"Put": {"TableName": table, "Item": audit_item,
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}},
        ]
        result = self._run("dynamodb", "transact-write-items", "--transact-items", json.dumps(transaction), allow_failure=True)
        if "TransactionCanceledException" in result.get("_error", ""):
            return False
        if result.get("_returncode"):
            raise TransientFailure(result["_error"])
        return True

    def delete_stack(self, stack_id: str, role_arn: str, token: str) -> None:
        self._run(
            "cloudformation", "delete-stack", "--stack-name", stack_id,
            "--role-arn", role_arn, "--client-request-token", token,
        )

    def invoke_hold(self, generation: str, error_code: str, correlation_id: str) -> None:
        payload = json.dumps({
            "generation": generation, "error_code": error_code, "correlation_id": correlation_id
        }).encode()
        with tempfile.NamedTemporaryFile(prefix="cloud-glider-hold-", suffix=".json") as response:
            result = self._run(
                "lambda", "invoke", "--function-name", self.config.emergency_hold_function_name,
                "--cli-binary-format", "raw-in-base64-out", "--payload", payload.decode(), response.name,
                allow_failure=True,
            )
            if result.get("_returncode") or result.get("FunctionError"):
                raise TransientFailure("failed to create emergency hold")
