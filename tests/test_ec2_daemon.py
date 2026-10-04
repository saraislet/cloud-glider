import dataclasses
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))
from cloud_glider.ec2_daemon import control_fingerprint
from cloud_glider.ec2_daemon import (
    Ec2Daemon as Daemon,
    Ec2DaemonConfig as DaemonConfig,
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
        daemon_artifact_bucket="artifacts",
        daemon_artifact_key="generation/daemon.tar.gz",
        daemon_artifact_version_id="daemon-object-v1",
        daemon_artifact_sha256="b" * 64,
        emergency_hold_function_name="cloud-glider-sandbox-emergency-hold",
        propagation_audit_log_group="/cloud-glider/sandbox/audit/propagation",
        daemon_operations_log_group="/cloud-glider/sandbox/daemon/operations",
        operational_alerts_topic_arn="arn:aws:sns:us-west-2:111122223333:cloud-glider-sandbox-operational-alerts",
    )
    return DaemonConfig(**{**values, **overrides})


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
        daemon_artifact_bucket="artifacts",
        daemon_artifact_key="generation/daemon.tar.gz",
        daemon_artifact_version_id="daemon-object-v1",
        daemon_artifact_sha256="b" * 64,
        max_generation=2,
        max_live_generations=3,
        concurrency_model="EC2_DRY_RUN_THEN_RETIRE",
        approved_region="us-west-2",
        approved_account_id="111122223333",
        approved_architecture="arm64",
        approved_instance_types=["t4g.micro"],
        environment="sandbox",
        readiness_poll_seconds=1,
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

    def read_cycle_snapshot(self):
        c, h = self.read_control_and_hold()
        return c, h, self.read_current()

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
            "daemon_artifact_version_id": c.daemon_artifact_version_id,
            "daemon_artifact_sha256": c.daemon_artifact_sha256,
            "status": "CANDIDATE",
            "daemon_live": True,
            "functional_readiness": {
                "schema_version": "1",
                "producer_instance_id": self.successor_id,
                "handoff_token": self.created["tags"]["handoff-token"],
                "control_sha256": control_fingerprint(self.control),
                "proved_at_epoch": self.clock(),
                "continuation_generation": f"{int(generation) + 1:06d}",
                "continuation_status": (
                    "BOUNDARY"
                    if int(generation) >= self.control["max_generation"]
                    else "DRY_RUN_PASSED"
                ),
            },
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


class DaemonTests(unittest.TestCase):
    def setup_daemon(self, cfg=None):
        cfg = cfg or config()
        clock = FakeClock()
        gateway = FakeGateway(cfg, clock)
        daemon = Daemon(
            cfg,
            gateway,
            clock=clock,
            sleep=clock.sleep,
            logger=lambda _: None,
            timing_clock=clock,
        )
        daemon.verify_self()
        gateway.calls.clear()
        return daemon, gateway, clock

    def test_candidate_polls_each_second_but_heartbeats_remain_five_seconds_apart(self):
        daemon, gateway, clock = self.setup_daemon()
        gateway.current["instance_id"] = CHILD
        for second in range(6):
            self.assertEqual(daemon.cycle(), "CANDIDATE")
            self.assertEqual(daemon._poll_seconds, 1)
            clock.sleep(1)
        self.assertEqual(gateway.calls.count("control"), 6)
        self.assertEqual(gateway.calls.count("heartbeat"), 2)
        self.assertEqual(daemon.heartbeat_sequence, 2)

    def test_functional_readiness_removes_fixed_heartbeat_wait(self):
        for interval, expected in ((1, 0), (2, 0)):
            with self.subTest(interval=interval):
                daemon, gateway, clock = self.setup_daemon()
                gateway.control["readiness_poll_seconds"] = interval
                spec, identifier = daemon.provision_successor(gateway.control)
                gateway.calls.clear()
                start = clock()
                state = daemon.wait_for_ready_successor(
                    gateway.control, spec, identifier
                )
                self.assertEqual(state["instance_id"], CHILD)
                self.assertEqual(clock() - start, expected)
                self.assertEqual(gateway.calls.count("renew"), gateway.reads)
                self.assertEqual(gateway.calls.count("control"), gateway.reads)

    def test_stop_on_ownership_transition_is_seen_between_heartbeats(self):
        for hold in (False, True):
            with self.subTest(hold=hold):
                daemon, gateway, clock = self.setup_daemon()
                current = dict(gateway.current)
                gateway.current["instance_id"] = CHILD
                self.assertEqual(daemon.cycle(), "CANDIDATE")
                clock.sleep(1)
                gateway.current = current
                gateway.hold_active = hold
                gateway.control["propagation_enabled"] = hold
                self.assertEqual(daemon.cycle(), "STOPPED_BY_OPERATOR")
                self.assertEqual(gateway.calls.count("heartbeat"), 1)
                self.assertNotIn("acquire", gateway.calls)
                self.assertFalse(gateway.created)

    def test_phase_timing_keeps_success_deferred_and_failure_distinct(self):
        import json

        a, _, _ = self.setup_daemon()
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
            a, g, _ = self.setup_daemon()
            g.control["propagation_enabled"], g.hold_active = enabled, hold
            self.assertEqual(a.cycle(), "STOPPED_BY_OPERATOR")
            self.assertEqual(g.calls, ["control", "heartbeat"])
            self.assertEqual(a._poll_seconds, 60)

    def test_duplicate_without_lease_does_not_create(self):
        a, g, _ = self.setup_daemon()
        g.lease_result = False
        self.assertEqual(a.cycle(), "LEASE_NOT_ACQUIRED")
        self.assertNotIn("create", g.calls)

    def test_owner_change_after_acquire_preserves_instances(self):
        a, g, _ = self.setup_daemon()

        def acquire(*args):
            g.current["instance_id"] = CHILD
            return True

        g.acquire_lease = acquire
        self.assertEqual(a.cycle(), "OWNERSHIP_CHANGED")
        self.assertNotIn("create", g.calls)

    def test_final_stop_or_hold_gate_blocks_ec2_submission(self):
        for hold in (False, True):
            a, g, _ = self.setup_daemon()

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
        a, g, _ = self.setup_daemon()
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertLess(g.calls.index("dry-run"), g.calls.index("handoff"))
        self.assertNotIn("terminate", g.calls)
        self.assertTrue(g.current["retirement_authorized"])
        self.assertEqual(g.current["predecessor_instance_id"], CURRENT)

    def test_lost_launch_response_reconciles_without_duplicate(self):
        a, g, _ = self.setup_daemon()
        g.timeout_once = True
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(g.calls.count("create"), 1)

    def test_ambiguous_submission_without_instance_is_not_resubmitted(self):
        a, g, _ = self.setup_daemon()
        g.intent = {"old": "intent"}
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertNotIn("create", g.calls)

    def test_conflicting_successor_preserves_parent(self):
        a, g, _ = self.setup_daemon()
        spec, _ = a.provision_successor(g.control)
        g.instances[CHILD]["Tags"]["handoff-token"] = "foreign"
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("handoff", g.calls)

    def test_running_without_authoritative_health_cannot_handoff(self):
        a, g, _ = self.setup_daemon()
        g.read_generation_state = lambda _: None
        with self.assertRaisesRegex(TransientFailure, "readiness timed out"):
            a.cycle()
        self.assertNotIn("handoff", g.calls)

    def test_stale_or_ambiguous_heartbeat_cannot_handoff(self):
        for change in (
            {"daemon_live": False},
            {"daemon_live": 1},
            {"observed_propagation_enabled": 1},
            {"observed_hold_active": 0},
            {"functional_readiness": None},
            {"observed_hold_active": True},
            {"launch_template_version": "2"},
            {"functional_readiness": {}},
            {"handoff_token": "foreign"},
        ):
            a, g, _ = self.setup_daemon()
            original = g.read_generation_state
            g.read_generation_state = lambda n: {**original(n), **change}
            with self.assertRaises(TransientFailure):
                a.cycle()
            self.assertNotIn("handoff", g.calls)

    def candidate(self):
        cfg = config(
            generation="000001",
            instance_id=CHILD,
            predecessor_instance_id=CURRENT,
            handoff_token="candidate-token",
        )
        a, g, clock = self.setup_daemon(cfg)
        g.current.update(generation="000000", instance_id=CURRENT)
        return a, g, clock

    def test_exact_successor_produces_own_functional_proof_without_ownership(self):
        a, g, _ = self.candidate()
        published = []
        g.write_heartbeat = published.append
        self.assertEqual(a.cycle(), "CANDIDATE")
        proof = published[0]["functional_readiness"]
        self.assertEqual(proof["producer_instance_id"], CHILD)
        self.assertEqual(proof["continuation_status"], "DRY_RUN_PASSED")
        self.assertIn("dry-run", g.calls)
        for operation in ("create", "claim", "acquire", "handoff", "terminate"):
            self.assertNotIn(operation, g.calls)
        self.assertNotIn("workload_healthy", published[0])

    def test_readiness_refresh_is_independent_of_heartbeat_spacing(self):
        a, g, clock = self.candidate()
        g.control["heartbeat_interval_seconds"] = 60
        published = []
        g.write_heartbeat = published.append
        a.cycle()
        clock.sleep(5)
        a.cycle()
        self.assertEqual(len(published), 2)
        self.assertEqual(g.calls.count("dry-run"), 2)
        self.assertEqual([s["heartbeat_sequence"] for s in published], [1, 1])
        self.assertEqual(
            published[0]["heartbeat_at_epoch"], published[1]["heartbeat_at_epoch"]
        )
        self.assertEqual(
            published[1]["functional_readiness"]["proved_at_epoch"]
            - published[0]["functional_readiness"]["proved_at_epoch"],
            5,
        )

    def test_parent_accepts_actual_candidate_proof_and_preserves_it_in_current(self):
        parent, pg, clock = self.setup_daemon()
        spec, child_id = parent.provision_successor(pg.control)
        cfg = dataclasses.replace(
            parent.config,
            generation="000001",
            instance_id=child_id,
            predecessor_instance_id=CURRENT,
            handoff_token=spec["tags"]["handoff-token"],
        )
        cg = FakeGateway(cfg, clock)
        cg.current, cg.control, cg.instances = pg.current, pg.control, pg.instances
        candidate = Daemon(
            cfg,
            cg,
            clock=clock,
            sleep=clock.sleep,
            logger=lambda _: None,
            timing_clock=clock,
        )
        candidate.verify_self()
        published = []
        cg.write_heartbeat = published.append
        self.assertEqual(candidate.cycle(), "CANDIDATE")
        pg.read_generation_state = lambda _: dict(published[-1])
        self.assertEqual(parent.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(
            pg.current["functional_readiness"], published[-1]["functional_readiness"]
        )
        self.assertIn("dry-run", cg.calls)
        self.assertEqual(pg.calls.count("create"), 1)
        self.assertNotIn("create", cg.calls)

    def test_candidate_failed_refresh_clears_previous_proof(self):
        a, g, clock = self.candidate()
        published = []
        g.write_heartbeat = published.append
        a.cycle()
        self.assertIsNotNone(published[-1]["functional_readiness"])
        clock.sleep(5)
        g.dry_run_instance = Mock(side_effect=TransientFailure("denied"))
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertIsNone(published[-1]["functional_readiness"])

    def test_candidate_parent_control_capacity_and_probe_duration_fail_closed(self):
        for failure in (
            "parent-before",
            "parent-after",
            "stop-after",
            "hold-after",
            "control-after",
            "capacity",
            "slow",
        ):
            with self.subTest(failure=failure):
                a, g, clock = self.candidate()
                if failure == "parent-before":
                    g.current["instance_id"] = "i-" + "4" * 17
                elif failure == "capacity":
                    g.check_capacity = Mock(
                        side_effect=TransientFailure("capacity unavailable")
                    )
                else:

                    def during_dry_run(spec):
                        g.calls.append("dry-run")
                        if failure == "parent-after":
                            g.current["instance_id"] = "i-" + "4" * 17
                        elif failure == "stop-after":
                            g.control["propagation_enabled"] = False
                        elif failure == "hold-after":
                            g.hold_active = True
                        elif failure == "control-after":
                            g.control["max_generation"] += 1
                        elif failure == "slow":
                            clock.sleep(16)

                    g.dry_run_instance = during_dry_run
                with self.assertRaises(TransientFailure):
                    a.prove_functional_readiness(g.control.copy(), g.current.copy())
                for operation in ("claim", "create", "handoff", "terminate", "acquire"):
                    self.assertNotIn(operation, g.calls)

    def test_candidate_dry_run_uses_its_own_next_hop_identity(self):
        a, g, _ = self.candidate()
        g.dry_run_instance = Mock()
        a.prove_functional_readiness(g.control, g.current)
        spec = g.dry_run_instance.call_args.args[0]
        self.assertEqual(spec["generation"], "000002")
        self.assertEqual(spec["tags"]["predecessor-instance-id"], CHILD)
        self.assertEqual(spec["launch_template_id"], LT)
        self.assertEqual(spec["launch_template_version"], "1")

    def test_proof_expiring_during_final_controls_preserves_parent(self):
        a, g, clock = self.setup_daemon()
        spec, child_id = a.provision_successor(g.control)
        state = g.read_generation_state(spec["generation"])

        def slow_fresh_control():
            clock.sleep(16)
            return g.control.copy()

        a._fresh_enabled_control = slow_fresh_control
        with self.assertRaisesRegex(TransientFailure, "expired during final"):
            a.conditional_handoff(g.control, spec, child_id, state)
        self.assertNotIn("handoff", g.calls)
        self.assertNotIn("terminate", g.calls)

    def test_parent_continuation_alone_cannot_substitute_for_candidate_proof(self):
        a, g, _ = self.setup_daemon()
        original = g.read_generation_state
        g.read_generation_state = lambda n: {
            **original(n),
            "functional_readiness": None,
        }
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertNotIn("handoff", g.calls)

    def test_candidate_boundary_proof_does_not_attempt_next_hop(self):
        a, g, _ = self.candidate()
        g.control["max_generation"] = 1
        proof = a.prove_functional_readiness(g.control, g.current)
        self.assertEqual(proof["continuation_status"], "BOUNDARY")
        self.assertNotIn("dry-run", g.calls)

    def test_candidate_failed_capability_does_not_publish_ready(self):
        a, g, _ = self.candidate()
        g.write_heartbeat = Mock()
        g.dry_run_instance = Mock(side_effect=TransientFailure("denied"))
        with self.assertRaises(TransientFailure):
            a.cycle()
        g.write_heartbeat.assert_called_once()
        self.assertIsNone(g.write_heartbeat.call_args.args[0]["functional_readiness"])

    def test_candidate_stop_or_hold_publishes_no_proof(self):
        for hold in (False, True):
            a, g, _ = self.candidate()
            g.hold_active = hold
            g.control["propagation_enabled"] = hold
            published = []
            g.write_heartbeat = published.append
            self.assertEqual(a.cycle(), "CANDIDATE")
            self.assertIsNone(published[0]["functional_readiness"])
            self.assertNotIn("dry-run", g.calls)

    def test_proof_rejects_stale_future_foreign_and_wrong_continuation(self):
        for changes in (
            {"proved_at_epoch": 1},
            {"proved_at_epoch": 9_000_000_000},
            {"proved_at_epoch": True},
            {"producer_instance_id": CURRENT},
            {"handoff_token": "foreign"},
            {"control_sha256": "0" * 64},
            {"continuation_status": "BOUNDARY"},
            {"continuation_generation": "000004"},
        ):
            a, g, _ = self.setup_daemon()
            original = g.read_generation_state

            def changed(n):
                state = original(n)
                state["functional_readiness"].update(changes)
                return state

            g.read_generation_state = changed
            with self.assertRaises(TransientFailure):
                a.cycle()
            self.assertNotIn("handoff", g.calls)

    def test_failed_continuation_preserves_predecessor(self):
        a, g, _ = self.setup_daemon()
        g.dry_run_instance = Mock(side_effect=TransientFailure("unauthorized"))
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertNotIn("handoff", g.calls)

    def test_terminal_successor_does_not_require_next_generation(self):
        a, g, _ = self.setup_daemon()
        g.control["max_generation"] = 1
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertNotIn("dry-run", g.calls)

    def test_failed_handoff_preserves_parent(self):
        a, g, _ = self.setup_daemon()
        g.handoff_result = False
        with self.assertRaisesRegex(TransientFailure, "handoff conditions"):
            a.cycle()
        self.assertNotIn("terminate", g.calls)
        self.assertEqual(g.current["instance_id"], CURRENT)

    def child_daemon(self):
        a, g, clock = self.setup_daemon(
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
        g.current["functional_readiness"] = {
            "schema_version": "1",
            "producer_instance_id": CHILD,
            "handoff_token": "token",
            "control_sha256": control_fingerprint(g.control),
            "proved_at_epoch": clock(),
            "continuation_generation": "000002",
            "continuation_status": "BOUNDARY",
        }
        return a, g, clock

    def test_retirement_requires_durable_successor_functional_evidence(self):
        a, g, _ = self.child_daemon()
        g.current.pop("functional_readiness")
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_delayed_retirement_keeps_authorization_after_proof_freshness_expires(self):
        a, g, clock = self.child_daemon()
        clock.sleep(60)
        with self.assertRaisesRegex(TransientFailure, "still in progress"):
            a.cycle()
        self.assertIn("terminate", g.calls)

    def test_child_retries_retirement_before_terminal_exit(self):
        a, g, _ = self.child_daemon()
        with self.assertRaisesRegex(TransientFailure, "still in progress"):
            a.cycle()
        self.assertNotIn("create", g.calls)
        with self.assertRaisesRegex(TransientFailure, "still in progress"):
            a.cycle()
        g.instances[CURRENT]["State"]["Name"] = "terminated"
        self.assertEqual(a.cycle(), "MAX_GENERATION_REACHED")
        self.assertEqual(g.calls.count("terminate"), 1)

    def overlap_daemon(self):
        a, g, clock = self.child_daemon()
        g.control["max_generation"] = 3
        g.current.update(
            handoff_control_sha256=control_fingerprint(g.control),
            continuation_status="DRY_RUN_PASSED",
        )
        g.current["functional_readiness"].update(
            control_sha256=control_fingerprint(g.control),
            continuation_status="DRY_RUN_PASSED",
        )
        return a, g, clock

    def test_launch_overlaps_retirement_but_handoff_waits_and_restart_recovers(self):
        a, g, clock = self.overlap_daemon()
        with self.assertRaisesRegex(TransientFailure, "still in progress"):
            a.cycle()
        self.assertLess(g.calls.index("terminate"), g.calls.index("create"))
        self.assertNotIn("handoff", g.calls)
        self.assertEqual(g.current["instance_id"], CHILD)
        restarted = Daemon(
            a.config,
            g,
            clock=clock,
            sleep=clock.sleep,
            logger=lambda _: None,
            timing_clock=clock,
        )
        with self.assertRaises(TransientFailure):
            restarted.cycle()
        self.assertEqual(g.calls.count("create"), 1)
        self.assertEqual(g.calls.count("terminate"), 1)
        g.instances[CURRENT]["State"]["Name"] = "terminated"
        self.assertEqual(restarted.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(g.calls.count("create"), 1)

    def test_two_slot_limit_and_failed_retirement_block_launch(self):
        a, g, _ = self.overlap_daemon()
        g.control["max_live_generations"] = 2
        g.current["handoff_control_sha256"] = control_fingerprint(g.control)
        g.current["functional_readiness"]["control_sha256"] = control_fingerprint(
            g.control
        )
        with self.assertRaisesRegex(TransientFailure, "ceiling"):
            a.cycle()
        self.assertNotIn("create", g.calls)

    def test_stop_and_hold_after_overlap_preserve_owner_and_candidate(self):
        for hold in (False, True):
            a, g, _ = self.overlap_daemon()
            with self.assertRaises(TransientFailure):
                a.cycle()
            g.calls.clear()
            g.hold_active = hold
            g.control["propagation_enabled"] = hold
            self.assertEqual(a.cycle(), "STOPPED_BY_OPERATOR")
            self.assertEqual(g.calls, ["control"])

    def test_child_stop_preserves_predecessor_even_at_terminal_generation(self):
        a, g, _ = self.child_daemon()
        g.control["propagation_enabled"] = False
        self.assertEqual(a.cycle(), "STOPPED_BY_OPERATOR")
        self.assertNotIn("terminate", g.calls)

    def test_unowned_retirement_is_rejected(self):
        a, g, _ = self.child_daemon()
        g.current["retirement_authorized"] = False
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_foreign_predecessor_is_preserved(self):
        a, g, _ = self.child_daemon()
        g.instances[CURRENT]["Tags"]["generation"] = "000123"
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_missing_or_failed_retirement_blocks_further_propagation(self):
        a, g, _ = self.child_daemon()
        g.instances.pop(CURRENT)
        with self.assertRaisesRegex(TransientFailure, "ambiguous"):
            a.cycle()
        self.assertNotIn("create", g.calls)
        a, g, _ = self.child_daemon()
        g.terminate_instance = Mock(side_effect=TransientFailure("termination failed"))
        with self.assertRaisesRegex(TransientFailure, "termination failed"):
            a.cycle()
        self.assertNotIn("create", g.calls)
        self.assertNotIn("retirement_completed", g.current)

    def test_completed_retirement_survives_ec2_history_expiry(self):
        a, g, _ = self.child_daemon()
        g.current["retirement_completed"] = True
        g.instances.pop(CURRENT)
        self.assertEqual(a.cycle(), "MAX_GENERATION_REACHED")

    def test_stopped_or_foreign_child_cannot_retire_parent(self):
        a, g, _ = self.child_daemon()
        g.instances[CHILD]["State"]["Name"] = "stopped"
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_alarm_failure_after_launch_reconciles_existing_instance(self):
        a, g, _ = self.setup_daemon()
        g.ensure_status_alarm = Mock(
            side_effect=[TransientFailure("alarm unavailable"), None]
        )
        with self.assertRaises(TransientFailure):
            a.cycle()
        self.assertEqual(a.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(g.calls.count("create"), 1)

    def test_malformed_control_types_trigger_safety_hold(self):
        a, g, _ = self.setup_daemon()
        for change in (
            {"max_generation": True},
            {"readiness_poll_seconds": "2"},
            {"launch_template_sha256": None},
        ):
            with self.assertRaises(SafetyViolation):
                a._validated_control(control(**change))

    def test_changed_limit_or_missing_proof_preserves_predecessor_after_handoff(self):
        a, g, _ = self.child_daemon()
        g.control["max_generation"] = 2
        with self.assertRaisesRegex(TransientFailure, "changed since handoff"):
            a.cycle()
        self.assertNotIn("terminate", g.calls)
        a, g, _ = self.child_daemon()
        g.current.pop("continuation_status")
        with self.assertRaises(SafetyViolation):
            a.cycle()
        self.assertNotIn("terminate", g.calls)

    def test_template_alias_and_foreign_account_rejected(self):
        a, g, _ = self.setup_daemon()
        for change in (
            {"launch_template_version": "$Latest"},
            {"approved_account_id": "999999999999"},
            {"max_live_generations": 4},
            {"concurrency_model": "PREFLIGHT_THEN_RETIRE"},
        ):
            with self.assertRaises(SafetyViolation):
                a._validated_control(control(**change))

    def test_non_seed_cannot_claim_uninitialized_current(self):
        a, g, _ = self.child_daemon()
        g.current = {"status": "UNINITIALIZED"}
        with self.assertRaises(SafetyViolation):
            a.ensure_bootstrap_ownership()

    def test_parent_exits_after_handoff_and_identity_violation_holds(self):
        a, g, _ = self.setup_daemon()
        self.assertEqual(a.run(), 0)
        a, g, _ = self.setup_daemon()
        g.control["launch_template_version"] = "2"
        self.assertEqual(a.run(), 2)
        self.assertIn("hold", g.calls)


if __name__ == "__main__":
    unittest.main()
