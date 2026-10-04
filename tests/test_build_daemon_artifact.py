import hashlib
import importlib.util
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_daemon_artifact.py"
SPEC = importlib.util.spec_from_file_location("build_daemon_artifact", MODULE_PATH)
build_daemon_artifact = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_daemon_artifact)


class BuildDaemonArtifactTests(unittest.TestCase):
    def test_build_is_deterministic_and_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.tar.gz"
            second = Path(directory) / "second.tar.gz"
            first_result = build_daemon_artifact.build(first)
            second_result = build_daemon_artifact.build(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_result["sha256"], hashlib.sha256(first.read_bytes()).hexdigest())
            self.assertEqual(first_result["sha256"], second_result["sha256"])
            with tarfile.open(first, "r:gz") as archive:
                names = archive.getnames()
                self.assertIn("bin/cloud-glider", names)
                self.assertIn("cloud_glider/daemon.py", names)
                self.assertIn("cloud_glider/aws_sdk.py", names)
                self.assertNotIn("cloud_glider/aws_cli.py", names)
                self.assertEqual(archive.extractfile("requirements.txt").read(),
                                 (ROOT / "daemon/requirements.txt").read_bytes())
                self.assertEqual(archive.getmember("bin/cloud-glider").mode, 0o755)


if __name__ == "__main__":
    unittest.main()
