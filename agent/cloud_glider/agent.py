"""Fail-closed propagation state machine.

The state machine depends on a small gateway interface so its safety behavior can
be tested without AWS credentials.  The production gateway is implemented by
``cloud_glider.aws_cli`` using only the Python standard library and AWS CLI v2.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


GENERATION_RE = re.compile(r"^[0-9]{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_CONTROL_FIELDS = {
    "propagation_enabled",
    "desired_template_version",
    "desired_bootstrap_version",
    "template_s3_bucket",
    "template_s3_key",
    "template_s3_version_id",
    "template_sha256",
    "template_build_id",
    "agent_artifact_bucket",
    "agent_artifact_key",
    "agent_artifact_version_id",
    "agent_artifact_sha256",
    "max_generation",
    "max_live_generations",
    "concurrency_model",
    "approved_region",
    "approved_architecture",
    "approved_instance_types",
    "environment",
    "readiness_poll_seconds",
    "readiness_required_heartbeats",
    "heartbeat_interval_seconds",
    "readiness_timeout_seconds",
}
GENERATION_PARAMETER_NAMES = {
    "Environment", "Owner", "Generation", "PredecessorStackId", "HandoffToken",
    "ApprovedImageId", "InstanceType", "ImageArchitecture", "SubnetId", "SecurityGroupId",
    "AgentInstanceProfileName", "StateTableName", "AgentArtifactBucket", "AgentArtifactKey",
    "AgentArtifactVersionId", "AgentArtifactSha256", "BootstrapVersion", "TemplateVersion",
    "TemplateBucket", "TemplateKey", "TemplateS3VersionId", "TemplateSha256", "TemplateBuildId",
    "PropagationAuditLogGroupName", "AgentOperationsLogGroupName", "EmergencyHoldFunctionName",
    "OperationalAlertsTopicArn", "RootDeviceName", "RootVolumeGiB",
}


class SafetyViolation(RuntimeError):
    """A terminal identity, ownership, or invariant failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TransientFailure(RuntimeError):
    """An ambiguous or retryable condition for which predecessors are preserved."""


@dataclasses.dataclass(frozen=True)
class AgentConfig:
    environment: str
    generation: str
    stack_id: str
    predecessor_stack_id: str
    handoff_token: str
    state_table_name: str
    bootstrap_version: str
    template_version: str
    template_bucket: str
    template_key: str
    template_s3_version_id: str
    template_sha256: str
    template_build_id: str
    agent_artifact_bucket: str
    agent_artifact_key: str
    agent_artifact_version_id: str
    agent_artifact_sha256: str
    emergency_hold_function_name: str
    propagation_audit_log_group: str
    agent_operations_log_group: str

    @classmethod
    def load(cls, path: str | Path) -> "AgentConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        expected = {field.name for field in dataclasses.fields(cls)}
        missing = expected - raw.keys()
        extra = raw.keys() - expected
        if missing or extra:
            raise ValueError(f"invalid config fields; missing={sorted(missing)}, extra={sorted(extra)}")
        config = cls(**raw)
        config.validate()
        return config

    def validate(self) -> None:
        if not GENERATION_RE.fullmatch(self.generation):
            raise ValueError("generation must be six digits")
        if not self.stack_id.startswith("arn:"):
            raise ValueError("stack_id must be a CloudFormation ARN")
        stack_parts = self.stack_id.split(":", 5)
        if len(stack_parts) != 6 or stack_parts[2] != "cloudformation" or not stack_parts[3]:
            raise ValueError("stack_id must be a regional CloudFormation ARN")
        if not self.handoff_token or len(self.handoff_token) > 128:
            raise ValueError("handoff_token is required and must not exceed 128 characters")
        for name in ("template_sha256", "agent_artifact_sha256"):
            if not SHA256_RE.fullmatch(getattr(self, name).lower()):
                raise ValueError(f"{name} must be a SHA-256 digest")


class Gateway(Protocol):
    instance_id: str

    def read_control_and_hold(self) -> tuple[dict[str, Any], bool]: ...
    def read_current(self) -> dict[str, Any]: ...
    def claim_initial_current(self, identity: dict[str, Any]) -> bool: ...
    def write_heartbeat(self, state: dict[str, Any]) -> None: ...
    def acquire_lease(self, owner: str, generation: str, now: int, expires: int) -> bool: ...
    def renew_lease(self, owner: str, expires: int) -> None: ...
    def release_lease(self, owner: str) -> None: ...
    def describe_stack(self, stack_id_or_name: str) -> dict[str, Any] | None: ...
    def stack_instance_id(self, stack_id: str) -> str: ...
    def verify_template_artifact(self, control: dict[str, Any]) -> None: ...
    def create_stack(self, specification: dict[str, Any]) -> str: ...
    def read_generation_state(self, generation: str) -> dict[str, Any] | None: ...
    def check_capacity(self, max_live_generations: int) -> None: ...
    def create_preflight(self, specification: dict[str, Any]) -> str: ...
    def describe_change_set(self, change_set_id: str) -> dict[str, Any]: ...
    def discard_preflight(self, change_set_id: str, stack_name: str, role_arn: str) -> None: ...
    def handoff(self, expected: dict[str, Any], successor: dict[str, Any], audit: dict[str, Any]) -> bool: ...
    def delete_stack(self, stack_id: str, role_arn: str, token: str) -> None: ...
    def invoke_hold(self, generation: str, error_code: str, correlation_id: str) -> None: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stable_token(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


class Agent:
    """One-generation orchestrator with explicit fail-closed gates."""

    def __init__(
        self,
        config: AgentConfig,
        gateway: Gateway,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        logger: Callable[[str], None] = print,
    ):
        self.config = config
        self.gateway = gateway
        self.clock = clock
        self.sleep = sleep
        self.logger = logger
        self.correlation_id = str(uuid.uuid4())
        self.lease_owner = f"{config.generation}:{gateway.instance_id}:{uuid.uuid4()}"
        self.heartbeat_sequence = 0
        self._own_stack: dict[str, Any] | None = None

    @property
    def generation_number(self) -> int:
        return int(self.config.generation)

    def log(self, event: str, **details: Any) -> None:
        record = {
            "timestamp": utc_now(),
            "event": event,
            "environment": self.config.environment,
            "generation": self.config.generation,
            "instance_id": self.gateway.instance_id,
            "correlation_id": self.correlation_id,
            **details,
        }
        self.logger(json.dumps(record, sort_keys=True, separators=(",", ":")))

    def _validated_control(self, control: dict[str, Any]) -> dict[str, Any]:
        missing = REQUIRED_CONTROL_FIELDS - control.keys()
        if missing:
            raise SafetyViolation("CONTROL_SCHEMA_INVALID", f"control record lacks {sorted(missing)}")
        if control["max_live_generations"] != 3:
            raise SafetyViolation("LIVE_GENERATION_LIMIT_INVALID", "absolute maximum must remain 3")
        if control["concurrency_model"] != "PREFLIGHT_THEN_RETIRE":
            raise SafetyViolation("CONCURRENCY_MODEL_INVALID", "unsupported concurrency model")
        if control["approved_region"] != "us-west-2" or self.config.stack_id.split(":")[3] != "us-west-2":
            raise SafetyViolation("REGION_POLICY_MISMATCH", "sandbox operation is restricted to us-west-2")
        if control["approved_architecture"] != "arm64":
            raise SafetyViolation("ARCHITECTURE_POLICY_MISMATCH", "sandbox operation requires arm64")
        if control["approved_instance_types"] != ["t4g.micro"]:
            raise SafetyViolation("INSTANCE_TYPE_POLICY_MISMATCH", "sandbox operation requires only t4g.micro")
        if control["environment"] != self.config.environment:
            raise SafetyViolation("ENVIRONMENT_POLICY_MISMATCH", "control and bootstrap environments differ")
        if int(control["max_generation"]) < 0:
            raise SafetyViolation("MAX_GENERATION_INVALID", "maximum generation cannot be negative")
        for field in ("readiness_poll_seconds", "readiness_required_heartbeats", "heartbeat_interval_seconds", "readiness_timeout_seconds"):
            if int(control[field]) < 1:
                raise SafetyViolation("CONTROL_TIMING_INVALID", f"{field} must be positive")
        if control["template_sha256"].lower() != control["template_sha256"]:
            raise SafetyViolation("TEMPLATE_IDENTITY_INVALID", "template digest must be lowercase")
        if control["agent_artifact_sha256"].lower() != control["agent_artifact_sha256"]:
            raise SafetyViolation("AGENT_IDENTITY_INVALID", "agent digest must be lowercase")
        return control

    def _identity(self) -> dict[str, Any]:
        return {
            "generation": self.config.generation,
            "stack_id": self.config.stack_id,
            "instance_id": self.gateway.instance_id,
            "template_version": self.config.template_version,
            "template_s3_version_id": self.config.template_s3_version_id,
            "template_sha256": self.config.template_sha256.lower(),
            "template_build_id": self.config.template_build_id,
            "bootstrap_version": self.config.bootstrap_version,
            "agent_artifact_version_id": self.config.agent_artifact_version_id,
            "agent_artifact_sha256": self.config.agent_artifact_sha256.lower(),
        }

    def verify_self(self) -> None:
        stack = self.gateway.describe_stack(self.config.stack_id)
        if not stack or stack.get("StackId") != self.config.stack_id:
            raise SafetyViolation("SELF_STACK_IDENTITY_MISMATCH", "own stack cannot be verified")
        parameters = stack.get("Parameters", {})
        if set(parameters) != GENERATION_PARAMETER_NAMES:
            missing = GENERATION_PARAMETER_NAMES - set(parameters)
            extra = set(parameters) - GENERATION_PARAMETER_NAMES
            raise SafetyViolation(
                "SELF_PARAMETER_SET_MISMATCH",
                f"generation parameter set changed; missing={sorted(missing)}, extra={sorted(extra)}",
            )
        expected = {
            "Environment": self.config.environment,
            "Generation": self.config.generation,
            "PredecessorStackId": self.config.predecessor_stack_id,
            "HandoffToken": self.config.handoff_token,
            "BootstrapVersion": self.config.bootstrap_version,
            "TemplateVersion": self.config.template_version,
            "TemplateBucket": self.config.template_bucket,
            "TemplateKey": self.config.template_key,
            "TemplateS3VersionId": self.config.template_s3_version_id,
            "TemplateSha256": self.config.template_sha256,
            "TemplateBuildId": self.config.template_build_id,
            "AgentArtifactBucket": self.config.agent_artifact_bucket,
            "AgentArtifactKey": self.config.agent_artifact_key,
            "AgentArtifactVersionId": self.config.agent_artifact_version_id,
            "AgentArtifactSha256": self.config.agent_artifact_sha256,
        }
        mismatches = {key: (parameters.get(key), value) for key, value in expected.items() if parameters.get(key) != value}
        if mismatches:
            raise SafetyViolation("SELF_TEMPLATE_IDENTITY_MISMATCH", f"own parameters mismatch: {mismatches}")
        if self.gateway.stack_instance_id(self.config.stack_id) != self.gateway.instance_id:
            raise SafetyViolation("SELF_INSTANCE_IDENTITY_MISMATCH", "stack instance does not match IMDS")
        self._own_stack = stack

    def heartbeat(self, control: dict[str, Any], hold_active: bool) -> None:
        current = self.gateway.read_current()
        is_current = (
            current.get("generation") == self.config.generation
            and current.get("stack_id") == self.config.stack_id
            and current.get("instance_id") == self.gateway.instance_id
            and current.get("status") == "CURRENT"
        )
        self.heartbeat_sequence += 1
        state = {
            **self._identity(),
            "status": "CURRENT" if is_current else "CANDIDATE",
            "predecessor_stack_id": self.config.predecessor_stack_id,
            "handoff_token": self.config.handoff_token,
            "workload_healthy": True,
            "observed_propagation_enabled": bool(control["propagation_enabled"]),
            "observed_hold_active": hold_active,
            "heartbeat_sequence": self.heartbeat_sequence,
            "heartbeat_at_epoch": int(self.clock()),
            "updated_at": utc_now(),
        }
        self.gateway.write_heartbeat(state)

    def ensure_bootstrap_ownership(self) -> None:
        current = self.gateway.read_current()
        if current.get("status") != "UNINITIALIZED":
            return
        identity = {**self._identity(), "status": "CURRENT", "updated_at": utc_now()}
        if not self.gateway.claim_initial_current(identity):
            current = self.gateway.read_current()
            if current.get("generation") != self.config.generation:
                raise SafetyViolation("BOOTSTRAP_OWNERSHIP_CONFLICT", "another generation claimed CURRENT")

    def is_current_owner(self) -> bool:
        current = self.gateway.read_current()
        return (
            current.get("status") == "CURRENT"
            and current.get("generation") == self.config.generation
            and current.get("stack_id") == self.config.stack_id
            and current.get("instance_id") == self.gateway.instance_id
        )

    def _fresh_enabled_control(self) -> dict[str, Any]:
        control, hold = self.gateway.read_control_and_hold()
        control = self._validated_control(control)
        if not control["propagation_enabled"] or hold:
            raise TransientFailure("operator stop state prevents provisioning")
        return control

    def _successor_parameters(self, control: dict[str, Any], generation: int, predecessor: str, token: str) -> dict[str, str]:
        assert self._own_stack is not None
        parameters = {
            key: self._own_stack["Parameters"][key]
            for key in GENERATION_PARAMETER_NAMES
        }
        parameters.update(
            {
                "Generation": f"{generation:06d}",
                "PredecessorStackId": predecessor,
                "HandoffToken": token,
                "BootstrapVersion": control["desired_bootstrap_version"],
                "TemplateVersion": control["desired_template_version"],
                "TemplateBucket": control["template_s3_bucket"],
                "TemplateKey": control["template_s3_key"],
                "TemplateS3VersionId": control["template_s3_version_id"],
                "TemplateSha256": control["template_sha256"],
                "TemplateBuildId": control["template_build_id"],
                "AgentArtifactBucket": control["agent_artifact_bucket"],
                "AgentArtifactKey": control["agent_artifact_key"],
                "AgentArtifactVersionId": control["agent_artifact_version_id"],
                "AgentArtifactSha256": control["agent_artifact_sha256"],
            }
        )
        return parameters

    def _stack_specification(self, control: dict[str, Any], generation: int, predecessor: str, token: str) -> dict[str, Any]:
        assert self._own_stack is not None
        return {
            "stack_name": f"cloud-glider-{self.config.environment}-gen-{generation:06d}",
            "generation": f"{generation:06d}",
            "template_bucket": control["template_s3_bucket"],
            "template_key": control["template_s3_key"],
            "template_version_id": control["template_s3_version_id"],
            "parameters": self._successor_parameters(control, generation, predecessor, token),
            "role_arn": self._own_stack["RoleARN"],
            "client_token": stable_token("cg-create", self.config.stack_id, f"{generation:06d}", control["template_sha256"]),
            "tags": {
                "project": "cloud-glider",
                "environment": self.config.environment,
                "generation": f"{generation:06d}",
                "purpose": "generation-stack",
            },
        }

    def provision_successor(self, control: dict[str, Any]) -> tuple[dict[str, Any], str]:
        generation = self.generation_number + 1
        token = stable_token("handoff", self.config.stack_id, f"{generation:06d}", control["template_sha256"])
        specification = self._stack_specification(control, generation, self.config.stack_id, token)
        existing = self.gateway.describe_stack(specification["stack_name"])
        if existing:
            if existing.get("Parameters") != specification["parameters"]:
                raise SafetyViolation("SUCCESSOR_RECONCILIATION_MISMATCH", "existing successor has different parameters")
            stack_id = existing["StackId"]
        else:
            self.gateway.verify_template_artifact(control)
            self.gateway.check_capacity(int(control["max_live_generations"]))
            fresh = self._fresh_enabled_control()  # mandatory last read before CreateStack
            if self._control_identity(fresh) != self._control_identity(control):
                raise TransientFailure("approved identity changed before successor creation")
            specification = self._stack_specification(fresh, generation, self.config.stack_id, token)
            stack_id = self.gateway.create_stack(specification)
        return specification, stack_id

    def _renew(self, control: dict[str, Any]) -> None:
        duration = max(60, int(control["readiness_poll_seconds"]) * 4)
        self.gateway.renew_lease(self.lease_owner, int(self.clock()) + duration)

    def wait_for_healthy_successor(self, control: dict[str, Any], specification: dict[str, Any], stack_id: str) -> dict[str, Any]:
        deadline = self.clock() + int(control["readiness_timeout_seconds"])
        expected_generation = specification["generation"]
        expected_instance: str | None = None
        prior: dict[str, Any] | None = None
        accepted_heartbeats = 0
        required_heartbeats = int(control["readiness_required_heartbeats"])
        while self.clock() <= deadline:
            self._renew(control)
            stack = self.gateway.describe_stack(stack_id)
            if not stack:
                raise TransientFailure("successor stack disappeared")
            status = stack.get("StackStatus", "")
            if status.endswith("FAILED") or "ROLLBACK" in status:
                raise TransientFailure(f"successor stack failed with {status}")
            if status == "CREATE_COMPLETE":
                expected_instance = expected_instance or self.gateway.stack_instance_id(stack_id)
                state = self.gateway.read_generation_state(expected_generation)
                if state and self._eligible_heartbeat(
                    state, control, specification, stack_id, expected_instance, int(self.clock())
                ):
                    if prior is None:
                        prior = state
                        accepted_heartbeats = 1
                        if required_heartbeats <= 1:
                            return state
                    elif state["heartbeat_sequence"] != prior["heartbeat_sequence"]:
                        elapsed = state["heartbeat_at_epoch"] - prior["heartbeat_at_epoch"]
                        if elapsed >= int(control["heartbeat_interval_seconds"]):
                            prior = state
                            accepted_heartbeats += 1
                            if accepted_heartbeats >= required_heartbeats:
                                return state
            self.sleep(int(control["readiness_poll_seconds"]))
        raise TransientFailure("successor readiness timed out; predecessor preserved")

    @staticmethod
    def _eligible_heartbeat(
        state: dict[str, Any], control: dict[str, Any], specification: dict[str, Any],
        stack_id: str, instance_id: str, now: int,
    ) -> bool:
        expected = {
            "generation": specification["generation"],
            "stack_id": stack_id,
            "instance_id": instance_id,
            "predecessor_stack_id": specification["parameters"]["PredecessorStackId"],
            "handoff_token": specification["parameters"]["HandoffToken"],
            "template_version": control["desired_template_version"],
            "template_s3_version_id": control["template_s3_version_id"],
            "template_sha256": control["template_sha256"],
            "template_build_id": control["template_build_id"],
            "bootstrap_version": control["desired_bootstrap_version"],
            "agent_artifact_version_id": control["agent_artifact_version_id"],
            "agent_artifact_sha256": control["agent_artifact_sha256"],
            "status": "CANDIDATE",
            "workload_healthy": True,
            "observed_propagation_enabled": True,
            "observed_hold_active": False,
        }
        if not all(state.get(key) == value for key, value in expected.items()):
            return False
        try:
            heartbeat_at = int(state["heartbeat_at_epoch"])
            int(state["heartbeat_sequence"])
        except (KeyError, TypeError, ValueError):
            return False
        freshness = max(
            int(control["heartbeat_interval_seconds"]) * 3,
            int(control["readiness_poll_seconds"]) * 3,
        )
        return 0 <= now - heartbeat_at <= freshness

    def continuation_preflight(self, control: dict[str, Any], successor_spec: dict[str, Any], successor_stack_id: str) -> None:
        successor_number = int(successor_spec["generation"])
        if successor_number >= int(control["max_generation"]):
            self.log("continuation_boundary_reached", successor_generation=successor_spec["generation"])
            return
        self.gateway.check_capacity(int(control["max_live_generations"]))
        next_number = successor_number + 1
        token = stable_token("handoff", successor_stack_id, f"{next_number:06d}", control["template_sha256"])
        fresh = self._fresh_enabled_control()  # mandatory last read before CreateChangeSet
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure("approved identity changed during cycle")
        specification = self._stack_specification(fresh, next_number, successor_stack_id, token)
        specification["change_set_name"] = stable_token("cg-preflight", successor_stack_id, f"{next_number:06d}")
        change_set_id = self.gateway.create_preflight(specification)
        try:
            deadline = self.clock() + int(control["readiness_timeout_seconds"])
            while self.clock() <= deadline:
                self._renew(control)
                result = self.gateway.describe_change_set(change_set_id)
                if result.get("Status") == "CREATE_COMPLETE":
                    return
                if result.get("Status") == "FAILED":
                    raise TransientFailure(f"continuation change set failed: {result.get('StatusReason', 'unknown')}")
                self.sleep(int(control["readiness_poll_seconds"]))
            raise TransientFailure("continuation change set timed out")
        finally:
            self.gateway.discard_preflight(change_set_id, specification["stack_name"], specification["role_arn"])

    @staticmethod
    def _control_identity(control: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(control[key] for key in sorted(REQUIRED_CONTROL_FIELDS))

    def conditional_handoff(self, control: dict[str, Any], successor_spec: dict[str, Any], stack_id: str, state: dict[str, Any]) -> None:
        fresh = self._fresh_enabled_control()
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure("control changed before handoff")
        expected = {
            **self._identity(),
            "lease_owner": self.lease_owner,
            "control_identity": {key: fresh[key] for key in REQUIRED_CONTROL_FIELDS},
        }
        successor = {
            "generation": successor_spec["generation"],
            "stack_id": stack_id,
            "instance_id": state["instance_id"],
            "status": "CURRENT",
            "handoff_token": successor_spec["parameters"]["HandoffToken"],
            "updated_at": utc_now(),
        }
        audit = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": utc_now(),
            "correlation_id": self.correlation_id,
            "from_generation": self.config.generation,
            "to_generation": successor_spec["generation"],
        }
        if not self.gateway.handoff(expected, successor, audit):
            raise SafetyViolation("HANDOFF_CONDITION_FAILED", "CURRENT or lease ownership changed")

    def cycle(self) -> str:
        control, hold = self.gateway.read_control_and_hold()
        control = self._validated_control(control)
        self.heartbeat(control, hold)
        if not self.is_current_owner():
            return "CANDIDATE"
        if hold or not control["propagation_enabled"]:
            return "STOPPED_BY_OPERATOR"
        if self.generation_number >= int(control["max_generation"]):
            return "MAX_GENERATION_REACHED"
        lease_seconds = max(60, int(control["readiness_poll_seconds"]) * 4)
        now = int(self.clock())
        if not self.gateway.acquire_lease(self.lease_owner, self.config.generation, now, now + lease_seconds):
            return "LEASE_NOT_ACQUIRED"
        try:
            if not self.is_current_owner():
                return "OWNERSHIP_CHANGED"
            specification, stack_id = self.provision_successor(control)
            state = self.wait_for_healthy_successor(control, specification, stack_id)
            self.continuation_preflight(control, specification, stack_id)
            self.conditional_handoff(control, specification, stack_id, state)
            assert self._own_stack is not None
            delete_token = stable_token("cg-retire", self.config.stack_id, stack_id)
            self.gateway.delete_stack(self.config.stack_id, self._own_stack["RoleARN"], delete_token)
            return "HANDOFF_COMPLETE"
        finally:
            try:
                self.gateway.release_lease(self.lease_owner)
            except TransientFailure as exc:
                self.log("lease_release_deferred", reason=str(exc))

    def run(self) -> int:
        try:
            self.verify_self()
            self.ensure_bootstrap_ownership()
            while True:
                try:
                    result = self.cycle()
                    self.log("cycle_complete", result=result)
                except TransientFailure as exc:
                    self.log("cycle_deferred", reason=str(exc))
                control, _ = self.gateway.read_control_and_hold()
                self.sleep(max(1, int(control.get("heartbeat_interval_seconds", 5))))
        except SafetyViolation as exc:
            self.log("terminal_safety_violation", error_code=exc.code, reason=str(exc))
            self.gateway.invoke_hold(self.config.generation, exc.code, self.correlation_id)
            return 2
