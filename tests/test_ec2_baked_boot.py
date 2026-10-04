"""Exercise the actual launch-template script with a baked runtime fixture."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Ec2BakedBootTests(unittest.TestCase):
    def boot(self, manifest=True, verifier_status=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            for name, body in {
                "uname": "echo aarch64",
                "systemctl": 'echo "$*" >> "$CALL_LOG"',
                "aws": 'echo unexpected-download >> "$CALL_LOG"; exit 99',
                "tar": 'echo unexpected-extract >> "$CALL_LOG"; exit 99',
            }.items():
                path = binary / name
                path.write_text("#!/bin/bash\n" + body + "\n")
                path.chmod(0o755)
            image = root / "etc/cloud-glider/image.json"
            image.parent.mkdir(parents=True)
            if manifest:
                image.write_text("{}")
            python = root / "opt/cloud-glider/venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
            verifier = root / "usr/local/lib/cloud-glider/verify_image.py"
            verifier.parent.mkdir(parents=True)
            verifier.write_text(
                'import json,sys\nraw=json.load(open(sys.argv[2]))\nassert raw["propagation_backend"]=="ec2"\nassert raw["daemon_delivery_mode"]=="baked"\nassert "request_id" not in raw and "generation" not in raw\nsys.exit('
                + str(verifier_status)
                + ")\n"
            )
            script = textwrap.dedent(
                (ROOT / "cfn/launch-template.yaml")
                .read_text()
                .split("Fn::Base64: !Sub |\n", 1)[1]
                .split("\nOutputs:", 1)[0]
            )
            script = re.sub(r"\$\{[^}]+\}", "fixture", script)
            for path in (
                "/etc/cloud-glider",
                "/opt/cloud-glider",
                "/var/lib/cloud-glider",
                "/usr/local/lib/cloud-glider",
            ):
                script = script.replace(path, str(root) + path)
            log = root / "calls"
            result = subprocess.run(
                ["bash"],
                input=script,
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": str(binary) + ":" + os.environ["PATH"],
                    "CALL_LOG": str(log),
                },
            )
            return result, log.read_text() if log.exists() else ""

    def test_baked_startup_uses_existing_service_without_download_or_extract(self):
        result, calls = self.boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.strip(), "enable --now cloud-glider.service")

    def test_missing_or_failed_image_verification_never_starts_service(self):
        for manifest, status in ((False, 0), (True, 1)):
            with self.subTest(manifest=manifest, status=status):
                result, calls = self.boot(manifest, status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, "")
