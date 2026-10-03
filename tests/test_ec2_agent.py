import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from cloud_glider.ec2_agent import control_fingerprint
from cloud_glider.ec2_agent import (
    Ec2Agent as Agent,
    Ec2AgentConfig as AgentConfig,
    SafetyViolation,
    TransientFailure,
)

CURRENT = "i-" + "1" * 17
CHILD = "i-" + "2" * 17
LT = "lt-" + "a" * 17


class FakeClock:
    def __init__(self):
        self.value = 1_800_000_000

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def config(**overrides):
    values = dict(
        request_id="1",
        generation_table_name="cloud-glider-sandbox-generations",
        environment="sandbox",
        owner="operator",
        generation="000000",
        instance_id=CURRENT,
        predecessor_instance_id="NONE",
        handoff_token="OPERATOR_BOOTSTRAP",
        launch_template_id=LT,
        launch_template_version="1",
        state_table_name="cloud-glider-sandbox-state",
        bootstrap_version="bootstrap-v1",
        template_version="template-v1",
        template_bucket="artifacts",
        template_key="generation/generation.yaml",
        template_s3_version_id="template-object-v1",
        template_sha256="a" * 64,
        template_build_id="abcdef123456",
        agent_artifact_bucket="artifacts",
        agent_artifact_key="generation/agent.tar.gz",
        agent_artifact_version_id="agent-object-v1",
        agent_artifact_sha256="b" * 64,
        emergency_hold_function_name="cloud-glider-sandbox-emergency-hold",
        propagation_audit_log_group="/cloud-glider/sandbox/audit/propagation",
        agent_operations_log_group="/cloud-glider/sandbox/agent/operations",
        operational_alerts_topic_arn="arn:aws:sns:us-west-2:111122223333:cloud-glider-sandbox-operational-alerts",
    )
    return AgentConfig(**{**values, **overrides})


def control(**overrides):
    values = dict(
        request_id="1",
        generation_table_name="cloud-glider-sandbox-generations",
        cleanup_requested=False,
        cleanup_status="IDLE",
        propagation_backend="ec2",
        propagation_enabled=True,
        desired_template_version="template-v1",
        desired_bootstrap_version="bootstrap-v1",
        template_s3_bucket="artifacts",
        template_s3_key="generation/generation.yaml",
        template_s3_version_id="template-object-v1",
        template_sha256="a" * 64,
        template_build_id="abcdef123456",
        agent_artifact_bucket="artifacts",
        agent_artifact_key="generation/agent.tar.gz",
        agent_artifact_version_id="agent-object-v1",
        agent_artifact_sha256="b" * 64,
        max_generation=2,
        max_live_generations=3,
        concurrency_model="EC2_DRY_RUN_THEN_RETIRE",
        approved_region="us-west-2",
        approved_account_id="111122223333",
        approved_architecture="arm64",
        approved_instance_types=["t4g.micro"],
        environment="sandbox",
        readiness_poll_seconds=2,
        readiness_required_heartbeats=2,
        heartbeat_interval_seconds=5,
        readiness_timeout_seconds=30,
        launch_template_id=LT,
        launch_template_version="1",
        launch_template_sha256="c" * 64,
    )
    return {**values, **overrides}


class FakeGateway:
    region, account_id = "us-west-2", "111122223333"

    def __init__(self, cfg, clock):
        self.cfg, self.clock, self.instance_id = cfg, clock, cfg.instance_id
        self.control = control()
        self.hold_active = False
        self.current = {
            "request_id": cfg.request_id,
            "generation": cfg.generation,
            "instance_id": cfg.instance_id,
            "status": "CURRENT",
            "launch_template_id": LT,
            "launch_template_version": "1",
        }
        self.instances = {
            cfg.instance_id: self.instance(
                cfg.instance_id,
                cfg.generation,
                cfg.predecessor_instance_id,
                cfg.handoff_token,
            )
        }
        self.calls, self.created, self.intent = [], None, None
        self.successor_id = CHILD if cfg.instance_id != CHILD else "i-" + "3" * 17
        self.reads = 0
        self.lease_result, self.handoff_result = True, True
        self.after_claim = None
        self.timeout_once = False

    @staticmethod
    def instance(identifier, generation, parent, token):
        return {
            "InstanceId": identifier,
            "State": {"Name": "running"},
            "LaunchTemplate": {"LaunchTemplateId": LT, "Version": "1"},
            "Tags": {
                "bootstrap-request-id": "1",
                "propagation-backend": "ec2",
                "project": "cloud-glider",
                "environment": "sandbox",
                "purpose": "generation-compute",
                "generation": generation,
                "predecessor-instance-id": parent,
                "handoff-token": token,
            },
        }

    def read_control_and_hold(self):
        self.calls.append("control")
        return dict(self.control), self.hold_active

    def read_current(self):
        return dict(self.current)

    def claim_initial_current(self, identity):
        self.current = identity
        return True

    def write_heartbeat(self, state):
        self.calls.append("heartbeat")

    def acquire_lease(self, *args):
        self.calls.append("acquire")
        return self.lease_result

    def renew_lease(self, *args):
        self.calls.append("renew")

    def release_lease(self, *args):
        self.calls.append("release")

    def describe_instance(self, identifier):
        return self.instances.get(identifier)

    def verify_launch_template(self, control):
        self.calls.append("verify-template")

    def verify_instance(self, instance, spec):
        for key in ("generation", "predecessor-instance-id", "handoff-token"):
            if instance["Tags"][key] != spec["tags"][key]:
                raise SafetyViolation("INSTANCE_IDENTITY_MISMATCH", "lineage differs")

    def find_successor(self, spec):
        return self.instances.get(self.successor_id) if self.created else None

    def check_capacity(self, maximum):
        self.calls.append("capacity")
        if (
            sum(i["State"]["Name"] != "terminated" for i in self.instances.values())
            >= maximum
        ):
            raise TransientFailure("live generation ceiling reached")

    def claim_submission(self, spec):
        self.calls.append("claim")
        if self.intent:
            raise TransientFailure("ambiguous intent")
        self.intent = spec
        if self.after_claim:
            self.after_claim()

    def reconcile_ec2_submission(self, spec, instance):
        self.intent = None

    def run_instance(self, spec):
        self.calls.append("create")
        self.created = spec
        self.instances[self.successor_id] = self.instance(
            self.successor_id,
            spec["generation"],
            spec["tags"]["predecessor-instance-id"],
            spec["tags"]["handoff-token"],
        )
        if self.timeout_once:
            self.timeout_once = False
            raise TransientFailure("timeout after submission")
        return self.successor_id

    def read_generation_state(self, generation):
        self.reads += 1
        c = self.cfg
        return {
            "request_id": self.cfg.request_id,
            "generation": generation,
            "instance_id": self.successor_id,
            "launch_template_id": LT,
            "launch_template_version": "1",
            "predecessor_instance_id": c.instance_id,
            "handoff_token": self.created["tags"]["handoff-token"],
            "template_version": c.template_version,
            "template_s3_version_id": c.template_s3_version_id,
            "template_sha256": c.template_sha256,
            "template_build_id": c.template_build_id,
            "bootstrap_version": c.bootstrap_version,
            "agent_artifact_version_id": c.agent_artifact_version_id,
            "agent_artifact_sha256": c.agent_artifact_sha256,
            "status": "CANDIDATE",
            "workload_healthy": True,
            "observed_propagation_enabled": True,
            "observed_hold_active": False,
            "heartbeat_sequence": self.reads,
            "heartbeat_at_epoch": self.clock(),
        }

    def dry_run_instance(self, spec):
        self.calls.append("dry-run")

    def handoff(self, expected, successor, audit):
        self.calls.append("handoff")
        if self.handoff_result:
            self.current = successor
        return self.handoff_result

    def ensure_status_alarm(self, generation, instance_id):
        self.calls.append("alarm")

    def delete_status_alarm(self, generation, instance_id):
        self.calls.append("delete-alarm")

    def mark_retirement_completed(self, current):
        self.current["retirement_completed"] = True

    def terminate_instance(self, instance_id):
        self.calls.append("terminate")
        self.instances[instance_id]["State"]["Name"] = "shutting-down"

    def invoke_hold(self, *args):
        self.calls.append("hold")


class AgentTests(unittest.TestCase):
    def setup_agent(self, cfg=None):
        cfg = cfg or config()
        clock = FakeClock()
        gateway = FakeGateway(cfg, clock)
        agent = Agent(
            cfg, gateway, clock=clock, sleep=clock.sleep, logger=lambda _: None
        )
        agent.verify_self()
        gateway.calls.clear()
        return agent, gateway, clock

    def test_phase_timing_keeps_success_deferred_and_failure_distinct(self):
        import json

        a, _, _ = self.setup_agent()
        records = []
        a.logger = lambda raw: records.append(json.loads(raw))
        for error, expected in (
            (None, "PASSED"),
            (TransientFailure("wait"), "DEFERRED"),
            (SafetyViolation("TEST", "conflict"), "FAILED"),
        ):
            ticks = iter([10.0, 12.5])
            a.timing_clock = lambda: next(ticks)
            try:
                with a.phase("fixture"):
                    if error:
                        raise error
            except (TransientFailure, SafetyViolation) as exc:
                self.assertIs(exc, error)
            self.assertEqual(records[-1]["outcome"], expected)
            self.assertEqual(records[-1]["duration_seconds"], 2.5)
            self.assertEqual(records[-1]["request_id"], a.config.request_id)

    def test_disabled_or_hold_preserves_instances(self):
        for enabled, hold in ((False, False), (True, True), (False, True)):
            a, g, _ = self.setup_agent()
            g.control["propagation_enabled"], g.hold_active = enabled, hold
            self.assertEqual(a.cycle(), "STOPPED_BY_OPERATOR")
            self.assertEqual(g.calls, ["control", "heartbeat"])
            self.assertEqual(a._poll_seconds, 60)

    def test_duplicate_without_lease_does_not_create(self):
        a, g, _ = self.setup_agent()
        g.lease_result = False
        self.assertEqual(a.cycle(), "LEASE_NOT_ACQUIRED")
        self.assertNotIn("create", g.calls)

    def test_owner_change_after_acquire_preserves_instances(self):
        a, g, _ = self.setup_agent()

        def acquire(*args):
            g.current["instance_id"] = CHILD
            return True

        g.acquire_lease = acquire
        self.assertEqual(a.cycle(), "OWNERSHIP_CHANGED")
        self.assertNotIn("create", g.calls)

    def test_final_stop_or_hold_gate_blocks_ec2_submission(self):
        for hold in (False, True):
            a, g, _ = self.setup_agent()

            def stop():
                if hold:
                    g.hold_active = True
                else:
                    g.control["propagation_enabled"] = False

            g.after_claim = stop
            with self.assertRaises(TransientFailure):
                a.cycle()
            self.assertNotIn("create", g.calls)
            self.assertEqual(g.calls[-1], "release")

    def test_direct_launch_and_dry_run_precede_handoff(self):
        a, g, _ = self.setup_agent()
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertLess(g.calls.index("dry-run"), g.calls.index("handoff"))
        self.assertNotIn("terminate", g.calls)
        self.assertTrue(g.current["retirement_authorized"])
        self.assertEqual(g.current["predecessor_instance_id"], CURRENT)

    def test_lost_launch_response_reconciles_without_duplicate(self):
        a, g, _ = self.setup_agent()
        g.timeout_once = True
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(g.calls.count("create"), 1)

    def test_ambiguous_submission_without_instance_is_not_resubmitted(self):
        a, g, _ = self.setup_agent()
        g.intent = {"old": "intent"}
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertNotIn("create", g.calls)

    def test_conflicting_successor_preserves_parent(self):
        a, g, _ = self.setup_agent()
        spec, _ = a.provision_successor(g.control)
        g.instances[CHILD]["Tags"]["handoff-token"] = "foreign"
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("handoff", g.calls)

    def test_running_without_authoritative_health_cannot_handoff(self):
        a, g, _ = self.setup_agent()
        g.read_generation_state = lambda _: None
        with self.assertRaisesRegex(TransientFailure, "readiness timed out"):
            a.cycle()
        self.assertNotIn("handoff", g.calls)

    def test_stale_or_ambiguous_heartbeat_cannot_handoff(self):
        for change in (
            {"workload_healthy": False},
            {"observed_hold_active": True},
            {"launch_template_version": "2"},
            {"heartbeat_at_epoch": 1},
            {"handoff_token": "foreign"},
        ):
            a, g, _ = self.setup_agent()
            original = g.read_generation_state
            g.read_generation_state = lambda n: {**original(n), **change}
            with self.assertRaises(TransientFailure):
                a.cycle()
            self.assertNotIn("handoff", g.calls)

    def test_failed_continuation_preserves_predecessor(self):
        a, g, _ = self.setup_agent()
        g.dry_run_instance = Mock(side_effect=TransientFailure("unauthorized"))
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertNotIn("handoff", g.calls)

    def test_terminal_successor_does_not_require_next_generation(self):
        a, g, _ = self.setup_agent()
        g.control["max_generation"] = 1
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertNotIn("dry-run", g.calls)

    def test_failed_handoff_preserves_parent(self):
        a, g, _ = self.setup_agent()
        g.handoff_result = False
        with self.assertRaisesRegex(TransientFailure, "handoff conditions"):
            a.cycle()
        self.assertNotIn("terminate", g.calls)
        self.assertEqual(g.current["instance_id"], CURRENT)

    def child_agent(self):
        a, g, clock = self.setup_agent(
            config(
                generation="000001",
                instance_id=CHILD,
                predecessor_instance_id=CURRENT,
                handoff_token="token",
            )
        )
        g.instances[CURRENT] = g.instance(
            CURRENT, "000000", "NONE", "OPERATOR_BOOTSTRAP"
        )
        g.current.update(
            predecessor_instance_id=CURRENT,
            handoff_token="token",
            retirement_authorized=True,
        )
        g.control["max_generation"] = 1
        g.current.update(
            handoff_control_sha256=control_fingerprint(g.control),
            continuation_status="BOUNDARY",
            continuation_generation="000002",
        )
        return a, g, clock

    def test_child_retries_retirement_before_terminal_exit(self):
        a, g, _ = self.child_agent()
        with self.assertRaisesRegex(TransientFailure, "still in progress"):
            a.cycle()
        self.assertNotIn("create", g.calls)
        with self.assertRaisesRegex(TransientFailure, "still in progress"):
            a.cycle()
        g.instances[CURRENT]["State"]["Name"] = "terminated"
        self.assertEqual(a.cycle(), "MAX_GENERATION_REACHED")
        self.assertEqual(g.calls.count("terminate"), 1)

    def overlap_agent(self):
        a, g, clock = self.child_agent()
        g.control["max_generation"] = 3
        g.current.update(
            handoff_control_sha256=control_fingerprint(g.control),
            continuation_status="DRY_RUN_PASSED",
        )
        return a, g, clock

    def test_launch_overlaps_retirement_but_handoff_waits_and_restart_recovers(self):
        a, g, clock = self.overlap_agent()
        with self.assertRaisesRegex(TransientFailure, "still in progress"):
            a.cycle()
        self.assertLess(g.calls.index("terminate"), g.calls.index("create"))
        self.assertNotIn("handoff", g.calls)
        self.assertEqual(g.current["instance_id"], CHILD)
        restarted = Agent(
            a.config, g, clock=clock, sleep=clock.sleep, logger=lambda _: None
        )
        with self.assertRaises(TransientFailure):
            restarted.cycle()
        self.assertEqual(g.calls.count("create"), 1)
        self.assertEqual(g.calls.count("terminate"), 1)
        g.instances[CURRENT]["State"]["Name"] = "terminated"
        self.assertEqual(restarted.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(g.calls.count("create"), 1)

    def test_two_slot_limit_and_failed_retirement_block_launch(self):
        a, g, _ = self.overlap_agent()
        g.control["max_live_generations"] = 2
        g.current["handoff_control_sha256"] = control_fingerprint(g.control)
        with self.assertRaisesRegex(TransientFailure, "ceiling"):
            a.cycle()
        self.assertNotIn("create", g.calls)

    def test_stop_and_hold_after_overlap_preserve_owner_and_candidate(self):
        for hold in (False, True):
            a, g, _ = self.overlap_agent()
            with self.assertRaises(TransientFailure):
                a.cycle()
            g.calls.clear()
            g.hold_active = hold
            g.control["propagation_enabled"] = hold
            self.assertEqual(a.cycle(), "STOPPED_BY_OPERATOR")
            self.assertEqual(g.calls, ["control", "heartbeat"])

    def test_child_stop_preserves_predecessor_even_at_terminal_generation(self):
        a, g, _ = self.child_agent()
        g.control["propagation_enabled"] = False
        self.assertEqual(a.cycle(), "STOPPED_BY_OPERATOR")
        self.assertNotIn("terminate", g.calls)

    def test_unowned_retirement_is_rejected(self):
        a, g, _ = self.child_agent()
        g.current["retirement_authorized"] = False
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_foreign_predecessor_is_preserved(self):
        a, g, _ = self.child_agent()
        g.instances[CURRENT]["Tags"]["generation"] = "000123"
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_missing_or_failed_retirement_blocks_further_propagation(self):
        a, g, _ = self.child_agent()
        g.instances.pop(CURRENT)
        with self.assertRaisesRegex(TransientFailure, "ambiguous"):
            a.cycle()
        self.assertNotIn("create", g.calls)
        a, g, _ = self.child_agent()
        g.terminate_instance = Mock(side_effect=TransientFailure("termination failed"))
        with self.assertRaisesRegex(TransientFailure, "termination failed"):
            a.cycle()
        self.assertNotIn("create", g.calls)
        self.assertNotIn("retirement_completed", g.current)

    def test_completed_retirement_survives_ec2_history_expiry(self):
        a, g, _ = self.child_agent()
        g.current["retirement_completed"] = True
        g.instances.pop(CURRENT)
        self.assertEqual(a.cycle(), "MAX_GENERATION_REACHED")

    def test_stopped_or_foreign_child_cannot_retire_parent(self):
        a, g, _ = self.child_agent()
        g.instances[CHILD]["State"]["Name"] = "stopped"
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_alarm_failure_after_launch_reconciles_existing_instance(self):
        a, g, _ = self.setup_agent()
        g.ensure_status_alarm = Mock(
            side_effect=[TransientFailure("alarm unavailable"), None]
        )
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(g.calls.count("create"), 1)

    def test_malformed_control_types_trigger_safety_hold(self):
        a, g, _ = self.setup_agent()
        for change in (
            {"max_generation": True},
            {"readiness_poll_seconds": "2"},
            {"launch_template_sha256": None},
        ):
            with self.assertRaises(SafetyViolation):
                a._validated_control(control(**change))

    def test_changed_limit_or_missing_proof_preserves_predecessor_after_handoff(self):
        a, g, _ = self.child_agent()
        g.control["max_generation"] = 2
        with self.assertRaisesRegex(TransientFailure, "changed since handoff"):
            a.cycle()
        self.assertNotIn("terminate", g.calls)
        a, g, _ = self.child_agent()
        g.current.pop("continuation_status")
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_template_alias_and_foreign_account_rejected(self):
        a, g, _ = self.setup_agent()
        for change in (
            {"launch_template_version": "$Latest"},
            {"approved_account_id": "999999999999"},
            {"max_live_generations": 4},
            {"concurrency_model": "PREFLIGHT_THEN_RETIRE"},
        ):
            with self.assertRaises(SafetyViolation):
                a._validated_control(control(**change))

    def test_non_seed_cannot_claim_uninitialized_current(self):
        a, g, _ = self.child_agent()
        g.current = {"status": "UNINITIALIZED"}
        with self.assertRaises(SafetyViolation):
            a.ensure_bootstrap_ownership()

    def test_parent_exits_after_handoff_and_identity_violation_holds(self):
        a, g, _ = self.setup_agent()
        self.assertEqual(a.run(), 0)
        a, g, _ = self.setup_agent()
        g.control["launch_template_version"] = "2"
        self.assertEqual(a.run(), 2)
        self.assertIn("hold", g.calls)


if __name__ == "__main__":
    unittest.main()
