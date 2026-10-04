# Cloud Glider safety contract

This contract is normative. A change that violates an invariant must not be
merged or deployed, even if it makes a happy-path propagation test pass.

## System invariants

1. **Fresh operator control.** The daemon must transactionally read `CONTROL/GLOBAL`, `BOOTSTRAP/REQUEST`, and
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
   real successor passes approved identity/configuration and health checks, and ownership has
   changed through a successful conditional write. Explicit operator cleanup is
   the exception: it first fences provisioning, then deletes verified cycle stacks
   through CloudFormation. Normal stop preserves running instances.
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
   ceiling, further limited by CONTROL. After fresh N+1 health validation,
   approved successor identity/configuration, and conditional ownership transfer,
   CloudFormation deletion of N may overlap creation of N+2. Completion of N
   deletion is not a prerequisite when a slot remains. Retiring instances and
   unresolved create requests consume slots until authoritative reconciliation
   proves them absent; a deletion request alone does not release capacity.
   Never admit N+3 while N, N+1, and N+2 occupy the three slots. A fourth
   instance is an invariant breach. See [decision 0005](decisions/0005-overlapping-handoff.md).

## Real successor validation

No separate N+2 continuation change set is created. Before handoff, reread the
real N+1 stack ID, parameters, service role, completed creation status, instance
identity and fresh eligible health record. Fresh control plus transactional
hold, lease and CURRENT ownership checks still gate transfer. Missing or
ambiguous evidence preserves N. Capacity/quota checks apply before every actual
creation. See [decision 0018](decisions/0018-remove-continuation-preflight.md).

## Overlap release requirements

This is an approved design change, not a claim that the existing runtime
implements every guard. Before an overlap release, revalidate successor health
immediately before handoff, durably record retirement
intent with handoff, reconcile deletion failures, and account for in-flight
creates and terminating instances under the propagation lease. A fresh stop or
hold blocks new create, handoff, and retirement submissions; accepted AWS
operations can finish. Missing or ambiguous evidence blocks additional work.

## Accepted starting decisions

- One AWS account in `us-west-2`; account identifiers are supplied at deployment
  time by CloudFormation pseudo-parameters.
- The operator deploys `cfn/network.yaml` to create the dedicated VPC, public
  subnet, internet-gateway route, and no-inbound security group. Its subnet and
  security-group IDs are supplied to the foundation stack. Each generation
  receives one ephemeral public IPv4 address at launch solely through the
  approved generation template. Generation daemons cannot create or mutate
  VPCs, subnets, routes, gateways, or security groups.
- Generation instances accept no inbound traffic: the approved security group
  has no ingress rules, no SSH path is created, and IMDSv2 remains required.
  Outbound access is limited to what is required for HTTPS AWS API calls.
- Public IPv4 assignment is a property of the primary launch ENI. Daemons cannot
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
  blocked by operator control or hold uses a minimum
  60-second loop interval. The current owner exits successfully at the generation
  limit, including when propagation is disabled or held. Candidates continue
  heartbeating until handoff makes them current. Every provisioning and handoff gate still checks
  fresh control and ownership; idle polling is not permission caching.
- EC2 basic monitoring retains free one-minute status-check metrics. The
  separate status alarm remains corroborating telemetry, not a readiness gate.
- The DynamoDB state table remains encrypted with an AWS-owned key. Disabling
  its CloudFormation `SSEEnabled` option selects this key, not plaintext storage.
- The approved generation definition is the template bucket/key, immutable S3
  VersionId, SHA-256 digest, template version, and Git commit or build ID. The
  daemon executable is independently approved by bucket/key, immutable S3
  VersionId, and SHA-256 digest. Any mismatch is terminal.
- A terminal policy, identity, ownership, or invariant error atomically records
  `ERROR`, creates `HOLD/ACTIVE`, and preserves the recoverable generation. The
  daemon invokes a dedicated function that can create but not delete the record;
  only an operator can clear it.
- DynamoDB tables, CloudWatch log groups, and audit archives are retained on
  stack deletion and replacement.

## Initial control state

`scripts/initialize_control.py` creates these records in one transaction:

- `CONTROL/GLOBAL`: propagation disabled, maximum generation 2, ceiling 3,
  `us-west-2`, `t4g.micro`, `arm64`, readiness values, and the complete immutable
  template identity tuple.
- `CONTROL/GLOBAL` also carries the immutable daemon artifact identity tuple.
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
- Start duplicate daemons for one generation; only one lease owner and at most
  one successor result.
- Inject a timeout after CloudFormation request submission; reconciliation does
  not create a duplicate stack.
- Break successor bootstrap; the predecessor remains.
- Fail the conditional handoff; the predecessor is not deleted.
- Attempt arbitrary `iam:PassRole`, IAM mutation, direct EC2 creation or
  termination, unrelated stack creation, and foundation deletion; all fail.
- Attempt direct Elastic IP and network-interface mutation from the daemon role;
  all fail. Confirm the approved template launches exactly one primary ENI with
  one ephemeral public IPv4 address and the no-inbound security group.
- Change the template version, digest, or parameters; the daemon rejects it.
- Keep propagation enabled at `max_generation`; the chain stops.
- Delay N deletion while N+2 launches; permit overlap only after all handoff
  gates and never admit a fourth instance. Test a configured ceiling of two.
- Fail or time out deletion after handoff; retain durable retirement intent,
  reconcile the exact stack, and prevent duplicate or unrelated deletion.
- Expire N+1 health before handoff; do not hand off or retire N.
- Verify propagation creates only the real successor, without preview placeholders.
  Fail or time out either activity, change control or identity during the join,
  or lose the lease; preserve N and reconcile the exact preview without execution.
- Restart while both activities are in flight; reconcile deterministic requests
  and rebuild fresh readiness evidence without duplicate preview stacks.
- Stop or hold between handoff and either parallel submission; submit no new
  operation after observing stop, and reconcile already accepted operations.
- Delay preview-stack cleanup, API visibility, or lease release; preserve
  capacity accounting and prevent duplicate creation across multiple cycles.

## Review rule

Any change to an invariant, IAM permission, audit category, or default safety
value requires a security-focused review and a corresponding audit entry. The
generation template must never use `CAPABILITY_IAM` or contain `AWS::IAM::*`.

## Independent permission ceilings

Runtime roles require their separately administered role-specific boundaries.
The foundation deployer cannot edit those boundary policies or replace its own
ceiling. CreateStack/CreateChangeSet require the exact approved versioned URL;
retirement and legacy preview cleanup remain separate. Empty or inconsistent release
approvals prevent new compute. Role boundary and organization-policy changes
follow [the guardrail runbook](../iam/permission-guardrails.md). These controls do
not replace the control-state, ownership, health or concurrency invariants.

## Reusable lifecycle and operator requests

Decisions [0012](decisions/0012-shared-chain-lifecycle.md) and [0013](decisions/0013-control-operator-switches.md) move authoritative propagation permission into BOOTSTRAP/REQUEST. CONTROL/GLOBAL holds operator request switches and feedback. Pending stop/cleanup blocks successor creation and handoff before acknowledgement. Every worker is fenced to its incremental request ID; non-expiring submission markers and exact inventory prevent cleanup from resetting around ambiguous creation. Bootstrap may enable propagation immediately; automatic health, ownership, template and capacity gates remain. Cleanup preserves HOLD and independent permission boundaries.

Decision [0014](decisions/0014-separated-generation-state.md) places generation state/inventory in a separate DynamoDB table. Cross-table transactions retain lifecycle and ownership conditions; approved table identity is checked before use. DynamoDB audits reuse latest-event keys instead of preserving a full event history.

For explicitly selected EC2 cycles, decision [0019](decisions/0019-direct-ec2-propagation.md) supersedes the CloudFormation-only provisioning, preflight and retirement rules. The existing lifecycle schema, operator switches and separate generation table remain authoritative; see [the EC2 runbook](ec2-propagation.md).

EC2 decision [0020](decisions/0020-ec2-retirement-overlap.md) permits candidate
launch after accepted retirement while preserving CURRENT's durable retirement
intent until exact termination. Handoff waits for that confirmation. Capacity
counts shutting-down instances and exact CURRENT/predecessor identities even
when filtered inventory omits them. Missing results never free a slot.
