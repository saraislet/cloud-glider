import copy
import unittest
from unittest.mock import patch
import test_cleanup as fixtures


class Ec2CleanupTests(unittest.TestCase):
    request = fixtures.CleanupTests.request
    step = fixtures.CleanupTests.step

    def setUp(self):
        fixtures.CleanupTests.setUp(self)
        control = self.ddb.items[("CONTROL", "GLOBAL")]
        control.update(
            propagation_backend={"S": "ec2"},
            launch_template_id={"S": "lt-" + "a" * 17},
            launch_template_version={"S": "1"},
        )
        self.identifier = "i-" + "2" * 17
        self.instance = {
            "InstanceId": self.identifier,
            "State": {"Name": "running"},
            "ClientToken": "token",
            "Tags": [
                {"Key": k, "Value": v}
                for k, v in {
                    "bootstrap-request-id": "1",
                    "propagation-backend": "ec2",
                    "aws:ec2launchtemplate:id": "lt-" + "a" * 17,
                    "aws:ec2launchtemplate:version": "1",
                }.items()
            ],
        }
        item = {
            **fixtures.controller.key("GEN#000001", "RESOURCE#" + self.identifier),
            "request_id": {"S": "1"},
            "instance_id": {"S": self.identifier},
            "client_token": {"S": "token"},
            "launch_template_id": control["launch_template_id"],
            "launch_template_version": {"S": "1"},
        }
        self.ddb.items[fixtures.MemoryDdb.identity(item)] = item
        self.ec2.get_paginator.side_effect = lambda operation: fixtures.Mock(
            paginate=lambda **kw: (
                [{"Reservations": [{"Instances": [copy.deepcopy(self.instance)]}]}]
                if operation == "describe_instances"
                and self.instance["State"]["Name"] != "terminated"
                else (
                    [{"Reservations": []}]
                    if operation == "describe_instances"
                    else [{"Volumes": []}]
                )
            )
        )
        self.ec2.describe_instances.side_effect = lambda **kw: {
            "Reservations": [{"Instances": [copy.deepcopy(self.instance)]}]
        }
        self.ec2.terminate_instances.side_effect = lambda **kw: self.instance.update(
            State={"Name": "shutting-down"}
        )

    def test_direct_cleanup_waits_for_exact_termination_and_preserves_cycle(self):
        self.step()
        self.ec2.terminate_instances.assert_called_once_with(
            InstanceIds=[self.identifier]
        )
        self.assertEqual(
            self.request["cleanup_instance_ids"], {"L": [{"S": self.identifier}]}
        )
        self.assertEqual(self.request["request_id"], {"S": "1"})
        self.step()
        self.ec2.terminate_instances.assert_called_once()
        self.assertEqual(self.request["cleanup_status"], {"S": "DELETING"})

    def test_unrecorded_successor_stops_all_deletion(self):
        del self.ddb.items[("GEN#000001", "RESOURCE#" + self.identifier)]
        with self.assertRaisesRegex(RuntimeError, "Unrecorded"):
            self.step()
        self.ec2.terminate_instances.assert_not_called()
        self.cfn.delete_stack.assert_not_called()
        self.assertEqual(self.request["cleanup_status"], {"S": "NEEDS_ATTENTION"})

    def test_stale_pin_stops_all_deletion(self):
        self.instance["Tags"][-1]["Value"] = "2"
        with self.assertRaisesRegex(RuntimeError, "mismatched"):
            self.step()
        self.ec2.terminate_instances.assert_not_called()
        self.cfn.delete_stack.assert_not_called()

    def test_missing_exact_instance_is_not_termination_proof(self):
        self.ec2.describe_instances.side_effect = None
        self.ec2.describe_instances.return_value = {"Reservations": []}
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.step()
        self.ec2.terminate_instances.assert_not_called()
        self.assertEqual(self.request["request_id"], {"S": "1"})

    def test_missing_instance_with_exact_termination_receipt_is_settled(self):
        record = self.ddb.items[("GEN#000001", "RESOURCE#" + self.identifier)]
        record.update(termination_confirmed={"BOOL": True},
                      termination_observed_at={"S": "2026-10-07T01:34:57+00:00"},
                      termination_evidence_sha256={"S": "a" * 64})
        self.ec2.describe_instances.side_effect = None
        self.ec2.describe_instances.return_value = {"Reservations": []}
        control = self.ddb.items[("CONTROL", "GLOBAL")]
        self.assertIsNone(fixtures.controller.recorded_instance(
            self.ec2, self.identifier, record, control, "1"))
        for field in ("termination_confirmed", "termination_observed_at", "termination_evidence_sha256"):
            changed = copy.deepcopy(record)
            del changed[field]
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                fixtures.controller.recorded_instance(self.ec2, self.identifier, changed, control, "1")
        with self.assertRaisesRegex(RuntimeError, "mismatched"):
            fixtures.controller.recorded_instance(self.ec2, self.identifier, record, control, "2")

    def test_receipt_never_masks_present_identity_mismatch_or_api_failure(self):
        record = self.ddb.items[("GEN#000001", "RESOURCE#" + self.identifier)]
        record.update(termination_confirmed={"BOOL": True}, termination_observed_at={"S": "now"},
                      termination_evidence_sha256={"S": "a" * 64})
        control = self.ddb.items[("CONTROL", "GLOBAL")]
        self.instance['ClientToken'] = 'foreign'
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            fixtures.controller.recorded_instance(self.ec2, self.identifier, record, control, "1")
        from botocore.exceptions import ClientError
        for code in ('UnauthorizedOperation', 'RequestLimitExceeded'):
            self.ec2.describe_instances.side_effect = ClientError({'Error': {'Code': code}}, 'DescribeInstances')
            with self.assertRaises(ClientError):
                fixtures.controller.recorded_instance(self.ec2, self.identifier, record, control, "1")
        self.ec2.describe_instances.side_effect = ClientError({'Error': {'Code': 'InvalidInstanceID.NotFound'}}, 'DescribeInstances')
        self.assertIsNone(fixtures.controller.recorded_instance(self.ec2, self.identifier, record, control, "1"))
