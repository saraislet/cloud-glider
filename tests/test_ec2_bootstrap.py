import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock
import test_cleanup as fixtures
from test_ec2_sdk import template_data


class Ec2BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.params = dict(
            DaemonDeliveryMode="baked",
            RootDeviceName="/dev/sda1",
            RootVolumeGiB="2",
            ApprovedImageId="ami-approved",
            DaemonInstanceProfileName="cloud-glider-sandbox-daemon",
            GenerationTableName="cloud-glider-sandbox-generations",
            SubnetId="subnet-approved",
            SecurityGroupId="sg-approved",
            OperationalAlertsTopicArn="arn:aws:sns:us-west-2:111122223333:cloud-glider-sandbox-operational-alerts",
            TemplateSha256="a" * 64,
            TemplateS3VersionId="seed-version",
            DaemonArtifactSha256="b" * 64,
            DaemonArtifactVersionId="daemon-version",
            BootstrapVersion="v1",
            TemplateVersion="v1",
        )
        raw = dict(
            propagation_backend="ec2",
            daemon_delivery_mode="baked",
            generation_table_name=self.params["GenerationTableName"],
            template_sha256=self.params["TemplateSha256"],
            template_s3_version_id="seed-version",
            daemon_artifact_sha256=self.params["DaemonArtifactSha256"],
            daemon_artifact_version_id="daemon-version",
            bootstrap_version="v1",
            template_version="v1",
        )
        self.data = template_data()
        self.data["UserData"] = base64.b64encode(
            ("cat >config <<'JSON'\n" + json.dumps(raw) + "\nJSON\n").encode()
        ).decode()
        self.control = dict(
            approved_account_id={"S": "111122223333"},
            launch_template_id={"S": "lt-" + "a" * 17},
            launch_template_version={"S": "1"},
            concurrency_model={"S": "EC2_DRY_RUN_THEN_RETIRE"},
            launch_template_sha256={
                "S": hashlib.sha256(
                    json.dumps(
                        self.data, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
            },
        )
        self.ec2 = Mock()
        self.ec2.describe_images.return_value = {
            "Images": [
                {
                    "Tags": [
                        {"Key": "project", "Value": "cloud-glider"},
                        {"Key": "purpose", "Value": "daemon-image"},
                        {"Key": "daemon-sha256", "Value": "b" * 64},
                    ]
                }
            ]
        }
        self.ec2.describe_launch_template_versions.return_value = {
            "LaunchTemplateVersions": [
                {
                    "LaunchTemplateId": self.control["launch_template_id"]["S"],
                    "VersionNumber": 1,
                    "LaunchTemplateData": self.data,
                }
            ]
        }

    def test_exact_template_and_static_artifact_pins_pass(self):
        fixtures.controller.verify_ec2_template(self.ec2, self.control, self.params)
        self.ec2.describe_launch_template_versions.assert_called_once_with(
            LaunchTemplateId=self.control["launch_template_id"]["S"], Versions=["1"]
        )

    def test_changed_digest_account_or_numeric_pin_blocks_bootstrap(self):
        for field, value in [
            ("launch_template_sha256", "c" * 64),
            ("approved_account_id", "000000000000"),
            ("launch_template_version", "$Default"),
        ]:
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                fixtures.controller.verify_ec2_template(
                    self.ec2, {**self.control, field: {"S": value}}, self.params
                )

    def test_seed_artifact_changes_cannot_override_static_template(self):
        with self.assertRaisesRegex(RuntimeError, "artifact mismatch"):
            fixtures.controller.verify_ec2_template(
                self.ec2,
                self.control,
                {**self.params, "DaemonArtifactVersionId": "other"},
            )
