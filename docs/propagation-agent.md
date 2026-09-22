# Propagation agent runbook

## Bootstrap

The operator creates `BOOTSTRAP/REQUEST` to ask the bootstrap Lambda for the
first generation. This is independent of `CONTROL/GLOBAL.propagation_enabled`;
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

## Stop and incident response

For a normal stop, set `propagation_enabled=false`. A generation already
running remains intact, while every fresh successor create/handoff gate fails
closed. This does not cancel a separate bootstrap request; use its cancellation
procedure or an emergency hold. For an incident, also create `HOLD/ACTIVE` through the approved emergency path.

Do not delete stacks while a provisioning request may still be in flight.
Inspect `CURRENT/GLOBAL`, `LOCK/PROPAGATION`, generation state records, and
CloudFormation events first. Clear a hold only with
`scripts/clear_emergency_hold.py` after reconciling those resources.

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
