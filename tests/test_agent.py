import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from cloud_glider.agent import Agent, AgentConfig, SafetyViolation, TransientFailure  # noqa: E402


class FakeClock:
    def __init__(self):
        self.value = 1_800_000_000

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def config():
    return AgentConfig(
        environment="sandbox",
        generation="000001",
        stack_id="arn:aws:cloudformation:us-west-2:111122223333:stack/cloud-glider-sandbox-gen-000001/one",
        predecessor_stack_id="NONE",
        handoff_token="OPERATOR_BOOTSTRAP",
        state_table_name="cloud-glider-sandbox-state",
        bootstrap_version="bootstrap-v1",
        template_version="template-v1",
        template_bucket="artifacts",
        template_key="generation/generation.yaml",
        template_s3_version_id="template-object-v1",
        template_sha256="a" * 64,
        template_build_id="abcdef123456",
        agent_artifact_bucket="artifacts",
        agent_artifact_key="generation/agent.tar.gz",
        agent_artifact_version_id="agent-object-v1",
        agent_artifact_sha256="b" * 64,
        emergency_hold_function_name="cloud-glider-sandbox-emergency-hold",
        propagation_audit_log_group="/cloud-glider/sandbox/audit/propagation",
        agent_operations_log_group="/cloud-glider/sandbox/agent/operations",
    )


def control(**overrides):
    values = {
        "propagation_enabled": True,
        "desired_template_version": "template-v1",
        "desired_bootstrap_version": "bootstrap-v1",
        "template_s3_bucket": "artifacts",
        "template_s3_key": "generation/generation.yaml",
        "template_s3_version_id": "template-object-v1",
        "template_sha256": "a" * 64,
        "template_build_id": "abcdef123456",
        "agent_artifact_bucket": "artifacts",
        "agent_artifact_key": "generation/agent.tar.gz",
        "agent_artifact_version_id": "agent-object-v1",
        "agent_artifact_sha256": "b" * 64,
        "max_generation": 2,
        "max_live_generations": 3,
        "concurrency_model": "PREFLIGHT_THEN_RETIRE",
        "approved_region": "us-west-2",
        "approved_architecture": "arm64",
        "approved_instance_types": ["t4g.micro"],
        "environment": "sandbox",
        "readiness_poll_seconds": 2,
        "readiness_required_heartbeats": 2,
        "heartbeat_interval_seconds": 5,
        "readiness_timeout_seconds": 20,
    }
    values.update(overrides)
    return values


class FakeGateway:
    instance_id = "i-current"

    def __init__(self, cfg, clock, controls=None):
        self.cfg = cfg
        self.clock = clock
        self.controls = list(controls or [control()])
        self.current = {
            "generation": cfg.generation,
            "stack_id": cfg.stack_id,
            "instance_id": self.instance_id,
            "status": "CURRENT",
        }
        self.calls = []
        self.created = None
        self.heartbeat_reads = 0
        self.handoff_result = True
        self.lease_result = True
        self.change_set_status = "CREATE_COMPLETE"
        self.successor_status = "CREATE_COMPLETE"
        self.hold_active = False
        self.create_timeout_once = False
        self.change_owner_on_acquire = False

    def read_control_and_hold(self):
        value = self.controls.pop(0) if len(self.controls) > 1 else self.controls[0]
        self.calls.append("control")
        return dict(value), self.hold_active

    def read_current(self):
        return dict(self.current)

    def claim_initial_current(self, identity):
        self.current = dict(identity)
        return True

    def write_heartbeat(self, state):
        self.calls.append("heartbeat")

    def acquire_lease(self, owner, generation, now, expires):
        self.calls.append("acquire")
        if self.change_owner_on_acquire:
            self.current["generation"] = "000099"
        return self.lease_result

    def renew_lease(self, owner, expires):
        self.calls.append("renew")

    def release_lease(self, owner):
        self.calls.append("release")

    def own_parameters(self):
        return {
            "Environment": self.cfg.environment,
            "Owner": "operator",
            "Generation": self.cfg.generation,
            "PredecessorStackId": self.cfg.predecessor_stack_id,
            "HandoffToken": self.cfg.handoff_token,
            "ApprovedImageId": "ami-approved",
            "InstanceType": "t4g.micro",
            "ImageArchitecture": "arm64",
            "SubnetId": "subnet-approved",
            "SecurityGroupId": "sg-approved",
            "AgentInstanceProfileName": "cloud-glider-sandbox-agent",
            "StateTableName": self.cfg.state_table_name,
            "AgentArtifactBucket": self.cfg.agent_artifact_bucket,
            "AgentArtifactKey": self.cfg.agent_artifact_key,
            "AgentArtifactVersionId": self.cfg.agent_artifact_version_id,
            "AgentArtifactSha256": self.cfg.agent_artifact_sha256,
            "BootstrapVersion": self.cfg.bootstrap_version,
            "TemplateVersion": self.cfg.template_version,
            "TemplateBucket": self.cfg.template_bucket,
            "TemplateKey": self.cfg.template_key,
            "TemplateS3VersionId": self.cfg.template_s3_version_id,
            "TemplateSha256": self.cfg.template_sha256,
            "TemplateBuildId": self.cfg.template_build_id,
            "PropagationAuditLogGroupName": self.cfg.propagation_audit_log_group,
            "AgentOperationsLogGroupName": self.cfg.agent_operations_log_group,
            "EmergencyHoldFunctionName": self.cfg.emergency_hold_function_name,
            "OperationalAlertsTopicArn": "arn:aws:sns:us-west-2:111122223333:alerts",
            "RootDeviceName": "/dev/xvda",
            "RootVolumeGiB": "8",
        }

    def describe_stack(self, identifier):
        if identifier == self.cfg.stack_id:
            return {
                "StackId": self.cfg.stack_id,
                "StackStatus": "CREATE_COMPLETE",
                "RoleARN": "arn:aws:iam::111122223333:role/cloud-glider-sandbox-generation-cfn",
                "Parameters": self.own_parameters(),
            }
        if self.created and identifier in (self.created["stack_name"], "successor-stack-id"):
            return {
                "StackId": "successor-stack-id",
                "StackStatus": self.successor_status,
                "RoleARN": self.created["role_arn"],
                "Parameters": self.created["parameters"],
            }
        return None

    def stack_instance_id(self, stack_id):
        return self.instance_id if stack_id == self.cfg.stack_id else "i-successor"

    def verify_template_artifact(self, control_value):
        self.calls.append("verify-template")

    def create_stack(self, specification):
        self.calls.append("create")
        self.created = specification
        if self.create_timeout_once:
            self.create_timeout_once = False
            raise TransientFailure("simulated timeout after request submission")
        return "successor-stack-id"

    def read_generation_state(self, generation):
        self.heartbeat_reads += 1
        parameters = self.created["parameters"]
        return {
            "generation": generation,
            "stack_id": "successor-stack-id",
            "instance_id": "i-successor",
            "predecessor_stack_id": parameters["PredecessorStackId"],
            "handoff_token": parameters["HandoffToken"],
            "template_version": parameters["TemplateVersion"],
            "template_s3_version_id": parameters["TemplateS3VersionId"],
            "template_sha256": parameters["TemplateSha256"],
            "template_build_id": parameters["TemplateBuildId"],
            "bootstrap_version": parameters["BootstrapVersion"],
            "agent_artifact_version_id": parameters["AgentArtifactVersionId"],
            "agent_artifact_sha256": parameters["AgentArtifactSha256"],
            "status": "CANDIDATE",
            "workload_healthy": True,
            "observed_propagation_enabled": True,
            "observed_hold_active": False,
            "heartbeat_sequence": self.heartbeat_reads,
            "heartbeat_at_epoch": int(self.clock()),
        }

    def check_capacity(self, max_live_generations):
        self.calls.append("capacity")

    def create_preflight(self, specification):
        self.calls.append("preflight")
        return "change-set-id"

    def describe_change_set(self, change_set_id):
        return {"Status": self.change_set_status}

    def discard_preflight(self, change_set_id, stack_name, role_arn):
        self.calls.append("discard-preflight")

    def handoff(self, expected, successor, audit):
        self.calls.append("handoff")
        return self.handoff_result

    def delete_stack(self, stack_id, role_arn, token):
        self.calls.append("delete-predecessor")

    def invoke_hold(self, generation, error_code, correlation_id):
        self.calls.append("hold")


class AgentTests(unittest.TestCase):
    def make_agent(self, gateway, clock):
        agent = Agent(config(), gateway, clock=clock, sleep=clock.sleep, logger=lambda value: None)
        agent.verify_self()
        return agent

    def test_disabled_propagation_only_heartbeats(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock, [control(propagation_enabled=False)])
        agent = self.make_agent(gateway, clock)
        self.assertEqual(agent.cycle(), "STOPPED_BY_OPERATOR")
        self.assertNotIn("acquire", gateway.calls)
        self.assertNotIn("create", gateway.calls)

    def test_emergency_hold_only_heartbeats(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock)
        gateway.hold_active = True
        agent = self.make_agent(gateway, clock)
        self.assertEqual(agent.cycle(), "STOPPED_BY_OPERATOR")
        self.assertNotIn("acquire", gateway.calls)
        self.assertNotIn("create", gateway.calls)

    def test_duplicate_agent_without_lease_does_not_create(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock)
        gateway.lease_result = False
        agent = self.make_agent(gateway, clock)
        self.assertEqual(agent.cycle(), "LEASE_NOT_ACQUIRED")
        self.assertNotIn("create", gateway.calls)

    def test_ownership_change_after_lease_does_not_create(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock)
        gateway.change_owner_on_acquire = True
        agent = self.make_agent(gateway, clock)
        self.assertEqual(agent.cycle(), "OWNERSHIP_CHANGED")
        self.assertNotIn("create", gateway.calls)

    def test_fresh_stop_before_create_prevents_provisioning(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock, [control(), control(propagation_enabled=False)])
        agent = self.make_agent(gateway, clock)
        with self.assertRaises(TransientFailure):
            agent.cycle()
        self.assertNotIn("create", gateway.calls)
        self.assertEqual(gateway.calls[-1], "release")

    def test_handoff_precedes_predecessor_retirement(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock)
        agent = self.make_agent(gateway, clock)
        self.assertEqual(agent.cycle(), "HANDOFF_COMPLETE")
        self.assertLess(gateway.calls.index("handoff"), gateway.calls.index("delete-predecessor"))
        self.assertGreaterEqual(gateway.heartbeat_reads, 2)

    def test_create_timeout_reconciles_without_duplicate_stack(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock)
        gateway.create_timeout_once = True
        agent = self.make_agent(gateway, clock)
        with self.assertRaisesRegex(TransientFailure, "simulated timeout"):
            agent.cycle()
        self.assertEqual(agent.cycle(), "HANDOFF_COMPLETE")
        self.assertEqual(gateway.calls.count("create"), 1)

    def test_failed_handoff_preserves_predecessor(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock)
        gateway.handoff_result = False
        agent = self.make_agent(gateway, clock)
        with self.assertRaisesRegex(SafetyViolation, "CURRENT or lease"):
            agent.cycle()
        self.assertNotIn("delete-predecessor", gateway.calls)

    def test_continuation_is_unexecuted_preflight_and_is_discarded(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock, [control(max_generation=3)])
        agent = self.make_agent(gateway, clock)
        self.assertEqual(agent.cycle(), "HANDOFF_COMPLETE")
        self.assertIn("capacity", gateway.calls)
        self.assertIn("preflight", gateway.calls)
        self.assertIn("discard-preflight", gateway.calls)
        self.assertLess(gateway.calls.index("discard-preflight"), gateway.calls.index("handoff"))

    def test_ambiguous_health_times_out_without_retirement(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock, [control(readiness_timeout_seconds=3)])
        gateway.successor_status = "CREATE_IN_PROGRESS"
        agent = self.make_agent(gateway, clock)
        with self.assertRaisesRegex(TransientFailure, "readiness timed out"):
            agent.cycle()
        self.assertNotIn("handoff", gateway.calls)
        self.assertNotIn("delete-predecessor", gateway.calls)

    def test_terminal_identity_error_invokes_emergency_hold(self):
        clock = FakeClock()
        gateway = FakeGateway(config(), clock)
        gateway.stack_instance_id = lambda stack_id: "i-wrong"
        agent = Agent(config(), gateway, clock=clock, sleep=clock.sleep, logger=lambda value: None)
        self.assertEqual(agent.run(), 2)
        self.assertIn("hold", gateway.calls)


if __name__ == "__main__":
    unittest.main()
