# 0027: Once-per-instance continuation authorization

Status: Operator approved, 2026-10-08; source implementation, awaiting AMI release.

Anna Sarai requested that continuation dry runs run no more than once per
instance. This supersedes repeated continuation authorization within decision
0023's five-second readiness refresh; it does not change readiness freshness,
identity validation, control checks, ownership transfer or retirement gates.

Each nonterminal inherited-family instance executes one successful RunInstances
DryRun against the first child's pinned launch configuration. Siblings share the
launch template, image, network, instance profile and IAM constraints. Their
individual lineage, tags and deterministic tokens remain validated at actual
launch. Terminal leaves perform no dry run.

A successful result is retained in an atomic local receipt under
/var/lib/cloud-glider, bound to the complete configuration and instance identity.
A daemon restart verifies its own identity before using that receipt. Missing
receipts require authorization; malformed or conflicting receipts fail closed.
Configuration changes invalidate the proof and require inspection, not reuse.
The receipt is not a current assertion about mutable IAM: actual launches still
undergo AWS authorization and all existing launch failure handling.

Readiness heartbeats continue every five seconds, without additional dry runs.
Throttled or inconclusive attempts do not establish proof or publish readiness;
existing jittered retry backoff applies, bounded by readiness_timeout_seconds
for that daemon invocation. Stop/HOLD checks remain active during retries and
before readiness publication. Timeout preserves the parent and invokes HOLD.

The six-generation local simulation must produce 63 nodes and 31 successful dry
runs, one per interior instance. This is local validation, not a live benchmark.
Release requires a new immutable AMI and the complete sandbox release procedure
in docs/ami-build.md before any live evaluation.

## Retirement observation correction

The October 8 trial also exposed a shutdown observation race: EC2 can detach
network and profile fields while a parent is shutting-down. An exact parent
with the cycle/configuration/owner/children-matched RETIRING receipt may omit
those three fields during shutdown. All remaining identity fields and any
present conflicting network/profile values remain strict. A running parent
retains full validation. Only observed terminated state records termination
confirmation; shutting-down does not authorize descendant retirement.
Identity-validation failures emit structured state and mismatched field
names into private timing evidence before the normal HOLD path.

Diagnostic uploads now retry a batch at most three times in the independent
export worker, retaining record IDs for deduplication. Shutdown drain is bounded
to three seconds. Exhausted or rejected uploads still report incomplete evidence.

## Rejected launch receipt durability

The October 9 trial exposed a definitive RunInstances throttle rejection whose
receipt transaction was canceled while siblings and control readers overlapped.
Without that receipt the safe retry path must treat the submission as ambiguous.
Retry only the same cycle/token-fenced rejection receipt, at most eight attempts
with bounded jitter. Do not repeat RunInstances during receipt retries. If the
receipt cannot be recorded, invoke HOLD with LAUNCH_REJECTION_RECEIPT_FAILED,
preserve the parent, and require exact submission reconciliation before cleanup.
The existing safe launch retry still consumes the durable rejection receipt
before submitting the deterministic token again. No identity or cleanup gate is
relaxed, and unknown launch outcomes remain reconciliation-only.

## Shutdown timing drain

A retiring parent in the October 9 trial lacked its timing completion marker.
The default SIGTERM action could terminate Python during EC2 shutdown before the
entrypoint's finally block drained diagnostic records. Handle SIGTERM by exiting
through those existing finally blocks: close the control monitor and SDK gateway,
then run the existing three-second bounded exporter drain. Ignore repeated SIGTERM
while unwinding so it cannot interrupt that drain. This does not publish STOP to
accepted descendants or change retirement gates. Failed uploads, drain timeout,
SIGKILL, and lost completion markers remain explicit incomplete evidence; the
handler is not a guarantee against every transport or host failure.
If shutdown interrupts an SDK call before it returns, API timing records report
INTERRUPTED with the interruption type. They preserve the exception and never
infer API success, resource absence, or permission to replay the request.
