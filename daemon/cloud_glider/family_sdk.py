"""AWS adapter for per-family, cycle-fenced EC2 lifecycle schema 3."""
from __future__ import annotations

import time
import hashlib
import json
from datetime import datetime, timezone
from .daemon import SafetyViolation, TransientFailure
from .aws_sdk import _av, _ddb_item, _item
from .ec2_sdk import Ec2SdkGateway
from .inherited import child_paths, specification, user_data, validate


class FamilySdkGateway(Ec2SdkGateway):
    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        self.settings = validate(config)
        self.configuration_sha256 = config.inherited_configuration["sha256"]
        self.first_ready_at = None
        self.confirmed_parent_termination = False

    def node_key(self, path, sk="NODE"):
        return {"PK": {"S": "GEN#" + path}, "SK": {"S": sk}}

    def invoke_hold(self, generation, error_code, correlation_id):
        return super().invoke_hold(generation, error_code, correlation_id, node_path=self.config.node_path, asynchronous=True)

    def get_node_item(self, path, sk):
        return self._get("GEN#" + path, sk)

    def read_node(self, path):
        return self.get_node_item(path, "NODE")

    def cycle_check(self, *, final=False, bookkeeping=False):
        check = self._lifecycle_check(provisioning=final)
        operation = check["ConditionCheck"]
        if bookkeeping:
            operation["ConditionExpression"] = "request_id = :id AND cleanup_status IN (:idle, :complete, :quiescing)"
            operation["ExpressionAttributeValues"] = {":id": _av(self.config.request_id),
                ":idle": _av("IDLE"), ":complete": _av("COMPLETE"), ":quiescing": _av("QUIESCING")}
        operation["ConditionExpression"] += " AND cycle_configuration_sha256 = :config"
        operation["ExpressionAttributeValues"][":config"] = _av(self.configuration_sha256)
        return check

    def transact(self, operations, *, final=False, bookkeeping=False):
        gates = [self.cycle_check(final=final, bookkeeping=bookkeeping)]
        if final:
            gates.extend([
                self.no_stop_check(self.config.node_path),
                {"ConditionCheck": {"TableName": self.config.state_table_name,
                    "Key": {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}},
                    "ConditionExpression": "attribute_not_exists(PK)"}},
                {"ConditionCheck": {"TableName": self.config.state_table_name,
                    "Key": {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                    "ConditionExpression": "stop_requested = :no AND cleanup_requested = :no",
                    "ExpressionAttributeValues": {":no": _av(False)}}}])
        self._call("dynamodb", "transact_write_items", TransactItems=gates + operations)

    def no_stop_check(self, path):
        return {"ConditionCheck": {"TableName": self.config.generation_table_name,
            "Key": self.node_key(path, "STOP"), "ConditionExpression": "attribute_not_exists(PK)"}}

    def poll_stopped(self):
        keys = [(self.config.state_table_name, {"PK": {"S": pk}, "SK": {"S": sk}})
                for pk, sk in (("CONTROL", "GLOBAL"), ("HOLD", "ACTIVE"), ("BOOTSTRAP", "REQUEST"))]
        paths = [self.config.node_path]
        if self.config.node_path != "r":
            paths.append(self.config.node_path[:-1])
        keys += [(self.config.generation_table_name, self.node_key(path, "STOP")) for path in paths]
        response = self._call("dynamodb", "transact_get_items",
            TransactItems=[{"Get": {"TableName": table, "Key": key}} for table, key in keys])
        if len(response.get("Responses", [])) != len(keys):
            raise TransientFailure("incomplete asynchronous controls")
        control, hold, cycle, *stops = [_item(r.get("Item")) for r in response["Responses"]]
        return (control.get("audit_table_name") != self.config.audit_table_name
            or bool(hold) or cycle.get("request_id") != self.config.request_id
            or cycle.get("cycle_configuration_sha256") != self.configuration_sha256
            or cycle.get("propagation_enabled") is not True
            or cycle.get("cleanup_requested") is not False
            or control.get("stop_requested") is not False
            or control.get("cleanup_requested") is not False
            or any(stop.get("request_id") == self.config.request_id for stop in stops))

    def stop_node(self, path):
        # Cycle-fenced cancellation is allowed during cleanup, never after reset.
        self.transact([{"Put": {"TableName": self.config.generation_table_name,
            "Item": _ddb_item({"PK": "GEN#" + path, "SK": "STOP", "request_id": self.config.request_id,
                            "configuration_sha256": self.configuration_sha256}),
            "ConditionExpression": "attribute_not_exists(PK) OR request_id = :id",
            "ExpressionAttributeValues": {":id": _av(self.config.request_id)}}}], bookkeeping=True)

    def own_spec(self):
        return specification(self.config, self.config.node_path,
                             self.config.predecessor_instance_id, self.config.handoff_token)

    def verify_family_self(self):
        self.config.validate()
        if self.instance_id != self.config.instance_id or self.account_id != self.settings["approved_account_id"] or self.region != self.settings["approved_region"]:
            raise SafetyViolation("SELF_IDENTITY_MISMATCH", "IMDS account/Region/instance differs")
        self.verify_launch_template(self.settings)
        self.verify_instance(self.describe_instance(self.instance_id), self.own_spec())

    def initialize_node(self):
        node = self.read_node(self.config.node_path)
        if node:
            if node.get("instance_id") != self.instance_id or node.get("configuration_sha256") != self.configuration_sha256:
                raise SafetyViolation("NODE_IDENTITY_CONFLICT", "node owned by another instance")
            return
        instance = self.describe_instance(self.instance_id)
        resource = {"PK": "GEN#" + self.config.node_path, "SK": "RESOURCE#" + self.instance_id,
                    "request_id": self.config.request_id, "instance_id": self.instance_id,
                    "client_token": instance.get("ClientToken", ""),
                    "launch_template_id": self.config.launch_template_id,
                    "launch_template_version": self.config.launch_template_version}
        record = {"PK": "GEN#" + self.config.node_path, "SK": "NODE",
                  "request_id": self.config.request_id, "instance_id": self.instance_id,
                  "configuration_sha256": self.configuration_sha256,
                  "started_at": int(time.time()),
                  "owner": self.instance_id if self.config.node_path == "r" else "",
                  "status": "OWNER" if self.config.node_path == "r" else "CANDIDATE"}
        self.transact([{"Put": {"TableName": self.config.generation_table_name,
            "Item": _ddb_item(record), "ConditionExpression": "attribute_not_exists(PK)"}},
            {"Put": {"TableName": self.config.generation_table_name, "Item": _ddb_item(resource)}}])

    def owner_check(self):
        return {"ConditionCheck": {"TableName": self.config.generation_table_name,
            "Key": self.node_key(self.config.node_path),
            "ConditionExpression": "#owner = :instance AND #status = :owner AND request_id = :id",
            "ExpressionAttributeNames": {"#owner": "owner", "#status": "status"},
            "ExpressionAttributeValues": {":instance": _av(self.instance_id),
                ":owner": _av("OWNER"), ":id": _av(self.config.request_id)}}}

    def child_request(self, spec):
        return {**self._run_request(spec), "UserData": user_data(self.config)}

    def dry_run_child(self, spec):
        result = self._call("ec2", "run_instances", allow_failure=True,
                            **self.child_request(spec), DryRun=True)
        if result.get("_code") != "DryRunOperation":
            raise TransientFailure("successor continuation authorization not confirmed")

    def launch_child(self, spec):
        path = spec["node_path"]
        intent = self.get_node_item(path, "SUBMISSION")
        if intent:
            if intent.get("token") != spec["client_token"] or intent.get("request_id") != self.config.request_id:
                raise SafetyViolation("SUBMISSION_CONFLICT", "child submission identity changed")
            if intent.get("instance_id"):
                instance = self.describe_instance(intent["instance_id"])
            else:
                instances = [self._instance(i) for page in self._pages("describe_instances",
                    Filters=[{"Name": "client-token", "Values": [spec["client_token"]]}])
                    for r in page.get("Reservations", []) for i in r.get("Instances", [])]
                if not instances and intent.get("launch_rejected") is True:
                    # Only an explicit throttle rejection permits another attempt.
                    # Clear permission before the request: a lost response must
                    # return to reconciliation, including after process restart.
                    self.transact([self.owner_check(), {"Update": {
                        "TableName": self.config.generation_table_name, "Key": self.node_key(path, "SUBMISSION"),
                        "UpdateExpression": "REMOVE launch_rejected",
                        "ConditionExpression": "request_id = :id AND #token = :token AND launch_rejected = :yes AND attribute_not_exists(instance_id)",
                        "ExpressionAttributeNames": {"#token": "token"},
                        "ExpressionAttributeValues": {":id": _av(self.config.request_id), ":token": _av(spec["client_token"]), ":yes": _av(True)}}}])
                    return self.submit_child(spec)
                if len(instances) != 1:
                    raise TransientFailure("ambiguous submission; do not resubmit child")
                instance = instances[0]
            self.verify_instance(instance, spec)
            if intent.get("settled") is not True:
                self.record_child(spec, instance)
            return instance["InstanceId"]
        intent = {"PK": "GEN#" + path, "SK": "SUBMISSION", "request_id": self.config.request_id,
                  "token": spec["client_token"], "settled": False}
        # A per-child write protects cleanup/retries; it is not a shared permit.
        # Stop/HOLD is intentionally monitored asynchronously, not gated here.
        self.transact([self.owner_check(), {"Put": {"TableName": self.config.generation_table_name,
            "Item": _ddb_item(intent), "ConditionExpression": "attribute_not_exists(PK)"}}])
        return self.submit_child(spec)

    def submit_child(self, spec):
        response = self._call("ec2", "run_instances", allow_failure=True, **self.child_request(spec))
        if response.get("_code") == "RequestLimitExceeded":
            self.transact([{"Update": {"TableName": self.config.generation_table_name,
                "Key": self.node_key(spec["node_path"], "SUBMISSION"),
                "UpdateExpression": "SET launch_rejected = :yes",
                "ConditionExpression": "request_id = :id AND #token = :token AND attribute_not_exists(instance_id)",
                "ExpressionAttributeNames": {"#token": "token"},
                "ExpressionAttributeValues": {":id": _av(self.config.request_id), ":token": _av(spec["client_token"]), ":yes": _av(True)}}}], bookkeeping=True)
            raise TransientFailure("child launch throttled; exact request may retry after backoff")
        if response.get("_code"):
            raise TransientFailure("child launch requires reconciliation: " + response["_code"])
        if len(response.get("Instances", [])) != 1:
            raise TransientFailure("ambiguous child launch response")
        instance = self._instance(response["Instances"][0])
        self.verify_instance(instance, spec)
        self.record_child(spec, instance)
        return instance["InstanceId"]

    def record_child(self, spec, instance):
        resource = {"PK": "GEN#" + spec["node_path"], "SK": "RESOURCE#" + instance["InstanceId"],
                    "request_id": self.config.request_id, "instance_id": instance["InstanceId"],
                    "client_token": spec["client_token"], "launch_template_id": spec["launch_template_id"],
                    "launch_template_version": spec["launch_template_version"]}
        self.transact([{"Put": {"TableName": self.config.generation_table_name,
            "Item": _ddb_item(resource),
            "ConditionExpression": "attribute_not_exists(PK) OR (request_id = :id AND client_token = :token)",
            "ExpressionAttributeValues": {":id": _av(self.config.request_id), ":token": _av(spec["client_token"])}}}], bookkeeping=True)
        self.ensure_status_alarm(spec["node_path"], instance["InstanceId"])
        self.transact([{ "Update": {"TableName": self.config.generation_table_name,
                "Key": self.node_key(spec["node_path"], "SUBMISSION"),
                "UpdateExpression": "SET instance_id = :instance, settled = :yes",
                "ConditionExpression": "request_id = :id AND #token = :token AND (attribute_not_exists(instance_id) OR instance_id = :instance)",
                "ExpressionAttributeNames": {"#token": "token"},
                "ExpressionAttributeValues": {":id": _av(self.config.request_id),
                    ":token": _av(spec["client_token"]), ":instance": _av(instance["InstanceId"]), ":yes": _av(True)}}}], bookkeeping=True)

    def publish_readiness(self, now, has_children):
        if self.first_ready_at is None:
            old = self.get_node_item(self.config.node_path, "STATE")
            self.first_ready_at = old.get("first_ready_at", old.get("ready_at", int(now))) if old.get("instance_id") == self.instance_id else int(now)
        state = {"PK": "GEN#" + self.config.node_path, "SK": "STATE", "request_id": self.config.request_id,
                 "instance_id": self.instance_id, "configuration_sha256": self.configuration_sha256,
                 "node_path": self.config.node_path, "generation": self.config.generation,
                 "predecessor_instance_id": self.config.predecessor_instance_id,
                 "handoff_token": self.config.handoff_token, "ready_at": int(now),
                 "first_ready_at": self.first_ready_at,
                 "daemon_live": True, "continuation": "DRY_RUN_PASSED" if has_children else "BOUNDARY"}
        self.transact([{"Put": {"TableName": self.config.generation_table_name,
            "Item": _ddb_item(state), "ConditionExpression": "attribute_not_exists(PK) OR (instance_id = :instance AND attribute_not_exists(error_code))",
            "ExpressionAttributeValues": {":instance": _av(self.instance_id)}}}])

    def eligible(self, path, instance_id, now):
        state = self.get_node_item(path, "STATE")
        if (state.get("request_id") != self.config.request_id
            or state.get("error_code") or state.get("status") == "ERROR"
            or state.get("instance_id") != instance_id or state.get("node_path") != path
            or state.get("configuration_sha256") != self.configuration_sha256
            or state.get("predecessor_instance_id") != self.instance_id
            or state.get("handoff_token") != "handoff-" + path
            or state.get("generation") != f"{len(path)-1:06d}"
            or state.get("daemon_live") is not True
            or type(state.get("ready_at")) is not int
            or not 0 <= now - state["ready_at"] <= 15
            or state.get("continuation") != ("BOUNDARY" if len(path)-1 >= self.settings["max_generation"] else "DRY_RUN_PASSED")):
            return None
        instance = self.describe_instance(instance_id)
        if instance["State"]["Name"] != "running":
            return None
        self.verify_instance(instance, specification(self.config, path, self.instance_id, "handoff-" + path))
        return state

    def readiness_check(self, path, state):
        names = {f"#s{i}": name for i, name in enumerate(sorted(state))}
        values = {f":s{i}": _av(state[name]) for i, name in enumerate(sorted(state))}
        now = int(time.time())
        values.update({":fresh": _av(now - 15), ":now": _av(now)})
        return {"ConditionCheck": {"TableName": self.config.generation_table_name,
            "Key": self.node_key(path, "STATE"),
            "ConditionExpression": " AND ".join(f"#s{i} = :s{i}" for i in range(len(state))) + " AND ready_at BETWEEN :fresh AND :now",
            "ExpressionAttributeNames": names, "ExpressionAttributeValues": values}}

    def accept_child(self, spec, instance_id, now):
        state = self.eligible(spec["node_path"], instance_id, now)
        if not state:
            return False
        child = self.read_node(spec["node_path"])
        if child.get("owner") == instance_id:
            return True
        self.transact([self.owner_check(), self.no_stop_check(spec["node_path"]), self.readiness_check(spec["node_path"], state),
            {"Update": {"TableName": self.config.generation_table_name, "Key": self.node_key(spec["node_path"]),
                "UpdateExpression": "SET #owner = :instance, #status = :owner, handoff_at = :now",
                "ConditionExpression": "instance_id = :instance AND #owner = :empty AND configuration_sha256 = :config",
                "ExpressionAttributeNames": {"#owner": "owner", "#status": "status"},
                "ExpressionAttributeValues": {":instance": _av(instance_id), ":owner": _av("OWNER"),
                    ":empty": _av(""), ":config": _av(self.configuration_sha256), ":now": _av(int(now))}}}], final=True)
        return True

    def parent_state(self):
        if self.config.predecessor_instance_id == "NONE":
            return "terminated"
        parent = self.describe_instance(self.config.predecessor_instance_id)
        tags = parent.get("Tags", {})
        spec = specification(self.config, self.config.node_path[:-1],
                             tags.get("predecessor-instance-id"), tags.get("handoff-token"))
        if parent["State"]["Name"] in ("shutting-down", "terminated"):
            # EC2 removes network/profile fields after termination. Anchor the
            # exact parent to its cycle-fenced, accepted retirement receipt.
            receipt = self.read_node(self.config.node_path[:-1])
            expected = {"request_id": self.config.request_id,
                        "instance_id": self.config.predecessor_instance_id,
                        "owner": self.config.predecessor_instance_id,
                        "configuration_sha256": self.configuration_sha256,
                        "status": "RETIRING"}
            if (parent.get("InstanceId") != self.config.predecessor_instance_id
                or any(receipt.get(k) != v for k, v in expected.items())
                or receipt.get("retirement_children") != list(child_paths(self.config.node_path[:-1], self.settings))
                or any(tags.get(k) != v for k, v in spec["tags"].items())):
                raise SafetyViolation("PARENT_RETIREMENT_IDENTITY_MISMATCH", "retiring parent differs from durable retirement receipt or lineage tags")
            if parent["State"]["Name"] == "shutting-down":
                self.verify_instance(parent, spec, retiring=True)
                return "shutting-down"  # Never confirmation of termination.
            if not self.confirmed_parent_termination:
                proof = json.dumps(parent, sort_keys=True, default=str, separators=(",", ":"))
                self.transact([{"Update": {"TableName": self.config.generation_table_name,
                    "Key": self.node_key(self.config.node_path[:-1], "RESOURCE#" + self.config.predecessor_instance_id),
                    "UpdateExpression": "SET termination_confirmed = :yes, termination_observed_at = if_not_exists(termination_observed_at, :at), termination_evidence_sha256 = if_not_exists(termination_evidence_sha256, :sha)",
                    "ConditionExpression": "request_id = :id AND instance_id = :instance AND launch_template_id = :lt AND launch_template_version = :version AND client_token = :token",
                    "ExpressionAttributeValues": {":yes": _av(True), ":at": _av(datetime.now(timezone.utc).isoformat()),
                        ":sha": _av(hashlib.sha256(proof.encode()).hexdigest()), ":id": _av(self.config.request_id),
                        ":instance": _av(self.config.predecessor_instance_id), ":lt": _av(self.config.launch_template_id),
                        ":version": _av(self.config.launch_template_version), ":token": _av(parent.get("ClientToken", ""))}}}], bookkeeping=True)
                self.confirmed_parent_termination = True
        else:
            self.verify_instance(parent, spec)
        return parent["State"]["Name"]

    def parent_terminated(self):
        return self.parent_state() == "terminated"

    def parent_launch_ready(self):
        return self.parent_state() in ("shutting-down", "terminated")

    def authorize_retirement(self, children, now, *, retiring=False):
        if tuple(children) != child_paths(self.config.node_path, self.settings):
            raise SafetyViolation("RETIREMENT_LINEAGE_INVALID", "retirement receipt has incorrect children")
        operations = []
        for path in children:
            child = self.read_node(path)
            state = self.eligible(path, child.get("instance_id"), now)
            if not state or child.get("owner") != state["instance_id"]:
                return False
            operations += [self.no_stop_check(path), self.readiness_check(path, state), {"ConditionCheck": {
                "TableName": self.config.generation_table_name, "Key": self.node_key(path),
                "ConditionExpression": "#owner = :instance AND #status IN (:owner, :leaf)",
                "ExpressionAttributeNames": {"#owner": "owner", "#status": "status"},
                "ExpressionAttributeValues": {":instance": _av(state["instance_id"]),
                    ":owner": _av("OWNER"), ":leaf": _av("LEAF")}}}]
        operations.append({"Update": {"TableName": self.config.generation_table_name,
            "Key": self.node_key(self.config.node_path), "UpdateExpression": "SET #status = :retiring, retirement_children = :children, retirement_at = if_not_exists(retirement_at, :now)",
            "ConditionExpression": "#owner = :instance AND #status = :owner",
            "ExpressionAttributeNames": {"#owner": "owner", "#status": "status"},
            "ExpressionAttributeValues": {":instance": _av(self.instance_id), ":owner": _av("RETIRING" if retiring else "OWNER"),
                ":retiring": _av("RETIRING"), ":children": _av(list(children)), ":now": _av(int(now))}}})
        self.transact(operations, final=True)
        return True

    def retire_self(self):
        self.verify_instance(self.describe_instance(self.instance_id), self.own_spec())
        node = self.read_node(self.config.node_path)
        if node.get("status") != "RETIRING" or not node.get("retirement_children"):
            raise SafetyViolation("RETIREMENT_NOT_AUTHORIZED", "missing family retirement intent")
        if not self.parent_terminated() or not self.authorize_retirement(node["retirement_children"], time.time(), retiring=True):
            raise TransientFailure("retirement proof changed; preserve parent")
        self.terminate_instance(self.instance_id)

    def mark_leaf(self):
        self.transact([{"Update": {"TableName": self.config.generation_table_name,
            "Key": self.node_key(self.config.node_path), "UpdateExpression": "SET #status = :leaf, leaf_at = if_not_exists(leaf_at, :now)",
            "ConditionExpression": "#owner = :instance AND #status IN (:owner, :leaf)",
            "ExpressionAttributeNames": {"#owner": "owner", "#status": "status"},
            "ExpressionAttributeValues": {":instance": _av(self.instance_id), ":owner": _av("OWNER"),
                ":leaf": _av("LEAF"), ":now": _av(int(time.time()))}}}])
