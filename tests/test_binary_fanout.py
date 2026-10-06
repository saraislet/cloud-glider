import sys
from dataclasses import replace
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from binary_fanout_trial import BinaryTrial, Blocked


class BinaryFanoutTests(unittest.TestCase):
    def test_three_levels_two_children_without_live_ceiling(self):
        trial = BinaryTrial()
        result = trial.run()
        self.assertTrue(result["complete"])
        self.assertEqual(result["nodes_per_generation"], [1, 2, 4])
        self.assertEqual(result["total_nodes"], 7)
        self.assertEqual(result["peak_occupied"], 7)
        self.assertIsNone(result["max_live_generations"])
        self.assertEqual(result["remaining_occupied"], 0)
        launches = [e["node"] for e in trial.events if e["action"] == "SUBMIT"]
        self.assertEqual(launches, ["r", "r0", "r1", "r00", "r01", "r10", "r11"])
        first_completion = next(i for i, e in enumerate(trial.events)
                                if e["action"] == "CONDITIONAL_COMPLETION")
        self.assertEqual(sum(e["action"] == "READINESS_VALIDATED"
                             for e in trial.events[:first_completion]), 7)
        for path, node in trial.nodes.items():
            if len(path) < 3:
                self.assertTrue(all(trial.nodes[path + str(i)].completion for i in range(2)))
            self.assertTrue(node.ready)

    def test_stop_and_hold_at_every_operation_preserve_resources(self):
        baseline = BinaryTrial()
        checkpoints = []
        while baseline.step():
            checkpoints.append(baseline.checkpoint())
        for checkpoint in checkpoints:
            for switch in ("enabled", "hold"):
                trial = checkpoint.checkpoint()
                setattr(trial, switch, switch == "hold")
                before = len(trial.events)
                try:
                    trial.step()
                except Blocked:
                    pass
                self.assertTrue(all(e["action"] == "TERMINATION_CONFIRMED"
                                    for e in trial.events[before:]))

    def test_health_identity_and_failed_handoff_preserve_ancestors(self):
        for fault in ("health", "identity", "stale", "missing", "handoff"):
            with self.subTest(fault=fault):
                trial = BinaryTrial(faults={"r00": fault})
                with self.assertRaises(Blocked):
                    trial.run()
                self.assertEqual(trial.nodes["r"].phase, "LIVE")
                self.assertEqual(trial.nodes["r0"].phase, "LIVE")
                self.assertGreaterEqual(trial.occupied(), 3)
                self.assertFalse(trial.nodes["r00"].completion)

    def test_unconfirmed_termination_stays_occupied_and_preserves_parent(self):
        trial = BinaryTrial(faults={"r00": "termination"})
        with self.assertRaises(Blocked):
            trial.run()
        self.assertEqual(trial.nodes["r00"].phase, "RETIRING")
        self.assertEqual(trial.occupied(), 7)
        self.assertIn("r01", trial.nodes)
        self.assertFalse(trial.nodes["r0"].completion)
        trial.faults.clear()
        self.assertTrue(trial.run()["complete"])

    def test_lost_response_is_not_reissued_and_wrong_cycle_is_fenced(self):
        trial = BinaryTrial(faults={"r00": "launch_response"})
        with self.assertRaises(Blocked):
            trial.run()
        trial = trial.checkpoint()
        for _ in range(3):
            with self.assertRaises(Blocked):
                trial.step()
        node = trial.nodes["r00"]
        for cycle, token, found in (("other-cycle", node.token, True),
                                    (trial.cycle, "wrong-token", True),
                                    (trial.cycle, node.token, False)):
            with self.assertRaises(Blocked):
                trial.reconcile_launch("r00", cycle=cycle, token=token,
                                       instance_found=found)
        trial.reconcile_launch("r00", cycle=trial.cycle, token=node.token,
                               instance_found=True)
        trial.faults.clear()
        self.assertTrue(trial.run()["complete"])
        self.assertEqual(sum(e["action"] == "SUBMIT" and e["node"] == "r00"
                             for e in trial.events), 1)

    def test_restart_after_every_step_and_duplicate_completion(self):
        trial = BinaryTrial()
        while trial.step():
            trial = trial.checkpoint()
        before = trial.report()
        self.assertEqual(trial.run(), before)
        self.assertEqual(len({n.token for n in trial.nodes.values()}), 7)

    def test_failed_retirement_preserves_completed_leaf_and_parent(self):
        trial = BinaryTrial(faults={"r00": "retirement"})
        with self.assertRaises(Blocked):
            trial.run()
        self.assertEqual(trial.nodes["r00"].phase, "COMPLETE")
        self.assertEqual(trial.occupied(), 7)

    def test_ancestor_readiness_change_blocks_descendant_launch(self):
        trial = BinaryTrial()
        while not trial.nodes.get("r") or not trial.nodes["r"].ready:
            trial.step()
        trial.faults["r"] = "stale"
        with self.assertRaises(Blocked):
            trial.step()
        self.assertEqual(set(trial.nodes), {"r"})

    def ready_root(self):
        trial = BinaryTrial()
        while not trial.nodes.get("r") or not trial.nodes["r"].ready:
            trial.step()
        trial.simulate_evidence = False
        return trial

    def test_readiness_requires_explicit_exact_fresh_workload_evidence(self):
        changes = ({"cycle": "other"}, {"path": "r1"}, {"token": "wrong"},
                   {"instance_id": "wrong"}, {"template_digest": "c" * 64},
                   {"agent_digest": "c" * 64}, {"owner": "other"},
                   {"revision": 99}, {"observed_at": -16}, {"observed_at": 1},
                   {"workload_passed": False}, {"continuation_passed": False})
        for change in changes:
            with self.subTest(change=change):
                trial = self.ready_root()
                trial.nodes["r"].evidence = replace(trial.nodes["r"].evidence, **change)
                before = len(trial.events)
                with self.assertRaises(Blocked):
                    trial.step()
                self.assertEqual(len(trial.events), before)
        trial = self.ready_root()
        trial.nodes["r"].evidence = None
        with self.assertRaises(Blocked):
            trial.step()

    def test_real_clock_expiry_blocks_even_without_stale_fault(self):
        trial = self.ready_root()
        trial.now += 16
        with self.assertRaises(Blocked):
            trial.step()
        self.assertEqual(set(trial.nodes), {"r"})

    def test_cycle_change_is_fenced_at_every_checkpoint(self):
        trial = BinaryTrial()
        checkpoints = [trial.checkpoint()]
        while trial.step():
            checkpoints.append(trial.checkpoint())
        for checkpoint in checkpoints[1:]:
            checkpoint.cycle = "different-cycle"
            before = checkpoint.checkpoint()
            with self.assertRaises(Blocked):
                checkpoint.step()
            self.assertEqual(checkpoint.nodes, before.nodes)
            self.assertEqual(checkpoint.events, before.events)

    def test_changed_ownership_or_revision_rejects_conditional_completion(self):
        for field, value in (("owner", "other-worker"), ("revision", 99)):
            trial = BinaryTrial()
            while "r00" not in trial.nodes or not trial.nodes["r00"].ready:
                trial.step()
            node = trial.nodes["r00"]
            expected = node.evidence
            setattr(node, field, value)
            trial._simulate_observations()
            with self.assertRaises(Blocked):
                trial._complete(node, expected)
            self.assertFalse(node.completion)
            self.assertIsNone(node.receipt)

    def test_retirement_revalidates_health_freshness_and_continuation(self):
        for fault in ("health", "stale", "continuation", "identity", "missing"):
            trial = BinaryTrial()
            while "r00" not in trial.nodes or trial.nodes["r00"].phase != "COMPLETE":
                trial.step()
            trial.faults["r00"] = fault
            with self.assertRaises(Blocked):
                trial.step()
            self.assertEqual(trial.nodes["r00"].phase, "COMPLETE")
            self.assertEqual(trial.nodes["r0"].phase, "LIVE")

    def test_missing_subtree_receipt_blocks_parent_completion(self):
        trial = BinaryTrial()
        while "r00" not in trial.nodes or trial.nodes["r00"].phase != "TERMINATED":
            trial.step()
        trial.nodes["r00"].receipt = None
        with self.assertRaises(Blocked):
            trial.step()
        self.assertFalse(trial.nodes["r0"].completion)


if __name__ == "__main__":
    unittest.main()
