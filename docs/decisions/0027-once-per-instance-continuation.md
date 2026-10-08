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
