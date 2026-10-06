#!/usr/bin/env python3
"""Fail closed if the baked release differs from the requested daemon or files."""
import argparse
import hashlib
import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path


def verify(root: Path, expected_digest: str) -> dict:
    manifest = json.loads((root / 'etc/cloud-glider/image.json').read_text())
    if manifest['daemon_sha256'] != expected_digest.lower():
        raise ValueError('baked daemon differs from approved artifact')
    if not manifest['files']:
        raise ValueError('empty image manifest')
    for relative, expected in manifest['files'].items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('invalid manifest path')
        actual = hashlib.sha256((root / path).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'baked file differs: {relative}')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--context', choices=['standalone','user_data','service_pre'], default='standalone')
    args = parser.parse_args()
    expected = args.expected_sha256
    if args.config:
        expected = json.loads(args.config.read_text())['daemon_artifact_sha256']
    if not expected:
        parser.error('a config or expected digest is required')
    started = time.monotonic()
    outcome = 'FAILED'
    try:
        manifest = verify(Path('/'), expected)
        outcome = 'PASSED'
        print(json.dumps(manifest, sort_keys=True))
    finally:
        # Runs in user data and ExecStartPre, before the daemon package is imported.
        try:
            uptime = float(Path('/proc/uptime').read_text().split()[0])
        except (OSError, ValueError, IndexError):
            uptime = None
        try:
            record = {
                'schema_version': 1, 'event': 'boot_timing',
                'phase': 'baked_image_verification',
                'boot_context': args.context,
                'startup_invocation': os.environ.get('INVOCATION_ID'),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'boot_elapsed_seconds': uptime,
                'duration_seconds': round(time.monotonic() - started, 6),
                'outcome': outcome,
            }
            print(json.dumps(record, sort_keys=True))
            if args.config:
                boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                spool = Path('/var/lib/cloud-glider/boot-timing') / (boot_id + '.jsonl')
                spool.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                if not spool.exists() or spool.stat().st_size < 65536:
                    with spool.open('a') as output:
                        output.write(json.dumps(record, sort_keys=True) + '\n')
        except Exception:
            pass


if __name__ == '__main__':
    main()
