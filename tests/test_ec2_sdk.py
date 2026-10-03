"""Verify the direct gateway against real SDK request models and lifecycle fences."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from test_ec2_agent import config, control, FakeClock, FakeGateway, Agent

if importlib.util.find_spec("boto3"):
    import boto3
    from botocore.validate import validate_parameters
    from cloud_glider.ec2_sdk import Ec2SdkGateway, template_digest
else:
    boto3 = None
from cloud_glider.agent import TransientFailure, SafetyViolation


def template_data():
    return {
        "ImageId": "ami-approved",
        "InstanceType": "t4g.micro",
        "IamInstanceProfile": {"Name": "cloud-glider-sandbox-agent"},
        "MetadataOptions": {
            "HttpTokens": "required",
            "HttpEndpoint": "enabled",
            "HttpPutResponseHopLimit": 1,
            "InstanceMetadataTags": "enabled",
        },
        "NetworkInterfaces": [
            {
                "DeviceIndex": 0,
                "AssociatePublicIpAddress": True,
                "DeleteOnTermination": True,
                "SubnetId": "subnet-approved",
                "Groups": ["sg-approved"],
            }
        ],
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "Encrypted": True,
                    "DeleteOnTermination": True,
                    "VolumeType": "gp3",
                    "VolumeSize": 2,
                },
            }
        ],
        "Monitoring": {"Enabled": False},
        "UserData": "static-approved-data",
    }


@unittest.skipIf(boto3 is None, "SDK dependencies required")
class Ec2SdkTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        with patch(
            "cloud_glider.aws_sdk._imds",
            side_effect=[
                self.cfg.instance_id,
                json.dumps({"region": "us-west-2", "accountId": "111122223333"}),
            ],
        ):
            self.g = Ec2SdkGateway(
                self.cfg,
                session=boto3.Session(
                    aws_access_key_id="testing", aws_secret_access_key="testing"
                ),
            )
        self.addCleanup(self.g.close)
        self.assertNotIn("cloudformation", self.g._clients)
        self.spec = Agent(self.cfg, FakeGateway(self.cfg, FakeClock()))._specification(
            control(), 1, self.cfg.instance_id, "token"
        )
        self.calls = []

        def call(service, operation, **kw):
            kw.pop("allow_failure", None)
            model = self.g._clients[service].meta.service_model.operation_model(
                self.g._clients[service].meta.method_to_api_mapping[operation]
            )
            validate_parameters(kw, model.input_shape)
            self.calls.append((service, operation, kw))
            return {}

        self.g._call = Mock(side_effect=call)

    def test_capacity_counts_exact_identities_omitted_by_filtered_inventory(self):
        parent = "i-" + "9" * 17
        self.g._pages = Mock(return_value=[{"Reservations": []}])
        self.g.read_current = Mock(
            return_value={
                "instance_id": self.cfg.instance_id,
                "predecessor_instance_id": parent,
            }
        )
        self.g.describe_instances_exact = Mock(
            return_value={
                identifier: {"State": {"Name": "shutting-down"}}
                for identifier in (self.cfg.instance_id, parent)
            }
        )
        with self.assertRaisesRegex(TransientFailure, "ceiling"):
            self.g.check_capacity(2)
        self.g.describe_instances_exact.assert_called_once_with(
            sorted([self.cfg.instance_id, parent])
        )
        self.g._call.assert_not_called()

    def test_capacity_missing_exact_lookup_never_frees_slot(self):
        self.g._pages = Mock(return_value=[])
        self.g.read_current = Mock(return_value={"instance_id": self.cfg.instance_id})
        self.g.describe_instances_exact = Mock(
            side_effect=TransientFailure("ambiguous")
        )
        with self.assertRaisesRegex(TransientFailure, "ambiguous"):
            self.g.check_capacity(3)
        self.g._call.assert_not_called()

    def test_capacity_skips_only_completed_predecessor_and_keeps_current(self):
        parent = "i-" + "9" * 17
        for predecessor in (parent, self.cfg.instance_id):
            with self.subTest(predecessor=predecessor):
                self.g._pages = Mock(return_value=[])
                self.g.read_current = Mock(
                    return_value={
                        "instance_id": self.cfg.instance_id,
                        "predecessor_instance_id": predecessor,
                        "retirement_completed": True,
                    }
                )
                self.g.describe_instances_exact = Mock(
                    return_value={self.cfg.instance_id: {"State": {"Name": "running"}}}
                )
                with self.assertRaisesRegex(TransientFailure, "ceiling"):
                    self.g.check_capacity(1)
                self.g.describe_instances_exact.assert_called_once_with(
                    [self.cfg.instance_id]
                )

    def test_exact_batch_uses_valid_sdk_request_and_propagates_errors(self):
        with self.assertRaisesRegex(TransientFailure, "incomplete"):
            self.g.describe_instances_exact([self.cfg.instance_id])
        self.assertEqual(self.calls[-1][2], {"InstanceIds": [self.cfg.instance_id]})
        self.g._call = Mock(return_value={"_returncode": 1, "_error": "lookup denied"})
        with self.assertRaisesRegex(TransientFailure, "lookup denied"):
            self.g.describe_instances_exact([self.cfg.instance_id])

    def test_exact_batch_rejects_missing_duplicate_and_unexpected_instances(self):
        identifiers = ["i-" + "1" * 17, "i-" + "2" * 17]
        for returned in (
            [identifiers[0]],
            [identifiers[0]] * 2,
            [identifiers[0], "i-" + "3" * 17],
        ):
            self.g._call = Mock(
                return_value={
                    "Reservations": [
                        {
                            "Instances": [
                                {"InstanceId": identifier} for identifier in returned
                            ]
                        }
                    ]
                }
            )
            with self.assertRaisesRegex(TransientFailure, "incomplete"):
                self.g.describe_instances_exact(identifiers)
        self.g._call = Mock(
            return_value={
                "Reservations": [
                    {
                        "Instances": [
                            {"InstanceId": identifier, "Tags": []}
                            for identifier in reversed(identifiers)
                        ]
                    }
                ]
            }
        )
        self.assertEqual(
            set(self.g.describe_instances_exact(identifiers)), set(identifiers)
        )
        self.g._call.assert_called_once_with(
            "ec2", "describe_instances", allow_failure=True, InstanceIds=identifiers
        )

    def test_claim_fences_cycle_stop_hold_and_both_tables_atomically(self):
        self.g.claim_submission(self.spec)
        tx = self.calls[0][2]["TransactItems"]
        self.assertIn(
            "propagation_enabled = :yes", tx[0]["ConditionCheck"]["ConditionExpression"]
        )
        self.assertIn("stop_requested", tx[1]["ConditionCheck"]["ConditionExpression"])
        self.assertEqual(tx[3]["Put"]["Item"]["SK"], {"S": "PROVISIONING"})
        self.assertEqual(tx[4]["Put"]["TableName"], self.cfg.generation_table_name)
        self.assertEqual(tx[4]["Put"]["Item"]["request_id"], {"S": "1"})

    def test_inventory_written_before_marker_is_released(self):
        identifier = "i-" + "2" * 17
        self.g._call.side_effect = None
        self.g._call.return_value = {"Instances": [{"InstanceId": identifier}]}
        self.g._record_ec2_submission = Mock()
        self.g._end_provisioning = Mock()
        parent = Mock()
        parent.attach_mock(self.g._record_ec2_submission, "record")
        parent.attach_mock(self.g._end_provisioning, "clear")
        self.assertEqual(self.g.run_instance(self.spec), identifier)
        self.assertEqual([c[0] for c in parent.mock_calls], ["record", "clear"])
        request = self.g._call.call_args.kwargs
        self.assertEqual(request["MinCount"], 1)
        self.assertEqual(request["MaxCount"], 1)
        self.assertEqual(request["LaunchTemplate"]["Version"], "1")
        self.assertNotIn("ImageId", request)

    def test_only_dry_run_operation_is_accepted(self):
        for result in (
            {},
            {"_returncode": 1, "_code": "UnauthorizedOperation"},
            {"_returncode": 1, "_code": "DryRunOperation"},
        ):
            self.g._call.side_effect = None
            self.g._call.return_value = result
            if result.get("_code") == "DryRunOperation":
                self.g.dry_run_instance(self.spec)
            else:
                with self.assertRaises(TransientFailure):
                    self.g.dry_run_instance(self.spec)

    def test_handoff_fences_cycle_and_exact_candidate_in_generation_table(self):
        expected = {
            "generation": "000000",
            "instance_id": self.cfg.instance_id,
            "launch_template_id": self.cfg.launch_template_id,
            "launch_template_version": "1",
            "lease_owner": "owner",
            "lease_now": 10,
            "control_identity": control(),
            "candidate": {
                "generation": "000001",
                "request_id": "1",
                "heartbeat_sequence": 2,
                "functional_readiness": {
                    "producer_instance_id": "i-candidate",
                    "proved_at_epoch": 10,
                },
            },
        }
        self.g.handoff(
            expected, {"generation": "000001"}, {"occurred_at": "now", "event_id": "id"}
        )
        tx = self.calls[0][2]["TransactItems"]
        self.assertEqual(tx[0]["ConditionCheck"]["Key"]["PK"], {"S": "BOOTSTRAP"})
        self.assertIn("expires_at", tx[3]["ConditionCheck"]["ConditionExpression"])
        self.assertIn("request_id = :id", tx[4]["Put"]["ConditionExpression"])
        self.assertEqual(
            tx[5]["ConditionCheck"]["TableName"], self.cfg.generation_table_name
        )
        candidate_check = tx[5]["ConditionCheck"]
        self.assertIn(
            "functional_readiness", candidate_check["ExpressionAttributeNames"].values()
        )
        self.assertTrue(
            any(
                "M" in value and "producer_instance_id" in value["M"]
                for value in candidate_check["ExpressionAttributeValues"].values()
            )
        )
        self.assertEqual(tx[6]["Put"]["Item"]["SK"], {"S": "LATEST_HANDOFF"})
        names = tx[1]["ConditionCheck"]["ExpressionAttributeNames"].values()
        self.assertNotIn("propagation_enabled", names)
        self.assertNotIn("request_id", names)

    def test_reserved_launch_template_tags_determine_identity(self):
        result = self.g._instance(
            {
                "Tags": [
                    {
                        "Key": "aws:ec2launchtemplate:id",
                        "Value": self.cfg.launch_template_id,
                    },
                    {"Key": "aws:ec2launchtemplate:version", "Value": "1"},
                ]
            }
        )
        self.assertEqual(
            result["LaunchTemplate"],
            {"LaunchTemplateId": self.cfg.launch_template_id, "Version": "1"},
        )

    def test_instance_inventory_is_paginated(self):
        self.g._pages = Mock(
            return_value=[
                {"Reservations": []},
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-child",
                                    "State": {"Name": "running"},
                                    "Tags": [],
                                }
                            ]
                        }
                    ]
                },
            ]
        )
        self.assertEqual(self.g.find_successor(self.spec)["InstanceId"], "i-child")
        filters = self.g._pages.call_args.kwargs["Filters"]
        self.assertIn({"Name": "tag:bootstrap-request-id", "Values": ["1"]}, filters)

    def test_instance_profile_path_and_launch_configuration_are_verified(self):
        self.g._template_data = template_data()
        instance = {
            "ClientToken": self.spec["client_token"],
            "Tags": self.spec["tags"],
            "LaunchTemplate": {
                "LaunchTemplateId": self.spec["launch_template_id"],
                "Version": "1",
            },
            "ImageId": "ami-approved",
            "InstanceType": "t4g.micro",
            "Architecture": "arm64",
            "SubnetId": "subnet-approved",
            "SecurityGroups": [{"GroupId": "sg-approved"}],
            "IamInstanceProfile": {
                "Arn": "arn:aws:iam::111122223333:instance-profile/cloud-glider/cloud-glider-sandbox-agent"
            },
            "MetadataOptions": template_data()["MetadataOptions"],
        }
        self.g.verify_instance(instance, self.spec)
        for field in ("ClientToken", "ImageId", "IamInstanceProfile"):
            bad = {
                **instance,
                field: (
                    "foreign" if field != "IamInstanceProfile" else {"Arn": "foreign"}
                ),
            }
            with self.assertRaises(SafetyViolation):
                self.g.verify_instance(bad, self.spec)

    def test_template_digest_and_numeric_version_must_match(self):
        data = template_data()
        version = {
            "LaunchTemplateId": self.spec["launch_template_id"],
            "VersionNumber": 1,
            "LaunchTemplateData": data,
        }
        self.g._call.side_effect = [
            {"LaunchTemplateVersions": [version]},
            {
                "Images": [
                    {
                        "Tags": [
                            {"Key": "project", "Value": "cloud-glider"},
                            {"Key": "purpose", "Value": "agent-image"},
                            {"Key": "agent-sha256", "Value": "b" * 64},
                        ],
                        "Architecture": "arm64",
                        "State": "available",
                        "RootDeviceType": "ebs",
                        "RootDeviceName": "/dev/sda1",
                    }
                ]
            },
        ]
        self.g.verify_launch_template(
            control(launch_template_sha256=template_digest(data))
        )
        self.g._call.side_effect = [{"LaunchTemplateVersions": [version]}]
        with self.assertRaises(SafetyViolation):
            self.g.verify_launch_template(control())
