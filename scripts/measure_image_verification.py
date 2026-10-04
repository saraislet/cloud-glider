#!/usr/bin/env python3
"""Measure duplicate verification using current artifact bytes on a local fixture.

This is a warm-filesystem developer-machine measurement, not an EC2 boot benchmark.
"""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from build_daemon_artifact import build

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        archive = root / "opt/cloud-glider/daemon.tar.gz"
        artifact = build(archive)
        with tarfile.open(archive) as source:
            source.extractall(archive.parent, filter="data")
        paths = [archive, *[archive.parent / p for p in artifact["files"]]]
        for source, target in (
            (
                "ami/files/cloud-glider.service",
                "etc/systemd/system/cloud-glider.service",
            ),
            ("ami/files/verify_image.py", "usr/local/lib/cloud-glider/verify_image.py"),
            ("ami/files/smoke_test.py", "usr/local/lib/cloud-glider/smoke_test.py"),
        ):
            path = root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / source).read_bytes())
            paths.append(path)
        manifest = root / "etc/cloud-glider/image.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "daemon_sha256": artifact["sha256"],
                    "files": {
                        str(p.relative_to(root)): hashlib.sha256(
                            p.read_bytes()
                        ).hexdigest()
                        for p in paths
                    },
                }
            )
        )
        # Include interpreter startup, imports and a full manifest hash pass.
        command = [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from verify_image import verify; verify(Path(sys.argv[1]), sys.argv[2])",
            str(root),
            artifact["sha256"],
        ]
        timings = []
        for _ in range(args.samples):
            start = time.perf_counter()
            subprocess.run(command, cwd=ROOT / "ami/files", check=True)
            timings.append(time.perf_counter() - start)
        print(
            json.dumps(
                {
                    "samples": args.samples,
                    "files": len(paths),
                    "bytes_hashed": sum(p.stat().st_size for p in paths),
                    "median_single_seconds": statistics.median(timings),
                    "median_duplicate_seconds": 2 * statistics.median(timings),
                    "minimum_single_seconds": min(timings),
                    "maximum_single_seconds": max(timings),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
