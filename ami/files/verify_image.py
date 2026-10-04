#!/usr/bin/env python3
"""Fail closed if the baked release differs from the requested daemon or files."""
import argparse
import hashlib
import json
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
    args = parser.parse_args()
    expected = args.expected_sha256
    if args.config:
        expected = json.loads(args.config.read_text())['daemon_artifact_sha256']
    if not expected:
        parser.error('a config or expected digest is required')
    print(json.dumps(verify(Path('/'), expected), sort_keys=True))


if __name__ == '__main__':
    main()
