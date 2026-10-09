"""Best-effort local timing telemetry; never an input to lifecycle decisions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import os
from collections import deque

_exporter = None
_early = deque(maxlen=128)
_early_dropped = 0

def export_record(record):
    global _early_dropped
    if os.environ.get("CLOUD_GLIDER_TIMING_EXPORT") != "1":
        return
    try:
        from .timing_export import sanitize
        safe = sanitize(record)
        if safe is None:
            return
        if _exporter is not None:
            _exporter.enqueue(safe)
        elif len(_early) < _early.maxlen:
            _early.append(safe)
        else:
            _early_dropped += 1
    except Exception:
        _early_dropped += 1

def configure_export(config, instance_id, region):
    global _exporter
    if os.environ.get("CLOUD_GLIDER_TIMING_EXPORT") != "1":
        return
    import boto3
    from botocore.config import Config
    from .timing_export import Exporter
    manifest = json.loads(Path("/etc/cloud-glider/image.json").read_text())
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    identity = dict(environment=config.environment, request_id=config.request_id,
        generation=config.generation, instance_id=instance_id, boot_id=boot_id,
        source_commit=manifest["source_commit"], daemon_sha256=manifest["daemon_sha256"])
    # Dedicated non-instrumented client, independent of lifecycle client pools.
    def client_factory():
        return boto3.Session(region_name=region).client("logs", config=Config(
            connect_timeout=.5, read_timeout=.5, retries={"total_max_attempts":1}))
    stream = "timing/" + str(config.request_id) + "/" + config.generation + "/" + instance_id + "/" + boot_id
    _exporter = Exporter(client_factory, config.daemon_operations_log_group, stream, identity)
    _exporter.dropped += _early_dropped
    try:
        spool = Path("/var/lib/cloud-glider/boot-timing") / (boot_id + ".jsonl")
        if spool.exists():
            with spool.open() as source:
                text = source.read(65536)
            for line in text.splitlines():
                record = json.loads(line)
                if record.get("boot_context") == "user_data" or record.get("startup_invocation") == os.environ.get("INVOCATION_ID"):
                    _exporter.enqueue(record)
    except Exception:
        _exporter.dropped += 1
    (Path("/var/lib/cloud-glider") / ("timing-started-" + boot_id + "-" + os.environ.get("INVOCATION_ID", "manual"))).touch(mode=0o600)
    while _early:
        _exporter.enqueue(_early.popleft())

def close_export(timeout=3):
    if _exporter is not None:
        return _exporter.close(timeout)
    return False


def emit(event, *, sink=print, **fields):
    try:
        # Kernel uptime is local to this boot; UTC is for observer correlation.
        uptime = float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        uptime = None
    try:
        record = {
            'schema_version': 1,
            'event': event,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'boot_elapsed_seconds': uptime,
            **fields,
        }
        export_record(record)
        sink(json.dumps(record, sort_keys=True))
    except Exception:
        # A diagnostic sink failure must not turn an accepted mutation into a retry.
        pass


@contextmanager
def span(phase, *, clock=time.monotonic, sink=print, **fields):
    started = clock()
    outcome = 'PASSED'
    try:
        yield
    except BaseException:
        outcome = 'FAILED'
        raise
    finally:
        emit('boot_timing', sink=sink, phase=phase,
             duration_seconds=round(clock() - started, 6), outcome=outcome, **fields)
