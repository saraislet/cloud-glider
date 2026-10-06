"""Fail-closed, single-successor EC2 propagation protocol."""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Readiness proof lifetime is independent of heartbeat and polling cadence.
FUNCTIONAL_READINESS_MAX_AGE_SECONDS = 15
FUNCTIONAL_READINESS_REFRESH_SECONDS = 5

GENERATION_RE = re.compile(r"^[0-9]{6}$")
INSTANCE_RE = re.compile(r"^i-[0-9a-f]{17}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_CONTROL_FIELDS = {
    "propagation_enabled",
    "generation_table_name",
    "request_id",
    "cleanup_requested",
    "cleanup_status",
    "propagation_backend",
    "desired_template_version",
    "desired_bootstrap_version",
    "template_s3_bucket",
    "template_s3_key",
    "template_s3_version_id",
    "template_sha256",
    "template_build_id",
    "daemon_artifact_bucket",
    "daemon_artifact_key",
    "daemon_artifact_version_id",
    "daemon_artifact_sha256",
    "max_generation",
    "max_live_generations",
    "concurrency_model",
    "approved_region",
    "approved_account_id",
    "approved_architecture",
    "approved_instance_types",
    "environment",
    "readiness_poll_seconds",
    "readiness_required_heartbeats",
    "heartbeat_interval_seconds",
    "readiness_timeout_seconds",
    "launch_template_id",
    "launch_template_version",
    "launch_template_sha256",
}


from .daemon import SafetyViolation, TransientFailure


@dataclasses.dataclass(frozen=True)
class Ec2DaemonConfig:
    environment: str
    request_id: str
    generation_table_name: str
    owner: str
    generation: str
    instance_id: str
    predecessor_instance_id: str
    handoff_token: str
    launch_template_id: str
    launch_template_version: str
    state_table_name: str
    bootstrap_version: str
    template_version: str
    template_bucket: str
    template_key: str
    template_s3_version_id: str
    template_sha256: str
    template_build_id: str
    daemon_artifact_bucket: str
    daemon_artifact_key: str
    daemon_artifact_version_id: str
    daemon_artifact_sha256: str
    emergency_hold_function_name: str
    propagation_audit_log_group: str
    daemon_operations_log_group: str
    operational_alerts_topic_arn: str
    daemon_delivery_mode: str = "baked"

    @classmethod
    def load(cls, path: str | Path) -> "Ec2DaemonConfig":
        # Launch-template user data is static. Only lineage comes from creation tags;
        # the template identity comes from EC2, never a mutable $Default/$Latest alias.
        from .ec2_sdk import load_instance_config

        raw = load_instance_config(json.loads(Path(path).read_text(encoding="utf-8")))
        expected = {field.name for field in dataclasses.fields(cls)}
        if set(raw) != expected:
            raise ValueError(f"invalid config fields: {sorted(set(raw) ^ expected)}")
        config = cls(**raw)
        config.validate()
        return config

    def validate(self) -> None:
        if self.daemon_delivery_mode != "baked":
            raise ValueError("EC2 propagation requires an approved baked image")
        if self.generation_table_name != f"cloud-glider-{self.environment}-generations":
            raise ValueError("unexpected generation table")
        if not re.fullmatch(r"[1-9][0-9]{0,17}", self.request_id):
            raise ValueError("request_id must be a positive cycle number")
        if not self.owner or len(self.owner) > 64:
            raise ValueError("invalid owner tag")
        if not GENERATION_RE.fullmatch(self.generation):
            raise ValueError("generation must be six digits")
        if not INSTANCE_RE.fullmatch(self.instance_id):
            raise ValueError("instance_id must be an EC2 instance ID")
        if self.predecessor_instance_id != "NONE" and not INSTANCE_RE.fullmatch(
            self.predecessor_instance_id
        ):
            raise ValueError("invalid predecessor instance ID")
        if (self.generation == "000000") != (self.predecessor_instance_id == "NONE"):
            raise ValueError("only generation zero may have no predecessor")
        if not re.fullmatch(r"lt-[0-9a-f]{17}", self.launch_template_id):
            raise ValueError("invalid launch template ID")
        if not re.fullmatch(r"[1-9][0-9]*", self.launch_template_version):
            raise ValueError(
                "launch template version must be a positive numeric version"
            )
        if not self.handoff_token or len(self.handoff_token) > 128:
            raise ValueError("invalid handoff token")
        for name in ("template_sha256", "daemon_artifact_sha256"):
            if not SHA256_RE.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def stable_token(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]


def control_fingerprint(control: dict) -> str:
    snapshot = {key: control[key] for key in sorted(REQUIRED_CONTROL_FIELDS)}
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Ec2Daemon:
    def __init__(
        self,
        config: Ec2DaemonConfig,
        gateway: Any,
        *,
        clock: Callable = time.time,
        sleep: Callable = time.sleep,
        logger: Callable = print,
        timing_clock: Callable = time.monotonic,
    ):
        self.timing_clock = timing_clock
        self.config, self.gateway = config, gateway
        self.clock, self.sleep, self.logger = clock, sleep, logger
        self.correlation_id = str(uuid.uuid4())
        self.lease_owner = f"{config.generation}:{gateway.instance_id}:{uuid.uuid4()}"
        self.heartbeat_sequence = 0
        # Ownership observations are independent of health publication cadence.
        self._poll_seconds = 1
        self._last_heartbeat_at = None
        self._heartbeat_at_epoch = None
        self._last_readiness_at = None
        self._functional_readiness = None

    @property
    def generation_number(self) -> int:
        return int(self.config.generation)

    def log(self, event: str, **details: Any) -> None:
        if event == "phase_timing":
            from .timing import export_record
            export_record({"event": event, "timestamp": utc_now(), **details})
        self.logger(
            json.dumps(
                {
                    "timestamp": utc_now(),
                    "event": event,
                    "environment": self.config.environment,
                    "request_id": self.config.request_id,
                    "generation": self.config.generation,
                    "instance_id": self.gateway.instance_id,
                    "correlation_id": self.correlation_id,
                    **details,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    def _validated_control(self, control: dict) -> dict:
        missing = REQUIRED_CONTROL_FIELDS - control.keys()
        if missing:
            raise SafetyViolation(
                "CONTROL_SCHEMA_INVALID", f"control lacks {sorted(missing)}"
            )
        if control["generation_table_name"] != self.config.generation_table_name:
            raise SafetyViolation(
                "GENERATION_TABLE_MISMATCH", "unexpected generation table"
            )
        if control["request_id"] != self.config.request_id:
            raise TransientFailure("stale chain identity; daemon is fenced")
        if type(control["cleanup_requested"]) is not bool:
            raise SafetyViolation("LIFECYCLE_SCHEMA_INVALID", "invalid cleanup switch")
        if control["cleanup_requested"] or control["cleanup_status"] not in (
            "IDLE",
            "COMPLETE",
        ):
            raise TransientFailure("cleanup fences daemon activity")
        if control["propagation_backend"] != "ec2":
            raise SafetyViolation("BACKEND_MISMATCH", "offline EC2 migration required")
        numeric = {
            "max_generation",
            "max_live_generations",
            "readiness_poll_seconds",
            "readiness_required_heartbeats",
            "heartbeat_interval_seconds",
            "readiness_timeout_seconds",
        }
        strings = (
            REQUIRED_CONTROL_FIELDS
            - numeric
            - {"propagation_enabled", "cleanup_requested", "approved_instance_types"}
        )
        if any(type(control[k]) is not int for k in numeric) or any(
            not isinstance(control[k], str) or not control[k] for k in strings
        ):
            raise SafetyViolation(
                "CONTROL_SCHEMA_INVALID", "control has invalid attribute types"
            )
        expected_topic = f"arn:aws:sns:us-west-2:{self.gateway.account_id}:cloud-glider-{self.config.environment}-operational-alerts"
        if self.config.operational_alerts_topic_arn != expected_topic:
            raise SafetyViolation(
                "ALERT_TOPIC_IDENTITY_MISMATCH",
                "alert topic differs from the approved environment",
            )
        if type(control["propagation_enabled"]) is not bool:
            raise SafetyViolation(
                "CONTROL_SCHEMA_INVALID", "propagation_enabled must be boolean"
            )
        if (
            control["max_live_generations"] not in (2, 3)
            or control["concurrency_model"] != "EC2_DRY_RUN_THEN_RETIRE"
        ):
            raise SafetyViolation(
                "CONCURRENCY_MODEL_INVALID", "unsupported concurrency or ceiling"
            )
        if (
            control["approved_region"] != "us-west-2"
            or self.gateway.region != "us-west-2"
        ):
            raise SafetyViolation(
                "REGION_POLICY_MISMATCH", "sandbox requires us-west-2"
            )
        if control["approved_account_id"] != self.gateway.account_id:
            raise SafetyViolation("ACCOUNT_POLICY_MISMATCH", "unexpected AWS account")
        if control["approved_architecture"] != "arm64" or control[
            "approved_instance_types"
        ] != ["t4g.micro"]:
            raise SafetyViolation(
                "COMPUTE_POLICY_MISMATCH", "sandbox requires arm64 t4g.micro"
            )
        if control["environment"] != self.config.environment:
            raise SafetyViolation("ENVIRONMENT_POLICY_MISMATCH", "environment differs")
        if not 0 <= int(control["max_generation"]) <= 999999:
            raise SafetyViolation(
                "MAX_GENERATION_INVALID", "generation is out of bounds"
            )
        for field in (
            "readiness_poll_seconds",
            "readiness_required_heartbeats",
            "heartbeat_interval_seconds",
            "readiness_timeout_seconds",
        ):
            if int(control[field]) < 1:
                raise SafetyViolation(
                    "CONTROL_TIMING_INVALID", f"{field} must be positive"
                )
        for field in (
            "template_sha256",
            "daemon_artifact_sha256",
            "launch_template_sha256",
        ):
            if not SHA256_RE.fullmatch(control[field]):
                raise SafetyViolation("TEMPLATE_IDENTITY_INVALID", f"invalid {field}")
        if (
            control["launch_template_id"] != self.config.launch_template_id
            or control["launch_template_version"] != self.config.launch_template_version
        ):
            raise SafetyViolation(
                "LAUNCH_TEMPLATE_IDENTITY_MISMATCH",
                "offline template migration required",
            )
        for key, value in self._artifact_identity().items():
            if control[key] != value:
                raise SafetyViolation(
                    "ARTIFACT_IDENTITY_MISMATCH",
                    f"offline artifact migration required: {key}",
                )
        return control

    def _artifact_identity(self) -> dict:
        c = self.config
        return {
            "desired_template_version": c.template_version,
            "desired_bootstrap_version": c.bootstrap_version,
            "template_s3_bucket": c.template_bucket,
            "template_s3_key": c.template_key,
            "template_s3_version_id": c.template_s3_version_id,
            "template_sha256": c.template_sha256,
            "template_build_id": c.template_build_id,
            "daemon_artifact_bucket": c.daemon_artifact_bucket,
            "daemon_artifact_key": c.daemon_artifact_key,
            "daemon_artifact_version_id": c.daemon_artifact_version_id,
            "daemon_artifact_sha256": c.daemon_artifact_sha256,
        }

    def _identity(self) -> dict:
        c = self.config
        return {
            "request_id": c.request_id,
            "generation": c.generation,
            "instance_id": self.gateway.instance_id,
            "launch_template_id": c.launch_template_id,
            "launch_template_version": c.launch_template_version,
            "template_version": c.template_version,
            "template_s3_version_id": c.template_s3_version_id,
            "template_sha256": c.template_sha256,
            "template_build_id": c.template_build_id,
            "bootstrap_version": c.bootstrap_version,
            "daemon_artifact_version_id": c.daemon_artifact_version_id,
            "daemon_artifact_sha256": c.daemon_artifact_sha256,
        }

    def verify_self(self) -> None:
        self.config.validate()
        if self.gateway.instance_id != self.config.instance_id:
            raise SafetyViolation(
                "SELF_INSTANCE_IDENTITY_MISMATCH", "configuration differs from IMDS"
            )
        control, _ = self.gateway.read_control_and_hold()
        control = self._validated_control(control)
        self.gateway.verify_launch_template(control)
        instance = self.gateway.describe_instance(self.gateway.instance_id)
        if not instance:
            raise TransientFailure("own instance is missing")
        self.gateway.verify_instance(
            instance,
            self._specification(
                control,
                self.generation_number,
                self.config.predecessor_instance_id,
                self.config.handoff_token,
            ),
        )

    def is_current_owner(self, current: dict | None = None) -> bool:
        if current is None:
            current = self.gateway.read_current()
        return (
            current.get("status") == "CURRENT"
            and current.get("request_id") == self.config.request_id
            and current.get("generation") == self.config.generation
            and current.get("instance_id") == self.gateway.instance_id
            and current.get("launch_template_id") == self.config.launch_template_id
            and current.get("launch_template_version")
            == self.config.launch_template_version
        )

    def ensure_bootstrap_ownership(self) -> None:
        current = self.gateway.read_current()
        if current.get("status") != "UNINITIALIZED":
            return
        if self.generation_number != 0 or self.config.predecessor_instance_id != "NONE":
            raise SafetyViolation(
                "BOOTSTRAP_OWNERSHIP_CONFLICT",
                "only the operator seed may claim CURRENT",
            )
        if not self.gateway.claim_initial_current(
            {**self._identity(), "status": "CURRENT", "updated_at": utc_now()}
        ):
            if not self.is_current_owner():
                raise SafetyViolation(
                    "BOOTSTRAP_OWNERSHIP_CONFLICT", "another instance claimed CURRENT"
                )

    def heartbeat(
        self,
        control: dict,
        hold: bool,
        current: dict,
        *,
        telemetry_tick=True,
        refresh_readiness=True,
    ) -> None:
        if telemetry_tick:
            self.heartbeat_sequence += 1
            self._heartbeat_at_epoch = int(self.clock())
        proof = self._functional_readiness
        proof_failure = None
        if (
            self._candidate_parent_matches(current)
            and not hold
            and control["propagation_enabled"]
        ):
            if refresh_readiness:
                try:
                    with self.phase("candidate_functional_probe"):
                        proof = self.prove_functional_readiness(control, current)
                    self._last_readiness_at = self.timing_clock()
                except TransientFailure as exc:
                    # Replace a prior proof before retrying a failed probe.
                    proof, proof_failure = None, exc
        else:
            proof = None
        self._functional_readiness = proof
        self.gateway.write_heartbeat(
            {
                **self._identity(),
                "status": "CURRENT" if self.is_current_owner(current) else "CANDIDATE",
                "predecessor_instance_id": self.config.predecessor_instance_id,
                "handoff_token": self.config.handoff_token,
                "daemon_live": True,
                "functional_readiness": proof,
                "observed_propagation_enabled": control["propagation_enabled"],
                "observed_hold_active": hold,
                "heartbeat_sequence": self.heartbeat_sequence,
                "heartbeat_at_epoch": self._heartbeat_at_epoch,
                "updated_at": utc_now(),
            }
        )

        if proof_failure is not None:
            raise proof_failure

    def _fresh_enabled_control(self) -> dict:
        control, hold = self.gateway.read_control_and_hold()
        control = self._validated_control(control)
        if not control["propagation_enabled"] or hold:
            raise TransientFailure("operator stop state prevents lifecycle mutation")
        return control

    @staticmethod
    def _control_identity(control: dict) -> tuple:
        return tuple(control[key] for key in sorted(REQUIRED_CONTROL_FIELDS))

    def _specification(
        self, control: dict, generation: int, predecessor: str, token: str
    ) -> dict:
        return {
            "request_id": self.config.request_id,
            "generation": f"{generation:06d}",
            "launch_template_id": control["launch_template_id"],
            "launch_template_version": control["launch_template_version"],
            "client_token": stable_token(
                "cg-create",
                self.config.request_id,
                predecessor,
                f"{generation:06d}",
                control["launch_template_id"],
                control["launch_template_version"],
            ),
            "tags": {
                "project": "cloud-glider",
                "bootstrap-request-id": self.config.request_id,
                "propagation-backend": "ec2",
                "environment": self.config.environment,
                "purpose": "generation-compute",
                "owner": self.config.owner,
                "generation": f"{generation:06d}",
                "predecessor-instance-id": predecessor,
                "handoff-token": token,
                "template-sha256": control["template_sha256"],
                "Name": f"cloud-glider-{self.config.environment}-gen-{generation:06d}",
            },
        }

    def provision_successor(self, control: dict) -> tuple[dict, str]:
        n = self.generation_number + 1
        token = stable_token(
            "handoff",
            self.gateway.instance_id,
            f"{n:06d}",
            control["launch_template_version"],
        )
        spec = self._specification(control, n, self.gateway.instance_id, token)
        # Durable intent is written before EC2 submission. An expired lease does not
        # authorize a new request after an ambiguous submission or token retention.
        self.gateway.verify_launch_template(control)
        existing = self.gateway.find_successor(spec)
        if existing:
            self.gateway.verify_instance(existing, spec)
            self.gateway.reconcile_ec2_submission(spec, existing)
            self.gateway.ensure_status_alarm(spec["generation"], existing["InstanceId"])
            return spec, existing["InstanceId"]
        self.gateway.check_capacity(int(control["max_live_generations"]))
        fresh = self._fresh_enabled_control()
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure("approved identity changed before creation")
        self._renew(control)
        if not self.is_current_owner():
            raise TransientFailure("CURRENT changed before creation")
        self.gateway.claim_submission(spec)
        fresh = self._fresh_enabled_control()  # last AWS operation before RunInstances
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure(
                "approved identity changed before submission; inspect intent"
            )
        instance_id = self.gateway.run_instance(spec)
        self.gateway.ensure_status_alarm(spec["generation"], instance_id)
        return spec, instance_id

    def _renew(self, control: dict) -> None:
        self.gateway.renew_lease(
            self.lease_owner,
            int(self.clock()) + max(60, int(control["readiness_poll_seconds"]) * 4),
        )

    def _candidate_parent_matches(self, current: dict) -> bool:
        return (
            current.get("status") == "CURRENT"
            and current.get("request_id") == self.config.request_id
            and current.get("generation") == f"{self.generation_number - 1:06d}"
            and current.get("instance_id") == self.config.predecessor_instance_id
            and current.get("launch_template_id") == self.config.launch_template_id
            and current.get("launch_template_version")
            == self.config.launch_template_version
        )

    def prove_functional_readiness(self, control: dict, current: dict) -> dict:
        """Candidate-only, read-only capability proof; never acquire ownership or launch."""
        started_at = int(self.clock())
        if not self._candidate_parent_matches(current):
            raise TransientFailure("candidate parent ownership differs")
        self.gateway.verify_launch_template(control)
        own = self.gateway.describe_instance(self.gateway.instance_id)
        if not own or own["State"]["Name"] != "running":
            raise TransientFailure("candidate instance is unavailable")
        self.gateway.verify_instance(
            own,
            self._specification(
                control,
                self.generation_number,
                self.config.predecessor_instance_id,
                self.config.handoff_token,
            ),
        )
        next_generation = self.generation_number + 1
        boundary = next_generation > control["max_generation"]
        if not boundary:
            self.gateway.check_capacity(int(control["max_live_generations"]))
        if not self._candidate_parent_matches(self.gateway.read_current()):
            raise TransientFailure("candidate parent changed during readiness")
        fresh = self._fresh_enabled_control()
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure("control changed during functional readiness")
        if not boundary:
            token = stable_token(
                "handoff",
                self.gateway.instance_id,
                f"{next_generation:06d}",
                control["launch_template_version"],
            )
            self.gateway.dry_run_instance(
                self._specification(
                    fresh, next_generation, self.gateway.instance_id, token
                )
            )
        fresh = self._fresh_enabled_control()
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure("control changed after candidate continuation")
        if not self._candidate_parent_matches(self.gateway.read_current()):
            raise TransientFailure("candidate parent changed after continuation")
        if not 0 <= self.clock() - started_at <= FUNCTIONAL_READINESS_MAX_AGE_SECONDS:
            raise TransientFailure(
                "functional readiness checks exceeded freshness budget"
            )
        return {
            "schema_version": "1",
            "producer_instance_id": self.gateway.instance_id,
            "handoff_token": self.config.handoff_token,
            "control_sha256": control_fingerprint(fresh),
            "proved_at_epoch": started_at,
            "continuation_generation": f"{next_generation:06d}",
            "continuation_status": "BOUNDARY" if boundary else "DRY_RUN_PASSED",
        }

    def _eligible_functional_readiness(
        self, state: dict, control: dict, spec: dict, instance_id: str
    ) -> bool:
        expected = {
            **self._identity(),
            "generation": spec["generation"],
            "instance_id": instance_id,
            "predecessor_instance_id": spec["tags"]["predecessor-instance-id"],
            "handoff_token": spec["tags"]["handoff-token"],
            "status": "CANDIDATE",
            "daemon_live": True,
            "observed_propagation_enabled": True,
            "observed_hold_active": False,
        }
        if not all(state.get(k) == v for k, v in expected.items()) or any(
            type(state.get(key)) is not bool
            for key in (
                "daemon_live",
                "observed_propagation_enabled",
                "observed_hold_active",
            )
        ):
            return False
        proof = state.get("functional_readiness")
        if not isinstance(proof, dict):
            return False
        expected_proof = {
            "schema_version": "1",
            "producer_instance_id": instance_id,
            "handoff_token": spec["tags"]["handoff-token"],
            "control_sha256": control_fingerprint(control),
            "continuation_generation": f"{int(spec['generation']) + 1:06d}",
            "continuation_status": (
                "BOUNDARY"
                if int(spec["generation"]) >= control["max_generation"]
                else "DRY_RUN_PASSED"
            ),
        }
        if any(proof.get(k) != v for k, v in expected_proof.items()):
            return False
        timestamp = proof.get("proved_at_epoch")
        return (
            type(timestamp) is int
            and 0 <= self.clock() - timestamp <= FUNCTIONAL_READINESS_MAX_AGE_SECONDS
        )

    def wait_for_ready_successor(
        self, control: dict, spec: dict, instance_id: str
    ) -> dict:
        deadline = self.clock() + int(control["readiness_timeout_seconds"])
        while self.clock() <= deadline:
            self._renew(control)
            fresh = self._fresh_enabled_control()
            if self._control_identity(fresh) != self._control_identity(control):
                raise TransientFailure("control changed during readiness")
            instance = self.gateway.describe_instance(instance_id)
            if not instance or instance["State"]["Name"] in (
                "stopping",
                "stopped",
                "shutting-down",
                "terminated",
            ):
                raise TransientFailure("successor unavailable; predecessor preserved")
            self.gateway.verify_instance(instance, spec)
            state = self.gateway.read_generation_state(spec["generation"])
            if (
                instance["State"]["Name"] == "running"
                and state
                and self._eligible_functional_readiness(
                    state, control, spec, instance_id
                )
            ):
                return state
            self.sleep(int(control["readiness_poll_seconds"]))
        raise TransientFailure("successor readiness timed out; predecessor preserved")

    def continuation_preflight(
        self, control: dict, spec: dict, successor_id: str
    ) -> None:
        n = int(spec["generation"]) + 1
        if n > int(control["max_generation"]):
            return
        self.gateway.verify_launch_template(control)
        self.gateway.check_capacity(int(control["max_live_generations"]))
        self._renew(control)
        fresh = self._fresh_enabled_control()
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure("control changed during continuation")
        token = stable_token(
            "handoff", successor_id, f"{n:06d}", control["launch_template_version"]
        )
        self.gateway.dry_run_instance(
            self._specification(fresh, n, successor_id, token)
        )

    def conditional_handoff(
        self, control: dict, spec: dict, instance_id: str, state: dict
    ) -> None:
        self._renew(control)
        instance = self.gateway.describe_instance(instance_id)
        if not instance or instance["State"]["Name"] != "running":
            raise TransientFailure("successor unavailable before handoff")
        self.gateway.verify_instance(instance, spec)
        state = self.gateway.read_generation_state(spec["generation"])
        if not state or not self._eligible_functional_readiness(
            state, control, spec, instance_id
        ):
            raise TransientFailure("successor readiness expired before handoff")
        fresh = self._fresh_enabled_control()
        if self._control_identity(fresh) != self._control_identity(control):
            raise TransientFailure("control changed before handoff")
        if not self._eligible_functional_readiness(state, fresh, spec, instance_id):
            raise TransientFailure(
                "functional readiness expired during final control check"
            )
        expected = {
            **self._identity(),
            "lease_owner": self.lease_owner,
            "lease_now": int(self.clock()),
            "control_identity": {k: fresh[k] for k in REQUIRED_CONTROL_FIELDS},
            "candidate": state,
        }
        successor = {
            **self._identity(),
            "generation": spec["generation"],
            "instance_id": instance_id,
            "status": "CURRENT",
            "handoff_token": spec["tags"]["handoff-token"],
            "predecessor_instance_id": self.gateway.instance_id,
            "retirement_authorized": True,
            "functional_readiness": state["functional_readiness"],
            "handoff_control_sha256": control_fingerprint(fresh),
            "continuation_generation": f"{int(spec['generation']) + 1:06d}",
            "continuation_status": (
                "BOUNDARY"
                if int(spec["generation"]) >= fresh["max_generation"]
                else "DRY_RUN_PASSED"
            ),
            "updated_at": utc_now(),
        }
        audit = {
            "event_id": str(uuid.uuid4()),
            "occurred_at": utc_now(),
            "correlation_id": self.correlation_id,
            "from_generation": self.config.generation,
            "to_generation": spec["generation"],
        }
        if not self.gateway.handoff(expected, successor, audit):
            raise TransientFailure("handoff conditions changed; predecessor preserved")

    def retire_predecessor(self, control: dict, current: dict) -> bool:
        if self.config.predecessor_instance_id == "NONE":
            return True
        if (
            current.get("predecessor_instance_id")
            != self.config.predecessor_instance_id
            or current.get("handoff_token") != self.config.handoff_token
            or current.get("retirement_authorized") is not True
        ):
            raise SafetyViolation(
                "RETIREMENT_OWNERSHIP_INVALID",
                "retirement was not authorized by handoff",
            )
        if current.get("handoff_control_sha256") != control_fingerprint(control):
            raise TransientFailure(
                "control changed since handoff; preserve predecessor"
            )
        expected_proof = (
            "BOUNDARY"
            if self.generation_number >= control["max_generation"]
            else "DRY_RUN_PASSED"
        )
        if (
            current.get("continuation_status") != expected_proof
            or current.get("continuation_generation")
            != f"{self.generation_number + 1:06d}"
        ):
            raise SafetyViolation(
                "CONTINUATION_PROOF_MISSING",
                "retirement has no matching continuation proof",
            )
        proof = current.get("functional_readiness")
        if (
            not isinstance(proof, dict)
            or type(proof.get("proved_at_epoch")) is not int
            or any(
                proof.get(key) != value
                for key, value in {
                    "schema_version": "1",
                    "producer_instance_id": self.gateway.instance_id,
                    "handoff_token": self.config.handoff_token,
                    "control_sha256": current["handoff_control_sha256"],
                    "continuation_generation": current["continuation_generation"],
                    "continuation_status": expected_proof,
                }.items()
            )
        ):
            raise SafetyViolation(
                "FUNCTIONAL_READINESS_PROOF_MISSING",
                "retirement lacks successor-produced handoff evidence",
            )
        # Freshness gated handoff; the resulting retirement authorization is durable.
        if current.get("retirement_completed") is True:
            return True
        self.gateway.verify_launch_template(control)
        own = self.gateway.describe_instance(self.gateway.instance_id)
        if not own or own["State"]["Name"] != "running":
            raise TransientFailure(
                "current instance health is ambiguous before retirement"
            )
        self.gateway.verify_instance(
            own,
            self._specification(
                control,
                self.generation_number,
                self.config.predecessor_instance_id,
                self.config.handoff_token,
            ),
        )
        self._renew(control)
        predecessor = self.gateway.describe_instance(
            self.config.predecessor_instance_id
        )
        if not predecessor:
            raise TransientFailure(
                "predecessor lookup is ambiguous; preserve retirement state"
            )
        tags = predecessor.get("Tags", {})
        if (
            tags.get("generation") != f"{self.generation_number - 1:06d}"
            or tags.get("project") != "cloud-glider"
            or tags.get("environment") != self.config.environment
            or tags.get("purpose") != "generation-compute"
            or tags.get("bootstrap-request-id") != self.config.request_id
            or tags.get("propagation-backend") != "ec2"
            or predecessor.get("LaunchTemplate")
            != {
                "LaunchTemplateId": self.config.launch_template_id,
                "Version": self.config.launch_template_version,
            }
        ):
            raise SafetyViolation(
                "PREDECESSOR_IDENTITY_MISMATCH", "predecessor identity differs"
            )
        if predecessor and predecessor["State"]["Name"] not in (
            "shutting-down",
            "terminated",
        ):
            if not self.is_current_owner():
                raise TransientFailure("retirement ownership changed")
            fresh = self._fresh_enabled_control()
            if self._control_identity(fresh) != self._control_identity(control):
                raise TransientFailure("retirement control or CURRENT changed")
            self.gateway.terminate_instance(self.config.predecessor_instance_id)
            return False
        if predecessor["State"]["Name"] != "terminated":
            return False
        if self.generation_number > 1:
            self.gateway.delete_status_alarm(
                f"{self.generation_number - 1:06d}", self.config.predecessor_instance_id
            )
        self.gateway.mark_retirement_completed(current)
        return True

    @contextmanager
    def phase(self, name: str):
        started_at, started = utc_now(), self.timing_clock()
        outcome = "PASSED"
        try:
            yield
        except TransientFailure:
            outcome = "DEFERRED"
            raise
        except Exception:
            outcome = "FAILED"
            raise
        finally:
            try:
                self.log(
                    "phase_timing",
                    phase=name,
                    started_at=started_at,
                    duration_seconds=round(self.timing_clock() - started, 6),
                    outcome=outcome,
                )
            except Exception:
                # Timing output must not mask a lifecycle result or error.
                pass

    def cycle(self) -> str:
        self._poll_seconds = 1
        control, hold, current = self.gateway.read_cycle_snapshot()
        control = self._validated_control(control)
        now = self.timing_clock()
        heartbeat_due = (
            self._last_heartbeat_at is None
            or now - self._last_heartbeat_at
            >= int(control["heartbeat_interval_seconds"])
        )
        readiness_due = (
            self._candidate_parent_matches(current)
            and not hold
            and control["propagation_enabled"]
            and (
                self._last_readiness_at is None
                or now - self._last_readiness_at >= FUNCTIONAL_READINESS_REFRESH_SECONDS
            )
        )
        if heartbeat_due or readiness_due:
            self.heartbeat(
                control,
                hold,
                current,
                telemetry_tick=heartbeat_due,
                refresh_readiness=readiness_due,
            )
            if heartbeat_due:
                self._last_heartbeat_at = now
        if not self.is_current_owner(current):
            return "CANDIDATE"
        if hold or not control["propagation_enabled"]:
            self._poll_seconds = max(60, self._poll_seconds)
            return "STOPPED_BY_OPERATOR"
        now = int(self.clock())
        if not self.gateway.acquire_lease(
            self.lease_owner,
            self.config.generation,
            now,
            now + max(60, int(control["heartbeat_interval_seconds"]) * 4),
        ):
            return "LEASE_NOT_ACQUIRED"
        try:
            if not self.is_current_owner():
                return "OWNERSHIP_CHANGED"
            with self.phase("predecessor_retirement"):
                retired = self.retire_predecessor(control, current)
            if self.generation_number >= int(control["max_generation"]):
                if not retired:
                    raise TransientFailure("predecessor termination still in progress")
                return "MAX_GENERATION_REACHED"
            with self.phase("successor_submission"):
                spec, instance_id = self.provision_successor(control)
            with self.phase("successor_readiness"):
                state = self.wait_for_ready_successor(control, spec, instance_id)
            # Keep CURRENT's durable retirement intent until physical termination.
            # The candidate may boot concurrently, but cannot take ownership yet.
            if not retired:
                with self.phase("predecessor_retirement_reconciliation"):
                    if not self.retire_predecessor(control, current):
                        raise TransientFailure(
                            "predecessor termination still in progress"
                        )
            with self.phase("continuation_dry_run"):
                self.continuation_preflight(control, spec, instance_id)
            with self.phase("conditional_handoff"):
                self.conditional_handoff(control, spec, instance_id, state)
            return "HANDOFF_COMPLETE"
        finally:
            try:
                self.gateway.release_lease(self.lease_owner)
            except TransientFailure as exc:
                self.log("lease_release_deferred", reason=str(exc))

    def run(self) -> int:
        try:
            with self.phase("startup_identity_validation"):
                self.verify_self()
            self.ensure_bootstrap_ownership()
            previous = None
            while True:
                try:
                    result = self.cycle()
                    if result != previous:
                        self.log("cycle_complete", result=result)
                    previous = result
                    if result in ("MAX_GENERATION_REACHED", "HANDOFF_COMPLETE"):
                        return 0
                except TransientFailure as exc:
                    self.log("cycle_deferred", reason=str(exc))
                    previous = None
                self.sleep(self._poll_seconds)
        except SafetyViolation as exc:
            self.log("terminal_safety_violation", error_code=exc.code, reason=str(exc))
            self.gateway.invoke_hold(
                self.config.generation, exc.code, self.correlation_id
            )
            return 2
