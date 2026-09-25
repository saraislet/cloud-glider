# Decision 0005: overlapping retirement and next-generation creation

Status: accepted design; runtime implementation and release validation pending.

## Decision and scope

After N+1 passes explicit health validation, the N+2 continuation gate passes,
and ownership conditionally transfers to N+1, allow CloudFormation deletion
of N to run concurrently with CloudFormation creation of N+2. Waiting for
DELETE_COMPLETE is unnecessary when the configured live ceiling has room.
This clarifies PREFLIGHT_THEN_RETIRE: preflight precedes retirement; completed
retirement need not precede the next creation. No new runtime mode is added.

This changes the former preference for waiting for retirement before creation.
It does not permit deleting N while its immediate successor N+1 is unready,
executing the preview change set, binary fan-out, direct EC2 mutation, or a
fourth live instance. The lower configured ceiling always wins. At the terminal
generation boundary the existing continuation exception applies; no N+2 is
created and terminal leaf retirement is not introduced.

## Required ordering

1. Under the propagation lease, create or reconcile approved N+1. Once its
   exact stack identity and approved parameters are known, begin two bounded
   activities: wait for CREATE_COMPLETE and the required consecutive eligible
   heartbeats, and prepare the unexecuted N+2 CREATE change-set preflight.
2. Join both successful results. The preflight requires fresh control, identity,
   quota and capacity checks before submission; it never executes or launches
   N+2. Reconcile removal of its empty preview stack before real N+2 creation;
   a DELETE_IN_PROGRESS preview is not a usable successor. At max_generation,
   the existing boundary exception replaces the preflight activity.
3. Re-read N+1 health after preflight. Check freshness, identity, ownership,
   current control and hold; never transfer using stale preflight-era evidence.
4. Atomically transfer CURRENT to N+1 and persist retirement intent identifying
   the exact N stack, service role, deterministic request token, and handoff.
5. Submit N deletion after fresh control and retirement-authorization checks.
   Release the propagation lease without waiting for physical deletion.
6. N+1 acquires the lease, rechecks ownership, control, approved identities and
   capacity, then creates N+2 while N deletion completes. No owner may use a
   successful handoff as cached permission for a later API submission.

Accepted deletion submission must be confirmed or reconciled before admitting
N+2 in this overlap path. Unknown deletion status blocks new work until resolved.
CloudFormation completion is asynchronous and is tracked independently.

## Bounded state and failures

Count N while it is pending, running, stopping, stopped, shutting down, or of
unknown termination status. Include an accepted or unresolved create request
before the instance becomes visible in EC2. Admission and deterministic create
identity must be serialized by the existing lease and durably reconciled after
crash or lease turnover. DescribeInstances alone is insufficient during an
in-flight create. No slot is freed merely by DeleteStack acceptance.

With three occupied slots (N retiring, N+1 current, N+2 candidate), defer further
creation until a slot is authoritatively free. With a configured ceiling of two,
wait for N termination before N+2 creation. Do not automatically raise limits.

Retirement intent survives the retiring process. A narrowly scoped successor
reconciliation path may retry only its recorded ancestor after checking control,
health, ownership and exact identity. This is approval for handoff retirement
recovery, not general cleanup or quarantine. Specify conditional record updates
and audit evidence in the implementation; no runtime schema is changed here.

Stop/hold blocks new create, handoff and delete submissions, including retries.
Already accepted operations may finish; they cannot be treated as cancelled.
Delete failure retains its occupied slot and requires reconciliation; never
transfer back to an ancestor whose deletion has been accepted. Preserve the
current owner and stop advancement when successor health or ownership is
ambiguous. Duplicate executions and uncertain API responses must reconcile the
same request, never invent another stack or retirement target.

## Current implementation gap

The runtime already submits DeleteStack without a completion waiter. Therefore
this design alone may produce little speedup. It still needs fresh health after
preflight, durable retirement retry after ownership transfer, preview cleanup
reconciliation, and capacity accounting that includes shutting-down instances
and unresolved creates. No performance improvement or safety completion is
claimed by this documentation change. IAM and deployed infrastructure are unchanged.

## Performance opportunities, separate from AMI work

- First measure CreateStack submission/acceptance, CREATE_COMPLETE, first and
  accepted heartbeat, preflight completion/cleanup, handoff, DeleteStack
  acceptance, EC2 termination and DELETE_COMPLETE. Use structured events and
  aggregated p50/p95 durations, not per-instance paid metrics. Track both
  throughput and total billable instance/volume lifetime.
- [Decision 0007](0007-persistent-sdk-clients.md) implements persistent SDK
  clients. Install the pinned manifest through the separate AMI/release work.
  Benchmark before claiming savings; lifecycle overlap remains pending.
- The approved [boot/preflight overlap](0006-preflight-during-successor-boot.md)
  changes the ideal wait from boot + preflight to max(boot, preflight), plus
  final revalidation and cleanup. Runtime implementation is still pending.
- Avoid unnecessary delay after ownership acquisition, while retaining the
  candidate heartbeat cadence and fresh gates. Current active polling is already
  two seconds for readiness and five seconds for heartbeats; do not weaken the
  two-heartbeat health requirement just to shave time.
- Measure preview-stack and per-generation alarm work before proposing template
  reductions. Shared monitoring is a separate reviewed change with an equivalent
  failure signal; do not silently remove the existing alarm.
- Keep retirement recovery off the normal creation critical path once acceptance
  is known, but enforce occupied slots and bounded retries. Overlap ideally turns
  delete + create waiting into max(delete, create); the current asynchronous
  implementation already captures part of that benefit.

AMI dependencies, service configuration and agent baking are being handled in a
separate task. This overlap decision does not change AMI IDs, bootstrap,
or that task's release decisions. Decision 0007 supplies SDK dependency metadata. Coordinate on pinned runtime dependencies and
immutable agent identity when implementing SDK reuse.

## Verification and release

Implement the negative cases in the safety contract before an overlap release:
stale health after preflight; disabled propagation and emergency hold at each
gate; duplicate execution and lease turnover; delayed preview deletion; uncertain
create/delete responses; failed handoff; failed retirement; configured ceilings
of two and three; and slow deletion over several hops. Prove no fourth instance
and no unapproved delete under fault injection. Run a small supervised trial
only through the approved release path, with timestamps and cost attribution.

Documentation approval does not deploy or enable a paid test. Review new
retirement state and existing role boundaries before implementing recovery;
permission expansion requires its own explicit assignment.
