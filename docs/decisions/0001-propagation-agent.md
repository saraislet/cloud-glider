# Decision 0001: first-pass propagation agent

Status: accepted for the sandbox first pass

## Decision

The original first pass used a dependency-free Python agent and AWS CLI v2.
[Decision 0007](0007-persistent-sdk-clients.md) supersedes that transport with
persistent boto3 clients and a pinned AMI-installed dependency manifest.
The agent remains a deterministic `tar.gz`; no on-instance build is required.

The approved template identity is its bucket, key, immutable S3 VersionId,
SHA-256 digest, template version, and build ID. The approved executable identity
is its bucket, key, immutable S3 VersionId, and SHA-256 digest. Both identities
are stored in `CONTROL/GLOBAL`; successor parameters are derived from that
record rather than trusted from the running predecessor.

Coordination uses one expiring `LOCK/PROPAGATION` lease. This is deliberately a
small first-pass coordination mechanism, not a distributed consensus system.
All ownership transfer still occurs through a DynamoDB transaction conditioned
on current ownership, lease ownership, enabled propagation, and absence of
`HOLD/ACTIVE`.

The agent itself is the initial workload health signal. An eligible heartbeat
proves process liveness plus generation, stack, instance, predecessor, handoff
token, template, bootstrap, agent-artifact, and observed operator-control
identity. A future workload must add its own explicit health result before the
meaning of `workload_healthy=true` is broadened.

Continuation proof is an unexecuted CloudFormation `CREATE` change set after
basic live-instance, regional offering, and standard-instance vCPU quota
checks. The change set and its empty `REVIEW_IN_PROGRESS` stack are discarded.
At `max_generation`, reaching the approved boundary satisfies the continuation
gate without constructing an out-of-policy generation.

## Later clarification

[Decision 0005](0005-overlapping-handoff.md) permits predecessor deletion to
overlap the next creation after healthy ownership transfer. It defines the
capacity, retirement, and failure handling required before releasing that path.

## Consequences

- No compiler, package index, long-lived key, direct EC2 mutation, or IAM
  mutation is needed on a generation.
- API submission timeouts reconcile by deterministic stack name and parameters.
- Missing or ambiguous health, capacity, control, or AWS responses preserve the
  predecessor and retry.
- Identity, ownership, and invariant conflicts invoke the dedicated emergency
  hold function and stop that agent process.
- More robust lease coordination, automated cleanup/recovery, and multi-account
  operation remain deferred.
