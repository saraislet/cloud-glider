# Propagation agent runbook

## Bootstrap

The operator toggles `BOOTSTRAP/REQUEST.bootstrap_requested` from false to true
to ask the bootstrap Lambda for the first generation. This is independent of `CONTROL/GLOBAL.propagation_enabled`;
the Lambda does not modify or gate on that flag. Keep it false for initial
inspection. `HOLD/ACTIVE` blocks both paths. See [the bootstrap runbook](bootstrap.md).

## Lifecycle

Each process verifies that IMDSv2 instance identity and its CloudFormation
stack agree with `/etc/cloud-glider/bootstrap.json`. The operator-bootstrapped
generation may conditionally claim an `UNINITIALIZED` `CURRENT/GLOBAL` record.
All other generations begin as candidates.

Every loop writes `GEN#{generation}/STATE`. A current owner may continue only
when `CONTROL/GLOBAL.propagation_enabled=true`, `HOLD/ACTIVE` is absent, and its
generation is below `max_generation`. It then:

1. conditionally acquires `LOCK/PROPAGATION`;
2. strongly rereads operator control and verifies the versioned template digest;
3. creates or reconciles the deterministic successor stack;
4. waits for `CREATE_COMPLETE` and distinct eligible heartbeats;
5. checks live-generation headroom, regional `t4g.micro` offering, and vCPU quota;
6. creates and discards an unexecuted `CREATE` change set for the next hop;
7. atomically transfers `CURRENT/GLOBAL` after one more fresh control check; and
8. submits predecessor stack deletion through CloudFormation.

The predecessor is never deleted before step 7 succeeds. No code path executes
the continuation change set.

## Approved overlapping handoff design

[Decision 0005](decisions/0005-overlapping-handoff.md) allows N deletion and
N+2 creation to overlap after healthy N+1 passes continuation and owns CURRENT.
The existing DeleteStack call is asynchronous, so some overlap is already
possible. [Decision 0006](decisions/0006-preflight-during-successor-boot.md) also
permits unexecuted N+2 preflight during N+1 boot, joining both successful results
before handoff. The runtime still performs those waits sequentially; the
required health refresh, durable retirement reconciliation, and
conservative capacity accounting are not all implemented. Do not interpret
this design approval as a completed runtime release. AMI baking is separate.

## DynamoDB records

- `CONTROL/GLOBAL`: operator control, limits, timing, and approved immutable
  template/bootstrap/agent identities.
- `BOOTSTRAP/REQUEST`: retained one-shot operator request and submission status.
- `CURRENT/GLOBAL`: authoritative generation, stack, instance, status, and
  handoff token.
- `GEN#{generation}/STATE`: identity and heartbeat evidence. Writes are
  conditional on the original stack and instance and cannot overwrite `ERROR`.
- `LOCK/PROPAGATION`: expiring owner token used to serialize propagation.
- `HOLD/ACTIVE`: append-only incident stop created only by the dedicated Lambda.
- `AUDIT#PROPAGATION/EVENT#{timestamp}#{uuid}`: transactional handoff audit.

CloudTrail is authoritative for AWS API changes. The service writes structured
JSON lifecycle events to the systemd journal; the configured EC2 status alarm
is corroborating telemetry and never a readiness gate.

## Polling and request cost

An ordinary cycle reads control/hold once, reads CURRENT once, and writes one
heartbeat. The CURRENT snapshot is shared only within that cycle's initial
heartbeat and ownership decision. Ownership is reread after lease acquisition;
fresh control reads before provisioning, preflight, and handoff remain intact.

Candidates and active owners use the configured heartbeat interval. A current
owner blocked by disabled propagation, emergency hold, or `max_generation`
sleeps for at least 60 seconds between cycles. Re-enabling propagation or raising
the boundary may therefore take one idle interval plus API latency to be noticed.
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

## Stop and incident response

For a normal stop, set `propagation_enabled=false`. A generation already
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

The agent creates six persistent boto3 clients (DynamoDB, CloudFormation, EC2,
Service Quotas, S3 and Lambda) in the IMDS-reported Region. Instance-profile
credentials use the normal refreshable SDK provider chain. Network calls have
2-second connect and 5-second read timeouts; SDK automatic retries are disabled
(one total attempt) so retry/reconciliation returns to the lifecycle's fresh
control gates. This setting applies to reads as well as writes. It is not a
global wall-clock deadline. Errors remain TransientFailure or the existing
conditional-write safety/conflict outcomes.

Install `agent/requirements.txt` during AMI baking into the exact Python
interpreter selected by the service. The artifact includes the manifest but
not dependencies. With a virtual environment, configure the service to invoke
its Python explicitly; the existing executable otherwise uses `env python3`.
The separate AMI task must validate imports, IMDSv2, instance-role credentials,
CA certificates, region, and service startup before approving the image. No AMI
or service-template change is made by the transport migration. AWS CLI remains
required for the current user-data S3 download and operator scripts.

Run the full tests with the manifest installed and check that SDK tests are not
skipped. Rebuild and approve the immutable artifact hash along with a compatible
AMI before rollout. Old images containing only AWS CLI cannot run this agent.
See [decision 0007](decisions/0007-persistent-sdk-clients.md).

## Artifact release

Run:

```sh
python3 scripts/build_agent_artifact.py --output dist/cloud-glider-agent.tar.gz
```

Upload the artifact and `cfn/generation.yaml` as immutable versioned objects.
Record their VersionIds and SHA-256 digests. Initialize control with those exact
values while propagation remains disabled, then bootstrap the first generation
using the same values. A rebuilt tarball has a new digest and must be explicitly
approved in control before it can propagate.
