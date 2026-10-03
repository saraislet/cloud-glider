# Direct EC2 propagation

This runbook applies only to explicitly initialized `ec2` cycles in the `cloud-glider` repository. The legacy CloudFormation backend and its templates remain available. The retained infrastructure, persistent launch template and seed remain operator-managed CloudFormation resources. Per-generation successors and retirement use EC2 APIs.

## Offline activation

1. Stop the existing cycle using the CONTROL `stop_requested` switch. Use the existing operator cleanup procedure to complete cleanup. Confirm BOOTSTRAP/REQUEST cleanup is COMPLETE, CURRENT is UNINITIALIZED, no provisioning marker exists, and there are no live generation instances. Do not change backend in a live cycle.
2. Review runtime IAM changes in foundation and permission-boundaries, including `PropagationBackend=ec2` and the exact `AllowedLaunchTemplateId`. Runtime roles cannot update their own permissions. Existing optional Organizations policies block direct EC2 and agent PassRole to EC2; a security administrator must review replacements before using this backend under those policies. Existing deployment roles/workflows are unchanged and may also require separately authorized updates to manage the persistent launch-template stack. Do not bypass a denied deployment.
3. Build a new private 2 GiB ARM64 image from this integrated source using `ami/ubuntu-minimal.pkr.hcl`, preserving `/dev/sda1`, the venv, disabled service and integrity manifest. Bake the SDK versions in `agent/requirements.txt`. Cold-boot testing must report both `CLOUD_GLIDER_AMI_SMOKE_PASS` and `EC2_BAKED_CONTRACT_PASS`; the isolated test has no instance profile or propagation configuration. Build the agent with `scripts/build_agent_artifact.py`, publish it and `cfn/ec2-seed.yaml` as immutable versioned artifacts, and record their hashes and version IDs. The seed is the versioned generation template pinned in CONTROL.
4. Deploy `cfn/launch-template.yaml` through the approved operator path. Its parameters include the generation table and all static seed/agent artifact pins. Obtain the exact template ID and numeric version. Describe that exact version, hash only `LaunchTemplateData` using compact sorted JSON (`json.dumps(data, sort_keys=True, separators=(',', ':'))`, UTF-8, SHA-256), and retain the reviewed response. Never pin `$Latest` or `$Default`.
5. Review the generated initializer transaction with `--propagation-backend ec2 --approved-account-id ACCOUNT --launch-template-id ID --launch-template-version NUMBER --launch-template-sha256 HASH` in addition to the existing required artifact arguments. Initialization is conditional and cannot overwrite an existing CONTROL. For an existing cleaned environment, an approved offline migration must preserve lifecycle schema 2 and the operator interface while applying equivalent pins; do not rerun the initializer with unconditional writes. Keep propagation disabled until ready to observe the cycle, and set a small max_generation.
6. Configure bootstrap parameters for the approved seed and template inputs, including the existing profile, subnet and new baked image (`AgentDeliveryMode=baked`, `RootDeviceName=/dev/sda1`, `RootVolumeGiB=2`). CONTROL supplies LaunchTemplateId and LaunchTemplateVersion automatically for EC2. Bootstrap checks the template digest and static artifact pins before provisioning. Use the existing CONTROL start switch to bootstrap the seed and observe readiness, handoff, predecessor termination and the terminal generation.

## Stop, cleanup and recovery

Use the existing CONTROL stop switch for normal stopping. For an incident, also set the emergency hold through the approved path. Stopping prevents new generation launches and preserves pending predecessor retirement. Existing instances keep running and incur their normal cost until cleanup.

Use CONTROL cleanup_requested only after stopping. Cleanup fences the cycle, waits for the provisioning marker, verifies all live instances and exact generation resource inventory, then deletes the seed stack and terminates direct successors. It records successor IDs before termination, waits for exact `terminated` state, checks residual storage, removes exact successor status alarms, and conditionally resets lifecycle state for the next cycle. The persistent template remains intact. Cleanup-only Scheduler retries retain the current repository's behavior.

A non-expiring LOCK/PROVISIONING or GEN submission intent is evidence of an unresolved request. Inspect EC2 for the exact cycle/generation, template version and client token. If the matching instance exists, the agent can reconcile it and persist inventory. If no matching instance is visible, preserve state and inspect through the operator path; do not blindly clear the marker or relaunch. Missing instances, wrong cycle tags, changed pins, duplicate identities, unknown resources or failed deletion enter NEEDS_ATTENTION and keep gates closed.

Status-check alarms provide corroborating telemetry. They do not authorize retirement; candidate heartbeat identity, control state, ownership and continuation proof remain the gates. Generation-zero's alarm belongs to its seed stack; successors' alarms are managed by the agent and cleanup controller.

## Validation limits

Local tests cover disabled propagation, HOLD, duplicate and ambiguous submission, readiness/handoff failures, changed controls, cycle fencing, typed SDK requests and direct cleanup ownership failures. CloudFormation linting checks the source templates. The [EC2-integrated AMI receipt](../config/releases/2026-10-03-ec2-integrated-minimal-ami.json) records passing metadata and isolated cold-boot gates for the merged source. The temporary builder and smoke resources were cleaned up; no runtime stacks or propagation controls were changed. A supervised propagation test remains necessary to validate live IAM, authoritative readiness, actual capacity, handoff, retirement and cleanup.

## Performance evidence

The last successful baked CloudFormation baseline (decision 0018, receipt
`2026-10-03-preflight-removal-minimal-ami.json`) reached initial ownership in
50.662s and averaged 39.481s per recorded ownership interval (38.555–42.458s).
Its ten-generation duration was 405.991s; sampled live peak was three.
This release removes successor stack creation/deletion and the unused
CloudFormation SDK client from the direct path. Baked startup performs no
artifact download, package install, extraction or unit replacement. These are
plausible latency savings, not measured propagation improvements. Confirmed
predecessor termination is deliberately serialized before another launch,
which may offset gains compared with the overlapping CloudFormation baseline.

The agent emits `phase_timing` records with UTC start/end timestamps, monotonic
duration, request/generation/instance/correlation identity and PASSED, DEFERRED
or FAILED outcome for startup identity, successor submission/readiness,
continuation dry run, conditional handoff and predecessor retirement attempts.
Preserve retry records; retirement attempt timings do not themselves measure
total EC2 termination time across retries. Cold-boot smoke establishes image
compatibility, not propagation or live IAM correctness. A separately authorized
bounded benchmark must record real RunInstances/TerminateInstances event times,
authoritative heartbeat readiness and conditional handoff times, consistent
hop boundaries, controls, live counts and retries before comparing latency.
