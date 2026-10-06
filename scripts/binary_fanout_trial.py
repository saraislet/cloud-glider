#!/usr/bin/env python3
"""Offline protocol experiment: two children, levels 0–2, no live ceiling.

This model never imports an AWS SDK and is not a deployable propagation backend.
Each step represents one authoritative observation or conditional operation.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field


class Blocked(RuntimeError):
    """Preserve occupied nodes when controls or evidence block progress."""


@dataclass(frozen=True)
class Readiness:
    cycle: str
    path: str
    token: str
    instance_id: str
    template_digest: str
    agent_digest: str
    owner: str
    revision: int
    observed_at: int
    workload_passed: bool
    continuation_passed: bool


@dataclass
class Node:
    path: str
    phase: str = "RESERVED"
    ready: bool = False
    completion: bool = False
    token: str = ""
    cycle: str = ""
    instance_id: str = ""
    owner: str = ""
    revision: int = 0
    evidence: Readiness | None = None
    receipt: Readiness | None = None
    completed_at: int | None = None


@dataclass
class BinaryTrial:
    cycle: str = "offline-trial-1"
    enabled: bool = True
    hold: bool = False
    nodes: dict[str, Node] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    peak_occupied: int = 0
    # Test-only transport/evidence faults, keyed by exact node path.
    faults: dict[str, str] = field(default_factory=dict)
    now: int = 0
    simulate_evidence: bool = True
    template_digest: str = "a" * 64
    agent_digest: str = "b" * 64

    MAX_GENERATION = 2
    CHILDREN = 2
    MAX_EVIDENCE_AGE = 15

    def _fence(self, node: Node) -> None:
        token = hashlib.sha256(f"{self.cycle}:{node.path}".encode()).hexdigest()
        if node.cycle != self.cycle or node.token != token:
            raise Blocked("node belongs to a different cycle or token")

    def _simulate_observations(self) -> None:
        """Fake successor workload producer; never real agent/AWS evidence."""
        for node in self.nodes.values():
            if node.phase not in ("LIVE", "COMPLETE"):
                continue
            fault = self.faults.get(node.path)
            if fault == "missing":
                node.evidence = None
                continue
            node.evidence = Readiness(
                node.cycle, node.path, node.token,
                "wrong-instance" if fault == "identity" else node.instance_id,
                self.template_digest, self.agent_digest, node.owner, node.revision,
                self.now - 16 if fault == "stale" else self.now,
                fault != "health", fault != "continuation")

    def _validate_evidence(self, node: Node) -> Readiness:
        self._fence(node)
        proof = node.evidence
        if proof is None or (
                proof.cycle, proof.path, proof.token, proof.instance_id,
                proof.template_digest, proof.agent_digest, proof.owner, proof.revision
        ) != (self.cycle, node.path, node.token, node.instance_id,
              self.template_digest, self.agent_digest, node.owner, node.revision):
            raise Blocked("missing or mismatched readiness evidence")
        if not node.owner or not node.instance_id or not (
                0 <= self.now - proof.observed_at <= self.MAX_EVIDENCE_AGE):
            raise Blocked("readiness evidence is stale or future dated")
        if proof.workload_passed is not True or proof.continuation_passed is not True:
            raise Blocked("workload or successor continuation did not pass")
        return proof

    def _complete(self, node: Node, expected: Readiness) -> None:
        self._control()
        actual = self._validate_evidence(node)
        if node.phase != "LIVE" or actual != expected or self.faults.get(node.path) == "handoff":
            raise Blocked("conditional completion snapshot or ownership changed")
        node.receipt = actual
        node.completed_at = self.now
        node.completion = True
        node.phase = "COMPLETE"
        node.revision += 1

    def checkpoint(self) -> "BinaryTrial":
        return copy.deepcopy(self)

    def occupied(self) -> int:
        # Submission reservations and retiring instances still consume capacity.
        return sum(n.phase != "TERMINATED" for n in self.nodes.values())

    def _control(self) -> None:
        if not self.enabled or self.hold:
            raise Blocked("propagation disabled or emergency hold active")

    def _event(self, action: str, path: str) -> None:
        occupied = self.occupied()
        self.peak_occupied = max(self.peak_occupied, occupied)
        self.events.append({"action": action, "node": path,
                            "generation": len(path) - 1, "occupied": occupied})

    def _advance(self, path: str) -> bool:
        if path not in self.nodes:
            self._control()
            token = hashlib.sha256(f"{self.cycle}:{path}".encode()).hexdigest()
            self.nodes[path] = Node(path, token=token, cycle=self.cycle)
            self._event("RESERVE", path)
            return True

        node = self.nodes[path]
        self._fence(node)
        fault = self.faults.get(path)
        if node.phase == "TERMINATED":
            if not node.completion or node.receipt is None or (
                    node.receipt.cycle, node.receipt.path, node.receipt.token,
                    node.receipt.instance_id, node.receipt.template_digest,
                    node.receipt.agent_digest) != (
                    self.cycle, path, node.token, node.instance_id,
                    self.template_digest, self.agent_digest):
                raise Blocked("termination is not completion evidence")
            if (node.completed_at is None or not node.receipt.workload_passed
                    or not node.receipt.continuation_passed
                    or node.receipt.owner != node.owner
                    or node.receipt.revision != node.revision - 1
                    or not 0 <= node.completed_at - node.receipt.observed_at <= self.MAX_EVIDENCE_AGE):
                raise Blocked("invalid historical subtree completion receipt")
            return False
        if node.phase == "SUBMITTING":
            raise Blocked("ambiguous launch; reconcile exact token before retry")
        if node.phase == "RETIRING":
            # Confirming an already accepted operation is permitted after stop.
            if fault in ("termination", "missing"):
                raise Blocked("termination unconfirmed; slot remains occupied")
            node.phase = "TERMINATED"
            self._event("TERMINATION_CONFIRMED", path)
            return True

        self._control()
        if node.phase == "RESERVED":
            node.phase = "SUBMITTING"
            self._event("SUBMIT", path)
            if fault == "launch_response":
                raise Blocked("launch response lost; reservation retained")
            node.phase = "LIVE"
            node.instance_id = "sim-" + node.token[:16]
            node.owner = node.instance_id
            self._event("LAUNCH_CONFIRMED", path)
            return True
        if node.phase == "LIVE" and not node.ready:
            self._validate_evidence(node)
            node.ready = True
            self._event("READINESS_VALIDATED", path)
            return True
        if node.phase == "LIVE":
            self._validate_evidence(node)
            if len(path) - 1 < self.MAX_GENERATION:
                for index in range(self.CHILDREN):
                    if self._advance(path + str(index)):
                        return True
            # Interior completion depends on two durable subtree receipts;
            # leaf completion depends on its own exact functional readiness.
            self._control()
            self._complete(node, self._validate_evidence(node))
            self._event("CONDITIONAL_COMPLETION", path)
            return True
        if node.phase == "COMPLETE":
            self._validate_evidence(node)
            if fault in ("identity", "missing", "retirement"):
                raise Blocked("verified retirement unavailable; preserve instance")
            node.phase = "RETIRING"
            self._event("TERMINATION_ACCEPTED", path)
            return True
        raise Blocked("unknown node phase")

    def reconcile_launch(self, path: str, *, cycle: str, token: str,
                         instance_found: bool) -> None:
        """Model an exact external observation; absence never authorizes reissue."""
        node = self.nodes[path]
        self._fence(node)
        if (cycle != self.cycle or token != node.token
                or node.phase != "SUBMITTING" or not instance_found):
            raise Blocked("launch reconciliation lacks exact cycle/token/instance")
        node.phase = "LIVE"
        node.instance_id = "sim-" + node.token[:16]
        node.owner = node.instance_id
        self._event("LAUNCH_RECONCILED", path)

    def step(self) -> bool:
        # Fence the entire checkpoint before any observation or mutation.
        for node in self.nodes.values():
            self._fence(node)
        if self.simulate_evidence:
            self._simulate_observations()
        # Expand the bounded tree before retirement; siblings no longer wait
        # for earlier subtrees to terminate to obtain an occupancy slot.
        for path in sorted(self.nodes, key=lambda p: (len(p), p)):
            node = self.nodes[path]
            if node.phase in ("RESERVED", "SUBMITTING") or (
                    node.phase == "LIVE" and not node.ready):
                self._check_ancestors(path)
                return self._advance(path)
            if node.phase == "LIVE" and node.ready and len(path) - 1 < self.MAX_GENERATION:
                self._check_ancestors(path)
                self._validate_evidence(node)
                for index in range(self.CHILDREN):
                    child = path + str(index)
                    if child not in self.nodes:
                        return self._advance(child)
        return self._advance("r")

    def _check_ancestors(self, path: str) -> None:
        for length in range(1, len(path)):
            ancestor = self.nodes[path[:length]]
            self._validate_evidence(ancestor)
            if (ancestor.phase != "LIVE" or not ancestor.ready or
                    self.faults.get(ancestor.path) in ("identity", "health", "stale", "missing")):
                raise Blocked("fresh ancestor evidence required before descendant work")

    def run(self) -> dict:
        for _ in range(100):
            if not self.step():
                return self.report()
        raise AssertionError("bounded trial did not finish")

    def report(self) -> dict:
        return {"mode": "OFFLINE_MODEL", "cycle": self.cycle,
                "max_generation": self.MAX_GENERATION,
                "max_live_generations": None,
                "levels": 3, "children_per_interior_node": self.CHILDREN,
                "total_nodes": len(self.nodes),
                "nodes_per_generation": [sum(len(p) - 1 == g for p in self.nodes)
                                         for g in range(3)],
                "peak_occupied": self.peak_occupied,
                "remaining_occupied": self.occupied(),
                "complete": len(self.nodes) == 7 and all(
                    n.completion and n.phase == "TERMINATED"
                    for n in self.nodes.values()),
                "events": self.events}


if __name__ == "__main__":
    print(json.dumps(BinaryTrial().run(), indent=2, sort_keys=True))
