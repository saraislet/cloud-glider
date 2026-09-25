import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("render_account_policy", ROOT / "scripts/render_account_policy.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class AccountPolicyTests(unittest.TestCase):
    def test_invalid_or_example_account_rejected(self):
        for value in (None, "", "123", "123456789012", "12345678901x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.render_policy('{"Statement": []}', value)

    def test_every_policy_only_changes_account_identity(self):
        for path in (ROOT / "iam").glob("*.json"):
            with self.subTest(path=path.name):
                original = json.loads(path.read_text())
                rendered = module.render_policy(path.read_text(), "111122223333")
                self.assertEqual(json.loads(rendered.replace("111122223333", "123456789012")), original)
                self.assertNotIn("123456789012", rendered)

    def test_non_policy_rejected(self):
        with self.assertRaises(ValueError):
            module.render_policy('{"StackId": "example"}', "111122223333")

    def test_workflow_account_gate(self):
        import os
        import subprocess
        workflow = (ROOT / ".github/workflows/deploy-sandbox.yml").read_text()
        section = workflow.split("- name: Validate expected account configuration", 1)[1].split("- name:", 1)[0]
        shell = section.split("run: |", 1)[1]
        for value, valid in (("", False), ("abc", False), ("123456789012", False), ("111122223333", True)):
            with self.subTest(value=value):
                result = subprocess.run(["bash", "-e", "-c", shell], env={**os.environ, "EXPECTED_AWS_ACCOUNT_ID": value}, capture_output=True)
                self.assertEqual(result.returncode == 0, valid)

    def test_sanitized_recovery_is_rejected_before_aws(self):
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("verify_public_import", ROOT / "scripts/verify_foundation_import.py")
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with patch.object(verifier, "aws") as aws:
            with self.assertRaisesRegex(ValueError, "Sanitized historical snapshot"):
                verifier.main()
            aws.assert_not_called()
