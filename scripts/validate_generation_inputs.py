#!/usr/bin/env python3
"""Reject generation inputs that violate the us-west-2 ARM64 sandbox baseline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS_FILE = Path(__file__).resolve().parents[1] / "config" / "runtime-defaults.json"
with DEFAULTS_FILE.open(encoding="utf-8") as defaults_file:
    DEFAULTS = json.load(defaults_file)

APPROVED_REGION = DEFAULTS["aws_region"]
APPROVED_INSTANCE_TYPE = DEFAULTS["instance_type"]
APPROVED_ARCHITECTURE = DEFAULTS["image_architecture"]


def validate_image(region: str, instance_type: str, images: list[dict[str, Any]]) -> dict[str, Any]:
    if region != APPROVED_REGION:
        raise ValueError(f"Region must be {APPROVED_REGION}")
    if instance_type != APPROVED_INSTANCE_TYPE:
        raise ValueError(f"instance type must be {APPROVED_INSTANCE_TYPE}")
    if len(images) != 1:
        raise ValueError("the AMI lookup must return exactly one image")
    image = images[0]
    if image.get("Architecture") != APPROVED_ARCHITECTURE:
        raise ValueError("AMI architecture must be arm64")
    if image.get("State") != "available":
        raise ValueError("AMI must be available")
    if image.get("RootDeviceType") != "ebs":
        raise ValueError("AMI must use an EBS root device")
    if image.get("VirtualizationType") != "hvm":
        raise ValueError("AMI must use HVM virtualization")
    return image


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--region", default=APPROVED_REGION)
    parser.add_argument("--instance-type", default=APPROVED_INSTANCE_TYPE)
    parser.add_argument("--profile")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = [
        "aws",
        "ec2",
        "describe-images",
        "--image-ids",
        args.image_id,
        "--region",
        args.region,
        "--output",
        "json",
    ]
    if args.profile:
        command.extend(["--profile", args.profile])
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr, end="")
        return completed.returncode
    try:
        payload = json.loads(completed.stdout)
        image = validate_image(args.region, args.instance_type, payload.get("Images", []))
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"approved": True, "image_id": image["ImageId"], "architecture": "arm64"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
