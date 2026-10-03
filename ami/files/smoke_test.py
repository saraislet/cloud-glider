#!/usr/bin/env python3
"""Run inside a fresh candidate image before any propagation configuration."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

sys.path.insert(0, '/usr/local/lib/cloud-glider')
from verify_image import verify


def verify_runtime():
    for command in ('python3', 'bash', 'uname', 'install', 'cat', 'chmod',
                    'sha256sum', 'tar', 'gzip', 'systemctl'):
        assert shutil.which(command), f'missing command: {command}'
    assert not shutil.which('aws'), 'AWS CLI unexpectedly present in baked image'
    assert Path(sys.prefix) == Path('/opt/cloud-glider/venv'), 'use the agent venv'
    # Import the actual SDK, not just its distribution metadata.
    importlib.import_module("boto3")
    importlib.import_module("botocore")


def verify_lifecycle_contract():
    # Import the installed agent itself; old smoke-passing archives lack these fields.
    sys.path.insert(0, '/opt/cloud-glider')
    import dataclasses
    from cloud_glider.agent import AgentConfig, GENERATION_PARAMETER_NAMES
    fields = {field.name for field in dataclasses.fields(AgentConfig)}
    assert {'request_id', 'generation_table_name'} <= fields, 'legacy agent configuration'
    assert {'RequestId', 'GenerationTableName', 'AgentDeliveryMode'} <= GENERATION_PARAMETER_NAMES, 'legacy generation parameter contract'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args()
    manifest = verify(Path('/'), args.expected_sha256)
    assert platform.machine() == 'aarch64', 'not ARM64'
    assert not Path('/etc/cloud-glider/bootstrap.json').exists(), 'baked generation config'
    assert not list(Path('/var/lib/cloud-glider').iterdir()), 'baked runtime state'
    assert subprocess.run(['systemctl', 'is-active', '--quiet', 'cloud-glider']).returncode != 0
    assert subprocess.run(['systemctl', 'is-enabled', '--quiet', 'cloud-glider']).returncode != 0
    verify_runtime()
    verify_lifecycle_contract()
    # Evaluate the pinned SDK requirements in this interpreter without contacting AWS.
    from pip._vendor.packaging.requirements import Requirement
    for line in Path('/opt/cloud-glider/requirements.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        req = Requirement(line)
        if req.marker is None or req.marker.evaluate():
            assert importlib.metadata.version(req.name) in req.specifier, req.name
    subprocess.run([sys.executable, '/opt/cloud-glider/bin/cloud-glider', '--help'], check=True)
    disk = os.statvfs('/')
    assert disk.f_blocks * disk.f_frsize <= 2 * 1024**3, 'root exceeds 2 GiB target'
    free = disk.f_bavail * disk.f_frsize
    assert free >= 384 * 1024**2, 'less than 384 MiB free after boot'
    print(json.dumps({'result': 'CLOUD_GLIDER_AMI_SMOKE_PASS', 'free_bytes': free,
                      'agent_sha256': manifest['agent_sha256'], 'python': platform.python_version()}))


if __name__ == '__main__':
    main()
