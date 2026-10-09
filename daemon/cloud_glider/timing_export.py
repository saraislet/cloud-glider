"""Bounded diagnostic exporter. No lifecycle inputs and no instrumented SDK calls."""
import hashlib
import json
import queue
import re
import threading
import time
import uuid

EVENTS = {'boot_timing', 'api_timing', 'phase_timing'}
FIELDS = {'event', 'phase', 'service', 'operation', 'duration_seconds', 'outcome',
          'boot_context', 'startup_invocation', 'error_code', 'dry_run', 'timestamp', 'started_at', 'boot_elapsed_seconds', 'ec2_state', 'retiring', 'inspected_instance_id', 'mismatch_fields'}


def sanitize(raw):
    if raw.get('event') not in EVENTS:
        return None
    record = {}
    for key in FIELDS & raw.keys():
        value = raw[key]
        if value is None or isinstance(value, bool):
            record[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == value and abs(value) < 1e12:
                record[key] = value
        elif isinstance(value, str) and len(value) <= 128 and re.fullmatch(r'[A-Za-z0-9_:.+ /-]*', value):
            record[key] = value
    return record


class Exporter:
    def __init__(self, client, group, stream, identity, *, capacity=2048,
                 max_bytes=2*1024*1024, batch_count=32, interval=.2):
        self.client, self.group, self.stream = client, group, stream
        self.identity = dict(identity)
        self.producer = uuid.uuid4().hex
        self.queue = queue.Queue(capacity)
        self.lock = threading.Lock()
        self.max_bytes, self.bytes = max_bytes, 0
        self.accepted = self.dropped = self.uploaded = self.errors = self.upload_retries = 0
        self.digest = hashlib.sha256()
        self.batch_count, self.interval = min(batch_count, 32), interval
        self.sealed = threading.Event()
        self.done = threading.Event()
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='timing-export', daemon=True)
        self.thread.start()

    def enqueue(self, raw):
        return self._enqueue(raw)

    def _enqueue(self, raw, *, initialization=False):
        record = sanitize(raw)
        if record is None:
            return False
        with self.lock:
            if self.closed and not initialization:
                self.dropped += 1
                return False
            record.update(schema_version=2, **self.identity, producer_id=self.producer,
                          sequence=self.accepted + 1)
            record['record_id'] = self.producer + ':' + str(record['sequence'])
            message = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)
            size = len(message.encode()) + 26
            if self.queue.full() or size > 16384 or self.bytes + size > self.max_bytes:
                self.dropped += 1
                return False
            self.queue.put_nowait((message, size))
            self.bytes += size
            self.accepted += 1
            self.digest.update(message.encode() + b'\n')
            return True

    def close(self, timeout=1.5):
        with self.lock:
            self.closed = True
        self.sealed.set()
        self.done.wait(timeout)
        return self.done.is_set()

    def _upload(self, messages):
        events = [{'timestamp': int(time.time()*1000), 'message': m} for m in messages]
        # Retry only within the diagnostic worker, never the lifecycle thread.
        # An ambiguous transport result may duplicate records; record_id permits
        # exact deduplication by the collection verifier.
        for attempt in range(3):
            try:
                response = self.client.put_log_events(logGroupName=self.group,
                    logStreamName=self.stream, logEvents=events)
                if response.get('rejectedLogEventsInfo'):
                    raise ValueError('RejectedTimingBatch')
                return
            except ValueError:
                raise  # Rejected event content will not improve with retries.
            except Exception:
                if attempt == 2:
                    raise
                self.upload_retries += 1
                time.sleep(.05 * 2 ** attempt)

    def _run(self):
        try:
            try:
                if callable(self.client):
                    started = time.monotonic()
                    self.client = self.client()
                    self._enqueue({"event":"boot_timing", "phase":"collector_client_initialization",
                        "duration_seconds":round(time.monotonic()-started,6), "outcome":"PASSED"}, initialization=True)
                self.client.create_log_stream(logGroupName=self.group, logStreamName=self.stream)
            except Exception as exc:
                if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ResourceAlreadyExistsException':
                    self.errors += 1
                    return
            while True:
                batch = []
                deadline = time.monotonic() + self.interval
                while len(batch) < self.batch_count:
                    try:
                        message, size = self.queue.get(timeout=max(.001, deadline-time.monotonic()))
                        with self.lock:
                            self.bytes -= size
                        batch.append(message)
                    except queue.Empty:
                        break
                    if time.monotonic() >= deadline or (self.sealed.is_set() and self.queue.empty()):
                        break
                if batch:
                    try:
                        self._upload(batch)
                        self.uploaded += len(batch)
                    except Exception:
                        self.errors += 1
                if self.sealed.is_set() and self.queue.empty():
                    with self.lock:
                        marker = {**self.identity, 'schema_version': 2,
                            'event': 'timing_collection_complete', 'producer_id': self.producer,
                            'accepted': self.accepted, 'uploaded': self.uploaded,
                            'dropped': self.dropped, 'upload_errors': self.errors,
                            'upload_retries': self.upload_retries,
                            'sha256': self.digest.hexdigest(),
                            'complete': self.dropped == 0 and self.errors == 0 and self.uploaded == self.accepted}
                    try:
                        self._upload([json.dumps(marker, sort_keys=True, separators=(',', ':'))])
                    except Exception:
                        self.errors += 1
                    return
        finally:
            self.done.set()
