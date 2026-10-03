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

Local tests cover disabled propagation, HOLD, duplicate and ambiguous submission, readiness/handoff failures, changed controls, cycle fencing, typed SDK requests and direct cleanup ownership failures. CloudFormation linting checks the source templates. The [EC2-integrated AMI receipt](../config/releases/2026-10-03-ec2-integrated-minimal-ami.json) records passing metadata and isolated cold-boot gates, authorized deployment, and a successful ten-generation retry after correcting the agent policy and boundary to match the existing `/cloud-glider/` instance-profile path. The retry validated live successor readiness, eight continuation DryRuns, nine conditional handoffs, confirmed predecessor termination and supported cleanup. Propagation is disabled, max_generation is 2, and no live test instances or cycle locks remain. The test was slower than the previous CloudFormation baseline; see the [timing breakdown](performance-log.md). Phase JSON stays in local journals; this release does not export it remotely.

For a definitively rejected submission, stop propagation and request supported cleanup first. Preserve the intent and inspect the exact CloudTrail request, client token, template and cycle. Only a confirmed terminal rejection with no matching EC2 instance permits an audited operator transaction releasing the exact provisioning marker for cleanup, conditioned on closed launch gates, unchanged CURRENT and no active lease. Retain GEN submission evidence until supported cleanup removes that cycle. A timeout, missing API event or uncertain instance lookup is ambiguous and does not authorize this recovery.

## Performance evidence

The last successful baked CloudFormation baseline (decision 0018, receipt
`2026-10-03-preflight-removal-minimal-ami.json`) reached initial ownership in
50.662s and averaged 39.481s per recorded ownership interval (38.555–42.458s).
Its ten-generation duration was 405.991s; sampled live peak was three.
This release removes successor stack creation/deletion and the unused
CloudFormation SDK client from the direct path. Baked startup performs no
artifact download, package install, extraction or unit replacement. These are
plausible latency savings, not measured propagation improvements. The
initial release serialized predecessor termination before another launch.
Decision 0020 allows boot overlap; no speedup has been measured for that change.

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

## Retirement overlap and recovery

After accepted termination (or an exact `shutting-down` observation), the current
owner may launch its candidate with capacity available. It retains CURRENT and
its predecessor retirement intent until exact termination is confirmed. The
candidate cannot take ownership during that wait. Restart the current owner's
service to reconcile the same candidate and exact predecessor; never clear the
intent or provisioning marker to force progress. A missing exact EC2 lookup
blocks progress. With a ceiling of two, launch waits for termination; with three,
one retiring predecessor, current owner and candidate may coexist. Stop/HOLD
blocks retries and handoff. Terminal generations wait for confirmed retirement.

The integrated source includes the profile ARN correction merged in PR #12
(`cabf646`). The separately authorized overlap release at `04bc90f` was baked,
cold-boot tested, deployed and verified over generations 0–9. The
[overlap receipt](../config/releases/2026-10-03-ec2-overlap-minimal-ami.json)
records AMI `ami-0dd526b0623ff67a3`, Launch Template version 2 and its exact digest,
measured overlap, nine handoffs, terminal retirement and full cleanup. Propagation
is disabled at idle; the operator-selected max_generation is 10 and the absolute
three-instance ceiling is retained. Future source changes require a matching
archive/AMI, reviewed pins and an observed bounded test before claiming improvement.

## Polling configuration review

See the [source-only polling evaluation](ec2-polling-evaluation.md) for the
one-second EC2 ownership loop, separately spaced heartbeats, operator-selected one-second
readiness default, batched capacity identities and unchanged image
verification. Review increased DynamoDB reads and lease-renewal writes before
deployment. New EC2 CONTROL initialization selects one second; CloudFormation
initialization retains two seconds. Existing CONTROL records require an approved
configuration update during deployment; initialization cannot overwrite them.
The subsequent source-only [functional readiness change](ec2-functional-readiness.md)
implements operator-approved decision 0021 and replaces the EC2 two-heartbeat wait
with successor-produced capability proof; it is not deployed. Heartbeat telemetry
retains its configured spacing, independently of functional proof refresh.
