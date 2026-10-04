# Propagation daemon runbook

## Bootstrap

Set `CONTROL/GLOBAL.start_requested=true` to bootstrap and enable propagation.
The Lambda prepares the request and checks prerequisites. Read progress/messages
in the same CONTROL item. Internal permissions remain in BOOTSTRAP/REQUEST;
no routine edits there are needed. HOLD blocks start.
See [the bootstrap runbook](bootstrap.md).

## Lifecycle

Each process verifies that IMDSv2 instance identity and its CloudFormation
stack agree with `/etc/cloud-glider/bootstrap.json`. The operator-bootstrapped
generation may conditionally claim an `UNINITIALIZED` `CURRENT/GLOBAL` record.
All other generations begin as candidates.

Every loop writes `GEN#{generation}/STATE`. A current owner may continue only
when `BOOTSTRAP/REQUEST.propagation_enabled=true`, `HOLD/ACTIVE` is absent, and its
generation is below `max_generation`. It then:

1. conditionally acquires `LOCK/PROPAGATION`;
2. strongly rereads operator control and verifies the versioned template digest;
3. creates or reconciles the deterministic successor stack;
4. waits for `CREATE_COMPLETE` and distinct eligible heartbeats;
5. rereads control and the real successor's stack identity/configuration and health;
6. atomically transfers `CURRENT/GLOBAL` with control, hold, and lease conditions; and
7. submits predecessor stack deletion through CloudFormation.

Capacity, regional offering, and quota checks remain before actual successor
creation. No disposable N+2 change set or placeholder is created. Each new owner
validates its own actual successor on the next cycle. The predecessor survives
missing, unhealthy, or mismatched successor evidence and failed handoff.
See [decision 0018](decisions/0018-remove-continuation-preflight.md), which
supersedes the continuation and preview overlap requirements in decisions 0005
and 0006. Asynchronous predecessor deletion may still overlap next creation;
the existing conservative capacity gate remains. Durable retirement recovery
and stronger in-flight accounting remain separate design work.

## DynamoDB records

Control and coordination stay in `cloud-glider-{environment}-state`; generation state and stack inventory use `cloud-glider-{environment}-generations`.

- `CONTROL/GLOBAL`: operator control, limits, timing, and approved immutable
  template/bootstrap/daemon identities.
- `BOOTSTRAP/REQUEST`: shared lifecycle switches, incremental cycle identity, bootstrap status, and latest cleanup progress.
- `CURRENT/GLOBAL`: authoritative generation, stack, instance, status, and
  handoff token.
- Generation table `GEN#{generation}/STATE`: identity and heartbeat evidence. Writes are
  conditional on the original stack and instance and cannot overwrite `ERROR`.
- `LOCK/PROPAGATION`: expiring owner token used to serialize propagation.
- `LOCK/PROVISIONING`: non-expiring submission marker; cleanup waits for a reconciled outcome.
- Generation table `GEN#.../RESOURCE#<stack ARN>`: exact submitted stack inventory, including legacy preview stacks for cleanup.
- `HOLD/ACTIVE`: append-only incident stop created only by the dedicated Lambda.
- `AUDIT#PROPAGATION/LATEST_HANDOFF`, `LATEST_HOLD`, `LATEST_INITIALIZATION`, `LATEST_BOOTSTRAP`, `LATEST_MIGRATION`: latest event per action.
- `AUDIT#RECOVERY/LATEST`: latest hold-clear result. These fixed keys replace expanding event history.

CloudTrail is authoritative for AWS API changes. The service writes structured
JSON lifecycle events to the systemd journal; the configured EC2 status alarm
is corroborating telemetry and never a readiness gate.

## Polling and request cost

An ordinary cycle reads control/hold once, reads CURRENT once, and writes one
heartbeat. The CURRENT snapshot is shared only within that cycle's initial
heartbeat and ownership decision. Ownership is reread after lease acquisition;
fresh control reads before provisioning and handoff remain intact.

Candidates and active owners use the configured heartbeat interval. A current
owner blocked by disabled propagation or emergency hold sleeps for at least
60 seconds between cycles. Re-enabling propagation may therefore take one idle
interval plus API latency to be noticed.
The instance remains running and heartbeats continue; this is not shutdown.
The scheduler reuses the cycle's interval instead of issuing another control
read just to choose a sleep duration.

Successful cycle logs are emitted when the result changes, and after recovery
from an error. Errors and lifecycle events remain logged. Heartbeat records
continue to carry liveness evidence even when repeated cycle logs are suppressed.

Basic EC2 monitoring and AWS-owned DynamoDB encryption reduce monitoring and
KMS charges. The separate EC2 status alarm is retained. See
[decision 0004](decisions/0004-reduced-cost-operation.md) for deployment and
verification requirements.

## Generation limit and a new run

After handoff, a current owner at or above `max_generation` writes its final
heartbeat, logs `MAX_GENERATION_REACHED`, and exits with status 0. This takes
precedence over disabled propagation or an active hold. Candidates continue
heartbeating until they become current, preserving the predecessor's readiness
and handoff checks. The service uses `Restart=on-failure`; successful completion
does not restart it. No further polling or heartbeat writes occur.

Changing `max_generation` after completion does not resume that run. Treat it
as configuration for the next run, starting from generation 0. Do not restart
the completed service to extend a chain. Disable propagation and verify no
creation is in flight, then inspect and clean up generation resources through
CloudFormation before preparing a fresh run. Reconcile retained CURRENT and
bootstrap records through an explicitly reviewed operator path; do not merely
flip the old request Boolean or overwrite ownership. Preserve incident evidence
when a hold is active. Exiting the daemon leaves the instance running for inspection;
operator cleanup is still needed to end instance and storage charges.

## Stop and incident response

For a normal stop, set `CONTROL/GLOBAL.stop_requested=true`. A generation already
running remains intact, while every fresh successor create/handoff gate fails
closed. This does not cancel a separate bootstrap request; use its cancellation
procedure or an emergency hold. For an incident, also create `HOLD/ACTIVE` through the approved emergency path.

For manual cleanup, do not delete stacks while a provisioning request may
still be in flight. The approved automated overlap is limited to the exact
retired ancestor recorded by a successful handoff; it does not authorize
arbitrary cleanup during provisioning. Already accepted CloudFormation
operations may complete after stop/hold; do not assume they were cancelled.
Inspect `CURRENT/GLOBAL`, `LOCK/PROPAGATION`, generation state records, and
CloudFormation events first. Clear a hold only with
`scripts/clear_emergency_hold.py` after reconciling those resources.

## SDK runtime and AMI integration

The daemon creates six persistent boto3 clients (DynamoDB, CloudFormation, EC2,
Service Quotas, S3 and Lambda) in the IMDS-reported Region. Instance-profile
credentials use the normal refreshable SDK provider chain. Network calls have
2-second connect and 5-second read timeouts; SDK automatic retries are disabled
(one total attempt) so retry/reconciliation returns to the lifecycle's fresh
control gates. This setting applies to reads as well as writes. It is not a
global wall-clock deadline. Errors remain TransientFailure or the existing
conditional-write safety/conflict outcomes.

Install `daemon/requirements.txt` during AMI baking into the exact Python
interpreter selected by the service. The artifact includes the manifest but
not dependencies. With a virtual environment, configure the service to invoke
its Python explicitly; the existing executable otherwise uses `env python3`.
The separate AMI task must validate imports, IMDSv2, instance-role credentials,
CA certificates, region, and service startup before approving the image. No AMI
or service-template change is made by the transport migration. AWS CLI remains
required for the current user-data S3 download and operator scripts.

Run the full tests with the manifest installed and check that SDK tests are not
skipped. Rebuild and approve the immutable artifact hash along with a compatible
AMI before rollout. Old images containing only AWS CLI cannot run this daemon.
See [decision 0007](decisions/0007-persistent-sdk-clients.md).

## Artifact release

Run:

```sh
python3 scripts/build_daemon_artifact.py --output dist/cloud-glider-daemon.tar.gz
```

Upload the artifact and `cfn/generation.yaml` as immutable versioned objects.
Record their VersionIds and SHA-256 digests. Initialize control with those exact
values while propagation remains disabled, then bootstrap the first generation
using the same values. A rebuilt tarball has a new digest and must be explicitly
approved in control before it can propagate.

## Chain cleanup

See [the lifecycle decision](decisions/0012-shared-chain-lifecycle.md) and
[operator runbook](bootstrap.md). Daemons inherit RequestId through the approved
template and reject a stale cycle or active cleanup. Provisioning claims a durable
marker transactionally with lifecycle/HOLD checks. Heartbeats, CURRENT claims,
leases, and handoffs are fenced by the same cycle. Normal stop keeps instances
running; only explicit cleanup authorizes generation deletion without handoff.

## Minimal baked AMI

Use `DaemonDeliveryMode=baked`, `/dev/sda1` and a 2 GiB root only with an image
containing the current lifecycle daemon. The previously deployed minimal image
predates the current request/table contract. Follow the [reconciliation and
release runbook](minimal-ami.md) before a coordinated image/controller release.

## Applying the daemon update

Existing baked AMIs contain the old daemon. Build a new immutable daemon artifact
and rebuild the private ARM64 minimal AMI from this source using the AMI build
runbook. Validate baked delivery and the 2 GiB root; publish the new approved
image/template and daemon version/digest through the existing operator release
path. Verify the release fingerprint and all runtime stacks before bootstrap.
Keep propagation disabled and the existing hold until cleanup is independently
verified and an operator explicitly approves the next observed run. Updating
source alone does not update a baked AMI. This change performs no AWS deployment.

Direct EC2 propagation is available as an explicit offline transition. See [the EC2 runbook](ec2-propagation.md) and decision 0019. Existing CloudFormation cycles retain their current behavior.
