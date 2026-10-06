"""Versioned, cycle-pinned configuration and deterministic family identity."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re

from .daemon import SafetyViolation


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(config) -> dict:
    envelope = config.inherited_configuration
    if not isinstance(envelope, dict) or set(envelope) != {"settings", "sha256"}:
        raise SafetyViolation("INHERITED_CONFIG_INVALID", "invalid configuration envelope")
    settings = envelope["settings"]
    if digest(settings) != envelope["sha256"]:
        raise SafetyViolation("INHERITED_CONFIG_INVALID", "configuration digest differs")
    required = {"schema_version", "request_id", "binary_fanout_enabled", "max_generation",
                "readiness_poll_seconds", "readiness_timeout_seconds", "heartbeat_interval_seconds",
                "control_poll_seconds", "control_max_age_seconds", "initial_propagation_enabled",
                "retry_backoff_max_seconds",
                "launch_template_id", "launch_template_version", "launch_template_sha256",
                "daemon_artifact_sha256", "approved_account_id", "approved_region", "environment"}
    if not required <= settings.keys() or settings["schema_version"] != "3":
        raise SafetyViolation("INHERITED_CONFIG_INVALID", "missing inherited settings")
    for name in ("binary_fanout_enabled", "initial_propagation_enabled"):
        if type(settings[name]) is not bool:
            raise SafetyViolation("INHERITED_CONFIG_INVALID", "invalid Boolean " + name)
    for name in ("max_generation", "readiness_poll_seconds", "readiness_timeout_seconds",
                 "heartbeat_interval_seconds", "control_poll_seconds", "control_max_age_seconds", "retry_backoff_max_seconds"):
        if type(settings[name]) is not int or settings[name] < (0 if name == "max_generation" else 1):
            raise SafetyViolation("INHERITED_CONFIG_INVALID", "invalid numeric " + name)
    if settings["max_generation"] > 9 or settings["control_max_age_seconds"] < settings["control_poll_seconds"]:
        raise SafetyViolation("INHERITED_CONFIG_INVALID", "depth or monitor timing outside supported range")
    if settings["retry_backoff_max_seconds"] < settings["readiness_poll_seconds"]:
        raise SafetyViolation("INHERITED_CONFIG_INVALID", "retry backoff is shorter than polling")
    if not re.fullmatch(r"r[01]{0,9}", config.node_path) or len(config.node_path) - 1 != int(config.generation):
        raise SafetyViolation("LINEAGE_INVALID", "node path does not match generation")
    if not settings["binary_fanout_enabled"] and "1" in config.node_path:
        raise SafetyViolation("LINEAGE_INVALID", "single-successor cycle has a right branch")
    for name in ("request_id", "launch_template_id", "launch_template_version", "daemon_artifact_sha256", "environment"):
        if settings[name] != getattr(config, name):
            raise SafetyViolation("INHERITED_CONFIG_INVALID", "configuration pin differs: " + name)
    for setting, field in {"template_sha256": "template_sha256", "template_s3_version_id": "template_s3_version_id",
            "template_s3_bucket": "template_bucket", "template_s3_key": "template_key", "template_build_id": "template_build_id",
            "desired_template_version": "template_version", "desired_bootstrap_version": "bootstrap_version",
            "generation_table_name": "generation_table_name", "daemon_artifact_version_id": "daemon_artifact_version_id",
            "daemon_artifact_bucket": "daemon_artifact_bucket", "daemon_artifact_key": "daemon_artifact_key"}.items():
        if setting in settings and settings[setting] != getattr(config, field):
            raise SafetyViolation("INHERITED_CONFIG_INVALID", "artifact/configuration pin differs: " + setting)
    if settings["approved_region"] != "us-west-2" or not re.fullmatch(r"[0-9]{12}", settings["approved_account_id"]):
        raise SafetyViolation("INHERITED_CONFIG_INVALID", "invalid account or Region")
    if len(config.node_path) - 1 > settings["max_generation"]:
        raise SafetyViolation("LINEAGE_INVALID", "node exceeds inherited depth")
    return settings


def child_paths(path: str, settings: dict) -> tuple[str, ...]:
    if len(path) - 1 >= settings["max_generation"]:
        return ()
    return tuple(path + str(i) for i in range(2 if settings["binary_fanout_enabled"] else 1))


def specification(config, path: str, parent: str, handoff: str) -> dict:
    settings = validate(config)
    token = "cg-tree-" + digest({"cycle": config.request_id, "path": path,
                               "parent": parent, "config": config.inherited_configuration["sha256"]})[:40]
    return {"request_id": config.request_id, "generation": f"{len(path)-1:06d}",
            "node_path": path, "launch_template_id": config.launch_template_id,
            "launch_template_version": config.launch_template_version, "client_token": token,
            "tags": {"project": "cloud-glider", "environment": config.environment,
                "owner": config.owner, "purpose": "generation-compute", "propagation-backend": "ec2",
                "bootstrap-request-id": config.request_id, "generation": f"{len(path)-1:06d}",
                "node-path": path, "configuration-sha256": config.inherited_configuration["sha256"],
                "predecessor-instance-id": parent, "handoff-token": handoff,
                "template-sha256": config.template_sha256,
                "Name": f"cloud-glider-{config.environment}-{config.request_id}-{path}"}}


def user_data(config) -> str:
    """Only the bootstrap JSON varies; executable startup is fixed in source."""
    raw = dataclasses.asdict(config)
    for name in ("instance_id", "request_id", "generation", "predecessor_instance_id",
                 "handoff_token", "launch_template_id", "launch_template_version", "node_path"):
        raw.pop(name)
    raw["propagation_backend"] = "ec2"
    text = """#!/bin/bash
set -euo pipefail
test "$(uname -m)" = aarch64
install -d -m 0750 /etc/cloud-glider /var/lib/cloud-glider
cat >/etc/cloud-glider/bootstrap.json <<'JSON'
""" + json.dumps(raw, sort_keys=True, separators=(",", ":")) + """
JSON
chmod 0640 /etc/cloud-glider/bootstrap.json
/usr/bin/python3 /usr/local/lib/cloud-glider/verify_image.py --config /etc/cloud-glider/bootstrap.json --context user_data
systemctl enable --now cloud-glider.service
"""
    if len(text.encode()) > 16384:
        raise ValueError("inherited startup exceeds EC2 user data limit")
    # botocore's RunInstances handler performs base64 encoding. Passing an
    # encoded string here would double-encode it and prevent cloud-init startup.
    return text
