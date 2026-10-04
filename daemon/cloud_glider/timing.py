"""Best-effort local timing telemetry; never an input to lifecycle decisions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import time


def emit(event, *, sink=print, **fields):
    try:
        # Kernel uptime is local to this boot; UTC is for observer correlation.
        uptime = float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        uptime = None
    try:
        sink(json.dumps({
            'schema_version': 1,
            'event': event,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'boot_elapsed_seconds': uptime,
            **fields,
        }, sort_keys=True))
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
