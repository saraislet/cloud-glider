"""Independent EC2 families with inherited configuration and asynchronous stop."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import time
import uuid

from .daemon import SafetyViolation, TransientFailure
from .inherited import child_paths, specification, validate


class ControlMonitor:
    """One outstanding control read; stop is monotonic for this agent/cycle."""
    def __init__(self, gateway, settings, *, clock=time.monotonic, executor=None):
        self.gateway, self.settings, self.clock = gateway, settings, clock
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="glider-control")
        self.stopped = not settings["initial_propagation_enabled"]
        self.observed_at = clock()
        self.submitted_at = None
        self.next_poll = 0
        self.pending = None

    def tick(self):
        now = self.clock()
        if self.pending is not None and self.pending.done():
            try:
                self.stopped |= self.pending.result()
                # Age starts at request submission, not at a delayed response.
                self.observed_at = self.submitted_at
            except Exception:
                pass  # A failed read never refreshes the last good observation.
            self.pending = None
        if now - self.observed_at >= self.settings["control_max_age_seconds"]:
            self.stopped = True
        if self.pending is None and now >= self.next_poll:
            self.submitted_at = now
            self.pending = self.executor.submit(self.gateway.poll_stopped)
            self.next_poll = now + self.settings["control_poll_seconds"]
        return self.stopped

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)


class FamilyDaemon:
    def __init__(self, config, gateway, *, clock=time.time, sleep=time.sleep,
                 logger=print, monitor=None):
        self.config, self.gateway = config, gateway
        self.settings = validate(config)
        self.clock, self.sleep, self.logger = clock, sleep, logger
        self.monitor = monitor or ControlMonitor(gateway, self.settings)
        self.correlation_id = str(uuid.uuid4())
        self.children = child_paths(config.node_path, self.settings)
        self.last_ready = 0
        self.wait_started = None
        self.stop_file = None
        self.cancelled_paths = set()

    def spec(self, path):
        return specification(self.config, path, self.config.instance_id,
                             "handoff-" + path)

    def stop_children(self):
        if self.stop_file is not None and not self.stop_file.exists():
            temporary = self.stop_file.with_suffix(".tmp")
            with temporary.open("w") as stream:
                stream.write(self.config.inherited_configuration["sha256"])
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.stop_file)
        # Durable stop is published even before a child boots or is discoverable.
        for path in (self.config.node_path, *self.children):
            if path not in self.cancelled_paths:
                self.gateway.stop_node(path)
                self.cancelled_paths.add(path)

    def cycle(self):
        if self.monitor.tick():
            self.stop_children()
            return "STOPPED"
        node = self.gateway.read_node(self.config.node_path)
        if node.get("status") == "RETIRING":
            self.gateway.retire_self()
            return "RETIRING"
        if self.clock() - self.last_ready >= 5:
            # The successor itself exercises its next-hop request authorization.
            for path in self.children:
                if self.monitor.tick():
                    self.stop_children()
                    return "STOPPED"
                self.gateway.dry_run_child(self.spec(path))
            self.gateway.publish_readiness(self.clock(), bool(self.children))
            self.last_ready = self.clock()
        if node.get("owner") != self.config.instance_id:
            return "CANDIDATE"
        if not self.children:
            if node.get("status") != "LEAF":
                self.gateway.mark_leaf()
            return "LEAF"  # Keep terminal leaves until explicit operator cleanup.
        # Single mode retains accepted-retirement overlap without a global count.
        if not self.settings["binary_fanout_enabled"] and not self.gateway.parent_launch_ready():
            return "WAITING_FOR_PARENT"
        if self.wait_started is None:
            self.wait_started = self.clock()
        # Separate child submissions can overlap; no global owner/capacity lock.
        with ThreadPoolExecutor(max_workers=len(self.children)) as workers:
            futures = []
            for path in self.children:
                if self.monitor.tick():
                    self.stop_children()
                    return "STOPPED"
                futures.append(workers.submit(self.gateway.launch_child, self.spec(path)))
            instances = [future.result() for future in futures]
        for path, instance in zip(self.children, instances):
            if self.monitor.tick():
                self.stop_children()
                return "STOPPED"
            if not self.gateway.accept_child(self.spec(path), instance, self.clock()):
                if self.clock() - self.wait_started > self.settings["readiness_timeout_seconds"]:
                    raise SafetyViolation("CHILD_READINESS_TIMEOUT", "parent preserved; operator inspection required")
                return "WAITING_FOR_CHILD"
        # Descendants may launch before parent termination in binary mode, but
        # they cannot retire ahead of their own parent and invalidate its gate.
        if not self.gateway.parent_terminated():
            return "WAITING_FOR_PARENT_RETIREMENT"
        if self.monitor.tick():
            self.stop_children()
            return "STOPPED"
        if not self.gateway.authorize_retirement(self.children, self.clock()):
            return "WAITING_FOR_CHILD"
        self.gateway.retire_self()
        return "RETIRING"

    def run(self):
        try:
            state_dir = Path("/var/lib/cloud-glider")
            state_dir.mkdir(parents=True, exist_ok=True)
            self.stop_file = state_dir / f"stop-{self.config.request_id}-{self.config.node_path}"
            if self.stop_file.exists():
                if self.stop_file.read_text() != self.config.inherited_configuration["sha256"]:
                    raise SafetyViolation("LOCAL_STOP_CONFLICT", "persisted stop belongs to different configuration")
                self.monitor.stopped = True
            self.gateway.verify_family_self()
            self.gateway.initialize_node()
            failures = 0
            while True:
                delay = self.settings["readiness_poll_seconds"]
                try:
                    started = time.monotonic()
                    result = self.cycle()
                    self.logger(json.dumps({"event": "family_cycle", "request_id": self.config.request_id,
                        "node_path": self.config.node_path, "generation": self.config.generation,
                        "instance_id": self.config.instance_id, "result": result,
                        "duration_seconds": round(time.monotonic() - started, 6)}, sort_keys=True))
                    failures = 0
                    if result in ("RETIRING", "STOPPED"):
                        # Finish the process normally so the entrypoint drains
                        # durable diagnostics before EC2 shutdown or local stop.
                        return 0
                except TransientFailure as exc:
                    self.logger(str(exc))
                    failures += 1
                    maximum = min(self.settings["retry_backoff_max_seconds"], delay * 2 ** min(failures, 10))
                    delay = random.uniform(delay, maximum)
                self.sleep(delay)
        except SafetyViolation as exc:
            self.logger(f"{exc.code}: {exc}")
            self.monitor.stopped = True
            try:
                self.stop_children()
            except TransientFailure as stop_error:
                self.logger(str(stop_error))
            self.gateway.invoke_hold(self.config.generation, exc.code, self.correlation_id)
            return 2
        finally:
            self.monitor.close()
