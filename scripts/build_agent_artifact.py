#!/usr/bin/env python3
"""Build a deterministic Cloud Glider agent tarball."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent"
SOURCE_FILES = (
    Path("bin/cloud-glider"),
    Path("cloud_glider/__init__.py"),
    Path("cloud_glider/agent.py"),
    Path("cloud_glider/aws_sdk.py"),
    Path("requirements.txt"),
)


def build(output: Path) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for relative in SOURCE_FILES:
            source = AGENT_ROOT / relative
            data = source.read_bytes()
            info = tarfile.TarInfo(str(relative))
            info.size = len(data)
            info.mode = 0o755 if relative == Path("bin/cloud-glider") else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(data))
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(buffer.getvalue())
    data = output.read_bytes()
    return {
        "artifact": str(output),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "files": [str(path) for path in SOURCE_FILES],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
