# Decision 0006: continuation preflight during successor boot

Status: accepted design; runtime implementation and validation pending.

## Decision

Allow N to prepare the unexecuted N+2 CloudFormation CREATE change set while
N+1 boots and establishes readiness. Start only after reconciling the exact
approved N+1 stack identity and parameters. This is separate from decision
0005's overlap of N deletion with actual N+2 creation after handoff.

Keep the existing CREATE_COMPLETE and consecutive-heartbeat readiness rule.
The preview creates no N+2 instance and is never executed. Both branches must
succeed, followed by fresh joined validation, before ownership transfer or N
retirement. At the generation boundary, use the existing continuation exception
and do not submit an out-of-policy preview.

## Coordination and final gate

One propagation lease owner coordinates both bounded activities and renews the
lease throughout their waits and cleanup. Use deterministic preview identity,
request tokens and approved parameters, reconciling uncertain API responses
before retry. A restart must reconstruct actual AWS state and collect fresh
health evidence; it cannot trust an in-memory success flag from a prior attempt.

Read control and hold immediately before preview submission. Tie both results
to the same approved control/template/agent identity and successor stack. After
both succeed, re-read successor health, current control, hold, ownership, lease
and quota/capacity. A stale heartbeat, changed control identity, changed limit,
lease loss or ambiguous response invalidates the join. Recollect required health
or restart validation; never hand off using cached permission. Preserve the
conditional DynamoDB handoff and all fresh checks at later create/delete gates.

If either activity fails or times out, do not hand off or retire N. Stop starting
new work and reconcile requests already accepted by AWS. Preserve N and existing
compute; do not roll back a candidate through unapproved direct resource deletion.
Use the configured readiness timeout as a bounded deadline for each activity;
do not extend readiness indefinitely because preflight is slow.

Track the exact change set and empty preview stack until cleanup is confirmed.
Only that approved empty preview may be discarded; never delete a populated
successor stack on the assumption that it is still a preview. Stop/hold blocks
new mutation submissions including cleanup retries; record pending cleanup for
reconciliation after operator clearance. Accepted operations may finish. Real
N+2 creation must wait for preview-stack removal/name reuse to be confirmed.
Cleanup failures must not be silently ignored or mistaken for successful readiness.

This authorizes no live N+2 during preflight, no extra live-generation slot, no
lease framework replacement, no IAM expansion, and no AMI changes. The separate
AMI task owns dependencies, service configuration and agent baking.

## Performance and validation

The ideal combined wait changes from readiness + preflight to
max(readiness, preflight), plus final validation and preview cleanup. Measure
both branches, join latency, cleanup and billable runtime before claiming gains.
The benefit depends on which wait dominates; there is no guaranteed speedup.

Before release test both completion orders, stale health while preflight is slow,
either branch failing/timing out, disabled propagation, emergency hold, changed
identity/limits, duplicate execution, lease loss, restart, uncertain submission,
preview cleanup failure and terminal-generation behavior. Require no handoff or
ancestor deletion on failed joins, no executed preview, no duplicate stacks,
and preservation of the configured live ceiling. Run existing lifecycle checks
and a separately approved bounded sandbox trial. This commit changes design only.
