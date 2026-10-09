from concurrent.futures import Future
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_ec2_daemon import config, control
from cloud_glider.daemon import SafetyViolation, TransientFailure
from cloud_glider.family_daemon import ControlMonitor, FamilyDaemon
from cloud_glider.inherited import child_paths, digest, specification, user_data, validate


def family_config(binary=True, **kwargs):
    settings = {**control(), "audit_table_name": config().audit_table_name, "schema_version": "3", "binary_fanout_enabled": binary,
                "initial_propagation_enabled": True, "control_poll_seconds": 2,
                "control_max_age_seconds": 15, "retry_backoff_max_seconds": 30}
    envelope = {"settings": settings, "sha256": digest(settings)}
    return config(inherited_configuration=envelope, **kwargs)


class FixedMonitor:
    stopped = False
    def tick(self): return self.stopped
    def close(self): pass


class World:
    def __init__(self, binary=True):
        self.configs = {"r": family_config(binary)}
        self.nodes, self.ready, self.intents, self.retired = {}, {}, {}, set()
        self.stops, self.events = set(), []
        self.propagated = set()
        self.now = 100
        self.peak_live = 0
        self.agents = {}

    def gateway(self, path):
        world = self
        class Gateway:
            def read_node(self, p): return world.nodes.get(p, {})
            def stop_node(self, p): world.stops.add(p)
            def dry_run_child(self, spec): world.events.append(("dryrun", spec["node_path"]))
            def publish_readiness(self, now, has_children): world.ready[path] = now
            def parent_terminated(self): return path == "r" or path[:-1] in world.retired
            def single_launch_ready(self): return len(path) <= 2 or path[:-2] in world.retired
            def publish_propagation(self, children, instances, now):
                world.propagated.add(path)
                world.events.append(("propagated", path))
            def launch_child(self, spec):
                p = spec["node_path"]
                if p not in world.configs:
                    world.events.append(("launch", p))
                    instance = "i-" + digest({"path": p})[:17]
                    world.configs[p] = replace(world.configs[path], generation=spec["generation"],
                        node_path=p, instance_id=instance, predecessor_instance_id=world.configs[path].instance_id,
                        handoff_token=spec["tags"]["handoff-token"])
                    world.nodes[p] = {"owner": "", "instance_id": instance, "status": "CANDIDATE"}
                    world.peak_live = max(world.peak_live, len(world.configs)-len(world.retired))
                return world.configs[p].instance_id
            def accept_child(self, spec, instance, now):
                p = spec["node_path"]
                if p not in world.ready or now - world.ready[p] > 15:
                    return False
                world.nodes[p]["owner"] = instance
                world.nodes[p]["status"] = "OWNER"
                world.events.append(("handoff", p))
                return True
            def mark_leaf(self): world.nodes[path]["status"] = "LEAF"
            def authorize_retirement(self, children, now):
                if not all(world.nodes[p]["owner"] and now-world.ready.get(p, -100) <= 15
                    and (not child_paths(p, validate(world.configs[p])) or p in world.propagated) for p in children):
                    return False
                world.nodes[path]["status"] = "RETIRING"
                return True
            def retire_self(self):
                world.retired.add(path)
                world.events.append(("retire", path))
        return Gateway()

    def agent(self, path):
        if path not in self.agents:
            self.agents[path] = FamilyDaemon(self.configs[path], self.gateway(path),
                clock=lambda: self.now, monitor=FixedMonitor())
        return self.agents[path]

    def run(self):
        self.nodes["r"] = {"owner": self.configs["r"].instance_id, "status": "OWNER"}
        for _ in range(20):
            for path in list(self.configs):
                if path not in self.retired:
                    self.agent(path).cycle()
            self.now += 1


class FamilyDaemonTests(unittest.TestCase):
    def test_retirement_and_persisted_stop_exit_normally_for_diagnostic_drain(self):
        for result in ("RETIRING", "STOPPED"):
            with self.subTest(result=result), patch("cloud_glider.family_daemon.Path") as path:
                path.return_value.__truediv__.return_value.exists.return_value = False
                monitor = Mock()
                daemon = FamilyDaemon(family_config(), Mock(), monitor=monitor,
                                      logger=Mock(), sleep=Mock(side_effect=AssertionError("must exit")))
                daemon.cycle = Mock(return_value=result)
                self.assertEqual(daemon.run(), 0)
                monitor.close.assert_called_once()


    def test_candidate_readiness_needs_no_launch_or_dry_run(self):
        world = World()
        for now in (100, 106, 112):
            world.now = now
            self.assertEqual(world.agent("r").cycle(), "CANDIDATE")
            self.assertEqual(world.ready["r"], now)
        self.assertEqual(world.events, [])

    def test_configuration_change_rejects_readiness(self):
        world = World(); agent = world.agent("r")
        agent.cycle()
        agent.config.inherited_configuration["settings"]["max_generation"] = 3
        with self.assertRaises(SafetyViolation): agent.cycle()
        self.assertEqual(world.events, [])

    def test_six_generation_tree_has_no_dry_runs_and_preserves_parents_until_launch(self):
        world = World()
        settings = world.configs["r"].inherited_configuration["settings"]
        settings["max_generation"] = 5
        world.configs["r"].inherited_configuration["sha256"] = digest(settings)
        world.run()
        self.assertEqual(len(world.configs), 63)
        self.assertEqual(len(world.retired), 31)
        self.assertFalse(any(event == "dryrun" for event, _ in world.events))
        for path in world.retired:
            for child in child_paths(path, validate(world.configs[path])):
                if child_paths(child, validate(world.configs[child])):
                    self.assertLess(world.events.index(("propagated", child)), world.events.index(("retire", path)))

    def test_failed_real_child_launch_preserves_predecessor(self):
        world = World(); world.nodes["r"] = {"owner": world.configs["r"].instance_id, "status": "OWNER"}
        world.agent("r").cycle(); world.agent("r0").cycle(); world.agent("r1").cycle()
        self.assertEqual(world.agent("r").cycle(), "WAITING_FOR_CONTINUATION")
        child = world.agent("r0")
        child.gateway.launch_child = Mock(side_effect=TransientFailure("ambiguous launch"))
        with self.assertRaises(TransientFailure): child.cycle()
        self.assertNotIn("r0", world.propagated)
        self.assertNotIn("r", world.retired)

    def test_unproven_real_launch_times_out_without_replay_or_retirement(self):
        world = World(); world.nodes["r"] = {"owner": world.configs["r"].instance_id, "status": "OWNER"}
        agent = world.agent("r")
        agent.gateway.launch_child = Mock(side_effect=TransientFailure("unknown response"))
        with self.assertRaises(TransientFailure): agent.cycle()
        calls = agent.gateway.launch_child.call_count
        world.now += agent.settings["readiness_timeout_seconds"]
        with self.assertRaises(SafetyViolation) as error: agent.cycle()
        self.assertEqual(error.exception.code, "CONTINUATION_LAUNCH_TIMEOUT")
        self.assertEqual(agent.gateway.launch_child.call_count, calls)
        self.assertFalse(world.retired)

    def test_single_live_demonstration_never_exceeds_three_generations(self):
        world = World(False)
        settings = world.configs["r"].inherited_configuration["settings"]
        settings["max_generation"] = 5
        world.configs["r"].inherited_configuration["sha256"] = digest(settings)
        world.run()
        self.assertEqual(len(world.configs), 6)
        self.assertEqual(len(world.retired), 5)
        self.assertEqual(world.peak_live, 3)
        settings["max_live_generations"] = 2
        world.configs["r"].inherited_configuration["sha256"] = digest(settings)
        with self.assertRaises(SafetyViolation): FamilyDaemon(world.configs["r"], world.gateway("r"))

    def test_stop_after_launch_prevents_propagation_receipt_and_retirement(self):
        world = World(); world.nodes["r"] = {"owner": world.configs["r"].instance_id, "status": "OWNER"}
        agent = world.agent("r"); launch = agent.gateway.launch_child
        def stopped_launch(spec):
            result = launch(spec); agent.monitor.stopped = True; return result
        agent.gateway.launch_child = stopped_launch
        self.assertEqual(agent.cycle(), "STOPPED")
        self.assertFalse(world.propagated)
        self.assertFalse(world.retired)

    def test_actual_agent_binary_tree_inherits_configuration_and_retains_leaves(self):
        world = World()
        world.run()
        self.assertEqual(set(world.configs), {"r", "r0", "r1", "r00", "r01", "r10", "r11"})
        self.assertEqual(world.retired, {"r", "r0", "r1"})
        self.assertTrue(all(world.nodes[p]["status"] == "LEAF" for p in ("r00", "r01", "r10", "r11")))
        self.assertEqual(sum(event == "launch" for event, _ in world.events), 6)
        for cfg in world.configs.values():
            self.assertEqual(cfg.inherited_configuration, world.configs["r"].inherited_configuration)

    def test_single_successor_launch_proof_precedes_parent_retirement(self):
        world = World(False)
        world.run()
        self.assertEqual(set(world.configs), {"r", "r0", "r00"})
        self.assertLess(world.events.index(("launch", "r00")), world.events.index(("retire", "r")))

    def test_depth_is_local_and_tokens_distinguish_siblings_and_cycles(self):
        cfg = family_config()
        self.assertEqual(child_paths("r00", validate(cfg)), ())
        self.assertNotEqual(specification(cfg, "r0", cfg.instance_id, "h")["client_token"],
                            specification(cfg, "r1", cfg.instance_id, "h")["client_token"])
        settings = {**cfg.inherited_configuration["settings"], "request_id": "2"}
        other = replace(cfg, request_id="2", inherited_configuration={"settings": settings, "sha256": digest(settings)})
        self.assertNotEqual(specification(cfg, "r0", cfg.instance_id, "h")["client_token"],
                            specification(other, "r0", cfg.instance_id, "h")["client_token"])

    def test_tampered_config_and_invalid_lineage_fail_closed(self):
        cfg = family_config()
        cfg.inherited_configuration["settings"]["max_generation"] = 8
        with self.assertRaises(SafetyViolation): validate(cfg)
        with self.assertRaises(SafetyViolation): validate(family_config(node_path="r1"))

    def test_inherited_audit_destination_is_required_and_pinned(self):
        for destination, code in ((None, "INHERITED_CONFIG_INVALID"), ("other-audit", "AUDIT_TABLE_MISMATCH")):
            cfg = family_config()
            settings = cfg.inherited_configuration["settings"]
            if destination is None:
                settings.pop("audit_table_name")
            else:
                settings["audit_table_name"] = destination
            cfg.inherited_configuration["sha256"] = digest(settings)
            with self.assertRaises(SafetyViolation) as raised:
                validate(cfg)
            self.assertEqual(raised.exception.code, code)

    def test_user_data_contains_inherited_envelope_and_no_operator_config_fetch(self):
        cfg = family_config()
        startup = user_data(cfg)
        self.assertIn("--context user_data", startup)
        raw = json.loads(startup.split("<<'JSON'\n")[1].split("\nJSON")[0])
        self.assertEqual(raw["inherited_configuration"], cfg.inherited_configuration)
        self.assertNotIn("instance_id", raw)
        self.assertNotIn("get-item", startup)

    def test_stop_propagates_to_submitted_and_unbooted_children(self):
        world = World()
        world.nodes["r"] = {"owner": world.configs["r"].instance_id, "status": "OWNER"}
        agent = world.agent("r")
        agent.cycle()
        agent.monitor.stopped = True
        self.assertEqual(agent.cycle(), "STOPPED")
        self.assertEqual(world.stops, {"r", "r0", "r1"})
        self.assertFalse(world.retired)

    def test_failed_child_handoff_preserves_parent(self):
        world = World()
        world.nodes["r"] = {"owner": world.configs["r"].instance_id, "status": "OWNER"}
        self.assertEqual(world.agent("r").cycle(), "WAITING_FOR_CHILD")
        self.assertNotIn("r", world.retired)

    def test_stop_is_persisted_before_remote_cancellation(self):
        world = World()
        agent = world.agent("r")
        with tempfile.TemporaryDirectory() as directory:
            agent.stop_file = Path(directory) / "stop-1-r"
            agent.gateway.stop_node = lambda path: (_ for _ in ()).throw(TransientFailure("DDB unavailable"))
            with self.assertRaises(TransientFailure): agent.stop_children()
            self.assertEqual(agent.stop_file.read_text(), agent.config.inherited_configuration["sha256"])


class ManualExecutor:
    def __init__(self): self.futures = []
    def submit(self, fn):
        future = Future()
        self.futures.append(future)
        return future
    def shutdown(self, **kwargs): pass


class ControlMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.executor = ManualExecutor()
        self.monitor = ControlMonitor(object(), validate(family_config()),
            clock=lambda: self.now, executor=self.executor)

    def test_read_runs_without_wait_and_only_one_is_outstanding(self):
        self.monitor.gateway = type("Gateway", (), {"poll_stopped": lambda self: False})()
        self.assertFalse(self.monitor.tick())
        self.now = 3
        self.assertFalse(self.monitor.tick())
        self.assertEqual(len(self.executor.futures), 1)

    def test_stop_latches_and_old_enabled_response_does_not_resume(self):
        self.monitor.gateway = type("Gateway", (), {"poll_stopped": lambda self: False})()
        self.monitor.tick()
        self.executor.futures[0].set_result(True)
        self.now = 3
        self.assertTrue(self.monitor.tick())
        self.executor.futures[-1].set_result(False)
        self.now = 6
        self.assertTrue(self.monitor.tick())

    def test_control_failure_or_delayed_response_does_not_extend_freshness(self):
        for result in (False, RuntimeError("transport")):
            self.setUp()
            self.monitor.gateway = type("Gateway", (), {"poll_stopped": lambda self: False})()
            self.monitor.tick()
            self.now = 16
            future = self.executor.futures[0]
            if isinstance(result, Exception): future.set_exception(result)
            else: future.set_result(result)
            self.assertTrue(self.monitor.tick())

class IndependentMonitorTests(unittest.TestCase):
    def test_polling_continues_while_lifecycle_worker_is_blocked(self):
        import threading
        reads = []
        observed = threading.Event()
        def poll():
            reads.append(1)
            if len(reads) >= 2: observed.set()
            return False
        gateway = Mock(poll_stopped=poll)
        settings = {**validate(family_config()), 'control_poll_seconds': .01}
        monitor = ControlMonitor(gateway, settings)
        try:
            monitor.start()
            # The caller performs no tick: represents a blocked SDK call/backoff.
            self.assertTrue(observed.wait(2))
            self.assertFalse(monitor.stopped)
        finally:
            monitor.close()
