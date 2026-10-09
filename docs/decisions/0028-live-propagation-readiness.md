# 0028: Demonstrate continuation with real successor launches

Status: accepted by the operator on October 9, 2026; source implementation.
Activation requires a new immutable AMI, smoke validation, coordinated sandbox
release and passing release receipt. Live validation is a separate operation.

For inherited EC2 families, supersede the DryRun continuation requirement in
0021 and 0027. This applies to both inherited single-successor and binary modes.
Legacy non-inherited EC2 and CloudFormation protocols retain their existing gates.
Never mix readiness protocols or replace release pins during a live cycle.

## Two milestones

A candidate publishes DAEMON_READY after exact self/configuration validation and
fresh live controls. Its parent accepts that fresh identity-bound proof and
conditionally grants launch ownership, while preserving the parent. Candidate
readiness no longer claims that future launch authorization has been demonstrated.
No inherited-family continuation DryRun is issued; old local DryRun cache files
are not used as evidence for the new protocol. Terminal candidates publish the
explicit BOUNDARY proof and never attempt out-of-range launches.

Once an owner has actually submitted every expected child (one in single mode,
two in binary mode), it records a cycle/configuration/instance-bound PROPAGATION
receipt with LIVE_LAUNCH_PASSED and each exact path, instance ID and deterministic
client token. Publication requires matching settled SUBMISSION and RESOURCE
records, exact approved EC2 identity, ownership, fresh controls, absent STOP and
HOLD. A pending child is acceptable as evidence of an actual accepted launch;
it is not treated as healthy. Normal child functional readiness and ownership
acceptance remain separate gates before that owner's own retirement.

A predecessor may retire only after its successors are fresh, identity-validated,
accepted owners and each nonterminal successor has its own real-launch receipt.
Validate the receipt's full expected child set, settled submission/resource
records, and the children's current exact EC2 identities before retirement.
Conditionally match all those records in the retirement transaction, together
with fresh controls, STOP/HOLD and successor readiness/ownership. Terminal
successors use their explicit boundary instead of a propagation receipt.
No receipt, missing/aborted/unsettled submissions, missing or shutting-down child,
identity mismatch, stale readiness or conflicting evidence preserves the parent.
Unknown launch outcomes remain reconciliation-only; receipt retries do not
permit a new launch. Bounded continuation/readiness timeout invokes HOLD.

## Overlap and lineage

A successor must launch before its preserved predecessor retires to demonstrate
continuation. Single mode therefore allows N+1 to launch N+2 while N remains.
N+2 cannot extend to N+3 until its exact grandparent N is physically terminated.
This maintains at most three live generations, counting shutting-down instances;
single cycles deeper than one hop require max_live_generations=3. Own retirement
still requires the exact parent to be physically terminated, preserving the
parent's child-validation gate. Binary mode retains its approved independent
family behavior; it also cannot retire ahead of an unconfirmed parent.

Restart reconciliation reuses exact durable propagation receipts and launch
intents. No new launch is needed to recreate readiness evidence. Receipts are
removed by the existing operator-controlled, cycle-fenced normal cleanup path.
Observer displays daemon readiness separately from reported live propagation,
retains source first-readiness times, and can still display older DryRun trials.

For the completed 63-node tree, the new protocol would eliminate 31 continuation
DryRun requests while retaining 62 required child launches. This is a request-count
projection, not a measured performance or throttle-free guarantee. It introduces
additional exact evidence reads at proof publication and retirement. No IAM,
launch-template mutation, automated cleanup or global launch lock is introduced.
