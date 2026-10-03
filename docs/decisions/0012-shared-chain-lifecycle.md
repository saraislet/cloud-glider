# Decision 0012: shared chain lifecycle and operator-requested cleanup

Status: accepted for implementation by the operator on October 2, 2026;
AWS deployment remains a separately reviewed action.

The operator interface is superseded by [decision 0013](0013-control-operator-switches.md).
The internal lifecycle and cleanup mechanics below remain in effect.

## Decision

Use the existing `BOOTSTRAP/REQUEST` item as the reusable lifecycle record.
It owns `bootstrap_requested`, `propagation_enabled`, and `cleanup_requested`,
bootstrap `status`, and `cleanup_status`. `CONTROL/GLOBAL` continues to contain
approved artifact identities, generation limits, and timing settings, but no
propagation switch. `HOLD/ACTIVE` remains an independent, preserved incident gate.
This intentionally supersedes decision 0002's permanent one-shot marker and
CONTROL propagation-switch placement. Normal propagation stop still preserves
running instances. Only an explicit cleanup request authorizes deleting the chain
without the normal healthy-successor retirement gate.

Use positive incremental request IDs, stored as decimal strings (`1`, `2`, ...),
independent of CloudFormation stack IDs. The request ID identifies bootstrap,
successors, preflight stacks, heartbeats, CURRENT, and cleanup for one cycle.
Approved generation templates carry a required `RequestId` parameter and tag.
Conditional transactions fence worker writes and provisioning to the current
cycle. Old workloads cannot write new cycle state or claim CURRENT after reset.

Keep only the most recent cleanup details in the lifecycle item. The next cleanup
replaces those details; completed cleanup requests are not archived as separate
DynamoDB records. Existing CloudTrail and application logs retain their established retention policies. Decision 0014 moves generation state into its own table and uses bounded latest-event audit records. Resource inventory entries under
`GEN#.../RESOURCE#<stack ARN>` identify live or retired stacks during a cycle and
are deleted with obsolete generation state after cleanup verification.

## Cleanup sequence

The operator helper atomically sets cleanup true and both launch switches false.
A console false-to-true cleanup toggle also triggers the controller, which closes
both launch gates in its first write. Both agents and bootstrap reject cleanup
true even before the controller changes status.

The controller transitions `QUIESCING -> DELETING -> VERIFYING -> COMPLETE`.
A non-expiring `LOCK/PROVISIONING` marker fences each stack/change-set submission.
It is claimed transactionally with current lifecycle and HOLD checks, then released
only after the API response and exact stack inventory are durable. Cleanup cannot
reset while that marker exists. It waits up to three minutes for an active submission;
a missing response or crash requires operator reconciliation, never lease expiry.
This is a deliberately small submission interlock, not advanced lease recovery.

An EventBridge rule calls the existing bootstrap Lambda once a minute to resume
active cleanup. Timed calls never bootstrap or propagate. Reserved concurrency
of one serializes controller invocations. Cleanup has a 30-minute deadline;
failures enter `NEEDS_ATTENTION`, disable launch gates, retain inventory, and
raise a Lambda error to the existing operational alarm. An inspected cleanup can
be resumed without dropping its inventory or changing its cycle identity.

Inventory combines paginated generation-stack listing with exact IDs recorded
in the lifecycle, CURRENT, generation state, and resource inventory. All stacks
must match environment, cycle parameter/tag, purpose, name, and designated service
role before deletion. Active creation/rollback operations settle first.
DeleteStack uses exact stack ARNs and deterministic request tokens. The controller
never directly terminates instances or mutates EC2/network/IAM resources.

Completion requires stack deletion confirmation, no DELETE_SKIPPED resources,
no live tagged instances, no captured root volumes, and no residual tagged EBS
volumes, snapshots, or elastic IPs. Orphans/conflicts require inspection. Shared
foundation/network/bootstrap/billing resources, versioned artifacts, state, and
audit/log storage intentionally remain. COMPLETE means generation cleanup, not
zero AWS cost or account-wide decommissioning.

After verification, conditionally delete old generation state; then atomically
reset CURRENT, remove the old propagation lease, prepare bootstrap READY with a
fresh control fingerprint, increment the request number, and clear all request
switches. Preserve the previous cleanup target ID and completion details in the
same item until the next cleanup. HOLD is never changed, so READY is preparation,
not authorization or proof that an incident was resolved.

## Permissions, rollout, and cost

The controller may inspect/delete only this environment's generation stacks and
reset only Cloud Glider state. Read-only stack enumeration and EC2 inventory need
wildcard resources because those listing APIs do not support per-object scoping.
DynamoDB Scan is limited to this state table. Agent access to lifecycle/control is
read/condition-check only; no lifecycle Boolean writes are granted. PassRole
remains limited to the designated service role and CloudFormation service.
No deployment-role grants, workflows, secrets, or repository security changes.

The timer adds bounded Lambda invocations and consistent state reads, plus cleanup
inventory/log/API activity. Review that recurring cost and template change sets
before deploying. No AWS changes are performed by repository implementation.

This release requires an offline migration, new immutable template and agent
artifacts, and coordinated foundation/bootstrap updates. Legacy schema and
missing cycle identity fail closed. Do not hot-migrate a live chain. Follow the
[bootstrap and cleanup runbook](../bootstrap.md).

## Submission bookkeeping recovery

A SUBMITTED bootstrap retry verifies its recorded exact stack ID, parameters, role and cycle tags, restores inventory, then conditionally releases its matching submission marker. Scheduled retries retain an accepted START when final bookkeeping fails; they never resubmit compute. Missing, foreign or ambiguous evidence reports NEEDS_ATTENTION and preserves the marker.

SDK coordination writes retain DynamoDB cancellation reasons. Only a confirmed failed coordination condition with a still-valid lifecycle can raise an ownership safety violation. Contention, throttling, lifecycle fencing and missing cancellation evidence defer the attempt instead of setting an emergency hold. Regression tests cover these cases and interruption after submission.

The record placement and DynamoDB audit retention described here are superseded by [decision 0014](0014-separated-generation-state.md). Cycle fencing and cleanup verification remain unchanged.
