# Cloud Glider safety contract

This contract is normative. A change that violates an invariant must not be
merged or deployed, even if it makes a happy-path propagation test pass.

## System invariants

1. **Fresh operator control.** The agent must strongly read `CONTROL/GLOBAL` and
   `HOLD/ACTIVE` immediately before every `CreateStack` or `ExecuteChangeSet`
   operation that can provision successor compute. `propagation_enabled=false`
   or the existence of `HOLD/ACTIVE` stops successor provisioning.
   The separate operator bootstrap path in [decision 0002](decisions/0002-operator-bootstrap-request.md)
   permits only the first generation from UNINITIALIZED CURRENT and an explicit
   one-shot request, regardless of `propagation_enabled`. It still checks HOLD
   immediately before provisioning; HOLD also stops operator bootstrap.
2. **Approved definitions only.** Generation stacks use the prescribed stack
   name, generation service role, allowed parameters, S3 object version, and
   SHA-256 digest. Arbitrary template URLs and parameters are forbidden.
3. **No self-escalation.** A generation identity cannot create, modify, attach,
   detach, or delete IAM policies, roles, instance profiles, permission
   boundaries, or SCPs.
4. **Safe predecessor survival.** A predecessor is not retired until its
   successor is healthy, the continuation gate has passed, and ownership has
   changed through a successful conditional write.
5. **Idempotent coordination.** Leases, state transitions, request tokens, and
   ownership changes are deterministic or protected by conditional writes.
   Timeouts are reconciled against AWS state before retry.
6. **Attribution.** Supported resources carry `project=cloud-glider`,
   `environment`, `generation`, `owner`, and `purpose` tags. Every change is
   preserved in the canonical CloudTrail archive and projected to its audit
   category where supported.
7. **Operator precedence.** Operator stop state takes precedence over retries,
   automated recovery, handoff, and propagation.
8. **Bounded concurrency.** Three live generation instances is the absolute
   ceiling. Preferred operation has two or fewer by preflighting N+2, retiring
   N, and only then provisioning N+2. A fourth instance is an invariant breach.

## Accepted starting decisions

- One AWS account in `us-west-2`; account identifiers are supplied at deployment
  time by CloudFormation pseudo-parameters.
- The operator deploys `cfn/network.yaml` to create the dedicated VPC, public
  subnet, internet-gateway route, and no-inbound security group. Its subnet and
  security-group IDs are supplied to the foundation stack. Each generation
  receives one ephemeral public IPv4 address at launch solely through the
  approved generation template. Generation agents cannot create or mutate
  VPCs, subnets, routes, gateways, or security groups.
- Generation instances accept no inbound traffic: the approved security group
  has no ingress rules, no SSH path is created, and IMDSv2 remains required.
  Outbound access is limited to what is required for HTTPS AWS API calls.
- Public IPv4 assignment is a property of the primary launch ENI. Agents cannot
  allocate or associate Elastic IPs or directly create, delete, or modify
  network interfaces.
- `t4g.micro` is the only initially approved instance type. The AMI and every
  bootstrap dependency must support Linux `arm64`.
- Successor readiness requires CloudFormation `CREATE_COMPLETE` followed by the
  configured number of consecutive eligible DynamoDB heartbeats, separated by
  at least `heartbeat_interval_seconds` (two heartbeats and five seconds in the
  sandbox baseline).
- An eligible heartbeat must match the expected generation ID, instance ID,
  stack ID, complete template identity, bootstrap version, and observed
  propagation state.
- Readiness is polled every 2 seconds. The 10-minute successor wait includes
  stack creation; heartbeat acceptance begins only after `CREATE_COMPLETE`.
  Candidates retain their configured heartbeat cadence. Only a current owner
  blocked by operator control, hold, or the generation boundary uses a minimum
  60-second loop interval. Every provisioning and handoff gate still checks
  fresh control and ownership; idle polling is not permission caching.
- EC2 basic monitoring retains free one-minute status-check metrics. The
  separate status alarm remains corroborating telemetry, not a readiness gate.
- The DynamoDB state table remains encrypted with an AWS-owned key. Disabling
  its CloudFormation `SSEEnabled` option selects this key, not plaintext storage.
- The approved generation definition is the template bucket/key, immutable S3
  VersionId, SHA-256 digest, template version, and Git commit or build ID. The
  agent executable is independently approved by bucket/key, immutable S3
  VersionId, and SHA-256 digest. Any mismatch is terminal.
- A terminal policy, identity, ownership, or invariant error atomically records
  `ERROR`, creates `HOLD/ACTIVE`, and preserves the recoverable generation. The
  agent invokes a dedicated function that can create but not delete the record;
  only an operator can clear it.
- DynamoDB tables, CloudWatch log groups, and audit archives are retained on
  stack deletion and replacement.

## Initial control state

`scripts/initialize_control.py` creates these records in one transaction:

- `CONTROL/GLOBAL`: propagation disabled, maximum generation 2, ceiling 3,
  `us-west-2`, `t4g.micro`, `arm64`, readiness values, and the complete immutable
  template identity tuple.
- `CONTROL/GLOBAL` also carries the immutable agent artifact identity tuple.
- `CURRENT/GLOBAL`: no authoritative running generation and status
  `UNINITIALIZED`.
- `AUDIT#PROPAGATION/EVENT#...`: attribution for the initialization operation.

No `HOLD/ACTIVE` item means the system is not held. Its existence means the hold
is active; the `active` Boolean is descriptive and is not used to clear it.

### HOLD record representation

The hold is a normal DynamoDB item, not a special DynamoDB data type:

- `PK`: String (`S`) with value `HOLD`
- `SK`: String (`S`) with value `ACTIVE`
- `active`: Boolean (`BOOL`) with value `true`, for readability only
- `created_at`, `created_by`, `generation`, `error_code`, `correlation_id`,
  `event_id`, and `environment`: Strings (`S`)

The item has no TTL. The Lambda creates it with
`attribute_not_exists(PK) AND attribute_not_exists(SK)`, cannot update or delete
it, and records the generation error in the same transaction. The operator-only
clear path deletes the item and appends a separate audit event atomically.

The transaction refuses to overwrite existing records. Subsequent changes must
use a separate, conditionally guarded operator workflow and emit before/after
audit values.

## Required negative tests before propagation

- Disable propagation between the cycle read and final provisioning read; no
  create or execute call occurs.
- Start duplicate agents for one generation; only one lease owner and at most
  one successor result.
- Inject a timeout after CloudFormation request submission; reconciliation does
  not create a duplicate stack.
- Break successor bootstrap; the predecessor remains.
- Fail the conditional handoff; the predecessor is not deleted.
- Attempt arbitrary `iam:PassRole`, IAM mutation, direct EC2 creation or
  termination, unrelated stack creation, and foundation deletion; all fail.
- Attempt direct Elastic IP and network-interface mutation from the agent role;
  all fail. Confirm the approved template launches exactly one primary ENI with
  one ephemeral public IPv4 address and the no-inbound security group.
- Change the template version, digest, or parameters; the agent rejects it.
- Keep propagation enabled at `max_generation`; the chain stops.
- Exercise preferred concurrency across multiple cycles; never observe four
  live generations.

## Review rule

Any change to an invariant, IAM permission, audit category, or default safety
value requires a security-focused review and a corresponding audit entry. The
generation template must never use `CAPABILITY_IAM` or contain `AWS::IAM::*`.

## Independent permission ceilings

Runtime roles require their separately administered role-specific boundaries.
The foundation deployer cannot edit those boundary policies or replace its own
ceiling. CreateStack/CreateChangeSet require the exact approved versioned URL;
retirement and preflight cleanup remain separate. Empty or inconsistent release
approvals prevent new compute. Role boundary and organization-policy changes
follow [the guardrail runbook](../iam/permission-guardrails.md). These controls do
not replace the control-state, ownership, health or concurrency invariants.
