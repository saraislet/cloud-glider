#!/usr/bin/env python3
"""Build/test/install the pinned local Amazon plugin; never launches AWS resources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "3896533621ce21e5d8277b7e86e9bf02b577045a"  # upstream v1.8.2
SOURCE_SHA256 = "80cb364384ab12fade9fa2e53f837fee10d6f5a3cba774e1b9222e87b3fd0509"
PLUGIN_SOURCE = "github.com/cloud-glider/amazon"  # local identity, not a published fork


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default="go", help="Go 1.25.11 or compatible executable")
    args = parser.parse_args()
    go = shutil.which(args.go)
    if not go:
        parser.error("Go is required; supply --go /absolute/path/to/go")
    patch = ROOT / "ami/packer-plugin/register-image-tags.patch"
    test = ROOT / "ami/packer-plugin/register_image_tags_glider_test.go"
    with tempfile.TemporaryDirectory(prefix="glider-packer-plugin-") as temp:
        work = Path(temp)
        archive = work / "source.tar.gz"
        urllib.request.urlretrieve(
            f"https://codeload.github.com/hashicorp/packer-plugin-amazon/tar.gz/{REVISION}", archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
            raise SystemExit("Upstream source archive checksum mismatch")
        # Extract only after verifying the exact reviewed upstream archive.
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(work)], check=True)
        source = work / f"packer-plugin-amazon-{REVISION}"
        subprocess.run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)], cwd=source, check=True)
        cleanup_patch = ROOT / "ami/packer-plugin/ssm-cleanup-context.patch"
        subprocess.run(["patch", "--batch", "--forward", "-p1", "-i", str(cleanup_patch)], cwd=source, check=True)
        cleanup_test = ROOT / "ami/packer-plugin/session_cleanup_glider_test.go"
        shutil.copyfile(cleanup_test, source / "common/ssm" / cleanup_test.name)
        shutil.copyfile(test, source / "builder/ebssurrogate" / test.name)
        env = dict(os.environ, GOTOOLCHAIN="local")
        subprocess.run([go, "test", "./builder/ebssurrogate", "./common/ssm", "-count=1"], cwd=source, env=env, check=True)
        binary = work / "packer-plugin-amazon"
        subprocess.run([go, "build", "-trimpath", "-o", str(binary), "."], cwd=source, env=env, check=True)
        # Distinct source address leaves the official HashiCorp binary untouched.
        subprocess.run(["packer", "plugins", "install", "--force", "--path", str(binary), PLUGIN_SOURCE], check=True)
        print(json.dumps({"source": PLUGIN_SOURCE, "upstream_revision": REVISION,
                          "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
                          "cleanup_patch_sha256": hashlib.sha256(cleanup_patch.read_bytes()).hexdigest(),
                          "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
