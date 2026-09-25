import importlib.util
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
if importlib.util.find_spec("boto3") is not None:
    import boto3
    from botocore.exceptions import EndpointConnectionError
    from botocore.response import StreamingBody
    from botocore.stub import ANY, Stubber
    from botocore.validate import validate_parameters
    from cloud_glider.aws_sdk import AwsSdkGateway
else:
    boto3 = None
from cloud_glider.agent import SafetyViolation, TransientFailure


@unittest.skipIf(
    boto3 is None, "Install agent/requirements.txt to run SDK transport tests"
)
class SdkTests(unittest.TestCase):
    def setUp(self):
        self.session = boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing"
        )
        self.factory = Mock(wraps=self.session)
        cfg = SimpleNamespace(
            state_table_name="cloud-glider-test",
            environment="sandbox",
            emergency_hold_function_name="cloud-glider-hold",
        )
        with patch(
            "cloud_glider.aws_sdk._imds",
            side_effect=[
                "i-test",
                json.dumps({"region": "us-west-2", "accountId": "123456789012"}),
            ],
        ):
            self.gateway = AwsSdkGateway(cfg, session=self.factory)
        self.addCleanup(self.gateway.close)

    def stub(self, service):
        stub = Stubber(self.gateway._clients[service])
        stub.activate()
        self.addCleanup(stub.deactivate)
        self.addCleanup(stub.assert_no_pending_responses)
        return stub

    def test_reuses_regional_clients_and_bounded_config(self):
        stub = self.stub("dynamodb")
        expected = {
            "TableName": "cloud-glider-test",
            "Key": {"PK": {"S": "CURRENT"}, "SK": {"S": "GLOBAL"}},
            "ConsistentRead": True,
        }
        for _ in range(2):
            stub.add_response(
                "get_item", {"Item": {"generation": {"S": "000001"}}}, expected
            )
            self.assertEqual(self.gateway.read_current(), {"generation": "000001"})
        self.assertEqual(self.factory.client.call_count, 6)
        for client in self.gateway._clients.values():
            self.assertEqual(client.meta.region_name, "us-west-2")
            self.assertEqual(client.meta.config.retries["total_max_attempts"], 1)
            self.assertEqual(client.meta.config.connect_timeout, 2)
            self.assertEqual(client.meta.config.read_timeout, 5)

    def test_control_hold_and_incomplete_reads(self):
        stub = self.stub("dynamodb")
        expected = {
            "RequestItems": {
                "cloud-glider-test": {
                    "Keys": [
                        {"PK": {"S": "CONTROL"}, "SK": {"S": "GLOBAL"}},
                        {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}},
                    ],
                    "ConsistentRead": True,
                }
            }
        }
        stub.add_response(
            "batch_get_item",
            {
                "Responses": {
                    "cloud-glider-test": [
                        {
                            "PK": {"S": "CONTROL"},
                            "propagation_enabled": {"BOOL": False},
                        },
                        {"PK": {"S": "HOLD"}, "SK": {"S": "ACTIVE"}},
                    ]
                }
            },
            expected,
        )
        control, hold = self.gateway.read_control_and_hold()
        self.assertFalse(control["propagation_enabled"])
        self.assertTrue(hold)
        stub.add_response(
            "batch_get_item", {"UnprocessedKeys": expected["RequestItems"]}, expected
        )
        with self.assertRaisesRegex(TransientFailure, "incomplete"):
            self.gateway.read_control_and_hold()

    def test_conditional_conflicts_and_access_denial(self):
        stub = self.stub("dynamodb")
        stub.add_client_error("update_item", "ConditionalCheckFailedException")
        self.assertFalse(self.gateway.acquire_lease("owner", "000001", 1, 61))
        stub.add_client_error("update_item", "ConditionalCheckFailedException")
        with self.assertRaisesRegex(SafetyViolation, "lease changed"):
            self.gateway.renew_lease("owner", 61)
        stub.add_client_error("delete_item", "ConditionalCheckFailedException")
        self.gateway.release_lease("owner")
        stub.add_client_error(
            "update_item",
            "AccessDeniedException",
            "ConditionalCheckFailedException in message",
        )
        with self.assertRaises(TransientFailure):
            self.gateway.acquire_lease("owner", "000001", 1, 61)

    def test_heartbeat_conflict_remains_safety_violation(self):
        stub = self.stub("dynamodb")
        stub.add_client_error("put_item", "ConditionalCheckFailedException")
        with self.assertRaises(SafetyViolation):
            self.gateway.write_heartbeat(
                {"generation": "000001", "stack_id": "stack", "instance_id": "i-test"}
            )

    def test_only_missing_stack_is_absence(self):
        stub = self.stub("cloudformation")
        stub.add_client_error(
            "describe_stacks", "ValidationError", "Stack test does not exist"
        )
        self.assertIsNone(self.gateway.describe_stack("test"))
        stub.add_client_error("describe_stacks", "AccessDenied", "does not exist")
        with self.assertRaises(TransientFailure):
            self.gateway.describe_stack("test")

    def test_transport_error_never_means_absence_or_conflict(self):
        with patch.object(
            self.gateway._clients["cloudformation"],
            "describe_stacks",
            side_effect=EndpointConnectionError(endpoint_url="https://example.invalid"),
        ):
            with self.assertRaises(TransientFailure):
                self.gateway.describe_stack("test")

    def test_paginated_capacity_counts_all_instances(self):
        stub = self.stub("ec2")
        stub.add_response(
            "describe_instances",
            {
                "Reservations": [{"Instances": [{"InstanceId": "i-one"}]}],
                "NextToken": "next",
            },
            {"Filters": ANY},
        )
        stub.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {"Instances": [{"InstanceId": "i-two"}, {"InstanceId": "i-three"}]}
                ]
            },
            {"Filters": ANY, "NextToken": "next"},
        )
        with self.assertRaisesRegex(TransientFailure, "ceiling"):
            self.gateway.check_capacity(3)

    def test_paginated_offerings_and_quota(self):
        ec2 = self.stub("ec2")
        ec2.add_response("describe_instances", {"Reservations": []})
        ec2.add_response(
            "describe_instance_type_offerings",
            {"InstanceTypeOfferings": [], "NextToken": "next"},
        )
        ec2.add_response(
            "describe_instance_type_offerings",
            {
                "InstanceTypeOfferings": [
                    {
                        "InstanceType": "t4g.micro",
                        "LocationType": "region",
                        "Location": "us-west-2",
                    }
                ]
            },
            {"LocationType": "region", "Filters": ANY, "NextToken": "next"},
        )
        self.stub("service-quotas").add_response(
            "get_service_quota",
            {"Quota": {"Value": 1.0}},
            {"ServiceCode": "ec2", "QuotaCode": "L-1216C47A"},
        )
        with self.assertRaisesRegex(TransientFailure, "quota"):
            self.gateway.check_capacity(3)

    def test_versioned_s3_digest_and_stream_close(self):
        stub = self.stub("s3")
        for good in (True, False):
            raw = io.BytesIO(b"template")
            stub.add_response(
                "get_object",
                {"Body": StreamingBody(raw, 8)},
                {"Bucket": "artifacts", "Key": "generation.yaml", "VersionId": "v1"},
            )
            control = {
                "template_s3_bucket": "artifacts",
                "template_s3_key": "generation.yaml",
                "template_s3_version_id": "v1",
                "template_sha256": hashlib.sha256(
                    b"template" if good else b"bad"
                ).hexdigest(),
            }
            if good:
                self.gateway.verify_template_artifact(control)
            else:
                with self.assertRaises(SafetyViolation):
                    self.gateway.verify_template_artifact(control)
            self.assertTrue(raw.closed)

    def test_truncated_s3_stream_is_transient_and_closed(self):
        raw = io.BytesIO(b"short")
        self.stub("s3").add_response("get_object", {"Body": StreamingBody(raw, 20)})
        with self.assertRaises(TransientFailure):
            self.gateway.verify_template_artifact(
                {
                    "template_s3_bucket": "artifacts",
                    "template_s3_key": "key",
                    "template_s3_version_id": "v1",
                    "template_sha256": "0" * 64,
                }
            )
        self.assertTrue(raw.closed)

    def test_lambda_payload_and_function_error(self):
        stub = self.stub("lambda")
        for failed in (False, True):
            raw = io.BytesIO(b"{}")
            response = {"StatusCode": 200, "Payload": StreamingBody(raw, 2)}
            if failed:
                response["FunctionError"] = "Unhandled"
            stub.add_response(
                "invoke",
                response,
                {
                    "FunctionName": "cloud-glider-hold",
                    "InvocationType": "RequestResponse",
                    "Payload": json.dumps(
                        {
                            "generation": "000001",
                            "error_code": "TEST",
                            "correlation_id": "id",
                        }
                    ).encode(),
                },
            )
            if failed:
                with self.assertRaises(TransientFailure):
                    self.gateway.invoke_hold("000001", "TEST", "id")
            else:
                self.gateway.invoke_hold("000001", "TEST", "id")
            self.assertTrue(raw.closed)

    def test_all_mutation_shapes_and_transaction_conditions(self):
        # Validate native request types against botocore models, while recording
        # safety-bearing parameters independently of the transport wrapper.
        calls = []

        def record(service, operation, allow_failure=False, **params):
            client = self.gateway._clients[service]
            api = client.meta.method_to_api_mapping[operation]
            validate_parameters(
                params, client.meta.service_model.operation_model(api).input_shape
            )
            calls.append((operation, params))
            return {"StackId": "stack", "Id": "preview"}

        with patch.object(self.gateway, "_call", side_effect=record):
            spec = {
                "stack_name": "cloud-glider-sandbox-gen-000002",
                "template_bucket": "artifacts",
                "template_key": "generation/template.yaml",
                "template_version_id": "v+1",
                "parameters": {"Owner": "operator", "Generation": "000002"},
                "role_arn": "arn:aws:iam::123456789012:role/generation",
                "client_token": "stable-token",
                "tags": {"project": "cloud-glider"},
                "change_set_name": "preview",
            }
            self.gateway.create_stack(spec)
            self.gateway.create_preflight(spec)
            self.gateway.delete_stack("stack", spec["role_arn"], "retire-token")
            self.gateway.discard_preflight("preview", "stack", spec["role_arn"])
            self.gateway.claim_initial_current({"generation": "000001"})
            self.gateway.acquire_lease("owner", "000001", 1, 61)
            self.gateway.renew_lease("owner", 62)
            self.gateway.release_lease("owner")
            self.gateway.write_heartbeat(
                {"generation": "000001", "stack_id": "stack", "instance_id": "i-test"}
            )
            self.assertTrue(
                self.gateway.handoff(
                    {
                        "generation": "000001",
                        "stack_id": "stack",
                        "instance_id": "i-test",
                        "lease_owner": "owner",
                        "control_identity": {"propagation_enabled": True},
                    },
                    {
                        "generation": "000002",
                        "stack_id": "next",
                        "instance_id": "i-next",
                        "status": "CURRENT",
                        "handoff_token": "token",
                        "updated_at": "now",
                    },
                    {"occurred_at": "now", "event_id": "event"},
                )
            )
        create = calls[0][1]
        self.assertEqual(create["ClientRequestToken"], "stable-token")
        self.assertEqual(create["OnFailure"], "DO_NOTHING")
        self.assertIn("versionId=v%2B1", create["TemplateURL"])
        self.assertEqual(calls[1][1]["ChangeSetType"], "CREATE")
        self.assertEqual(calls[2][1]["ClientRequestToken"], "retire-token")
        self.assertNotIn("execute_change_set", [op for op, _ in calls])
        transaction = calls[-1][1]["TransactItems"]
        self.assertEqual(len(transaction), 6)
        self.assertEqual(transaction[1]["ConditionCheck"]["Key"]["PK"], {"S": "HOLD"})
        self.assertEqual(
            transaction[2]["ConditionCheck"]["ConditionExpression"],
            "lease_owner = :owner",
        )
        self.assertIn("ConditionExpression", transaction[3]["Update"])
        self.assertIn("ConditionExpression", transaction[4]["Update"])

    def test_handoff_transaction_cancel_remains_conflict(self):
        # A minimal shaped transaction fixture comes from the full method above;
        # the structured service error path is exercised directly here.
        self.stub("dynamodb").add_client_error(
            "transact_write_items", "TransactionCanceledException"
        )
        result = self.gateway._call(
            "dynamodb",
            "transact_write_items",
            TransactItems=[
                {
                    "ConditionCheck": {
                        "TableName": "table",
                        "Key": {"PK": {"S": "x"}},
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
            ],
            allow_failure=True,
        )
        self.assertEqual(result["_code"], "TransactionCanceledException")


if __name__ == "__main__":
    unittest.main()
