"""Execute generation user data against a temporary baked-runtime fixture."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MinimalAmiBootTests(unittest.TestCase):
    def boot(self, manifest=True, verifier_status=0, mode='baked'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'bin'
            binary.mkdir()
            for name, body in {
                'uname': 'echo aarch64',
                'systemctl': 'echo "$*" >> "$CALL_LOG"',
                'aws': 'echo unexpected-download >> "$CALL_LOG"; exit 99',
            }.items():
                path = binary / name
                path.write_text('#!/bin/bash\n' + body + '\n')
                path.chmod(0o755)
            image_dir = root / 'etc/cloud-glider'
            image_dir.mkdir(parents=True)
            if manifest:
                (image_dir / 'image.json').write_text('{}')
            verifier = root / 'usr/local/lib/cloud-glider/verify_image.py'
            verifier.parent.mkdir(parents=True)
            verifier.write_text(
                'import json, sys\n'
                'config = json.load(open(sys.argv[2]))\n'
                'assert config["request_id"] == "fixture"\n'
                'assert config["generation_table_name"] == "fixture"\n'
                'sys.exit(' + str(verifier_status) + ')\n')
            template = (ROOT / 'cfn/generation.yaml').read_text()
            script = textwrap.dedent(template.split('Fn::Base64: !Sub |\n', 1)[1].split('\n  GenerationStatusCheckAlarm:', 1)[0])
            script = re.sub(r'\$\{([^}]+)\}', lambda match: mode if match[1] == 'DaemonDeliveryMode' else 'fixture', script)
            for path in ('/etc/cloud-glider', '/opt/cloud-glider', '/etc/systemd', '/var/lib/cloud-glider', '/usr/local/lib/cloud-glider'):
                script = script.replace(path, str(root) + path)
            log = root / 'calls'
            result = subprocess.run(['bash'], input=script, text=True, capture_output=True,
                                    env={**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH'], 'CALL_LOG': str(log)})
            calls = log.read_text() if log.exists() else ''
            return result, calls

    def test_baked_boot_starts_existing_service_without_download(self):
        result, calls = self.boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('enable --now cloud-glider.service', calls)
        self.assertNotIn('unexpected-download', calls)

    def test_missing_manifest_and_failed_verification_prevent_start(self):
        for manifest, status in ((False, 0), (True, 1)):
            with self.subTest(manifest=manifest, status=status):
                result, calls = self.boot(manifest, status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, '')

    def test_download_mode_refuses_to_overwrite_baked_runtime(self):
        result, calls = self.boot(mode='s3')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
