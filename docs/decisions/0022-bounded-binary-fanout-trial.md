# 0022: Three-level binary fan-out trial

Status: historical offline model. The live-agent source design is superseded by
[decision 0023](0023-independent-inherited-ec2-families.md); deployment is outstanding.

The operator requested two children per propagation node and chose three
generations before considering a larger test. After clarification, the operator
approved three levels (0, 1, 2), seven total nodes, and subsequently requested
removal of the live-instance ceiling. The offline binary model has no separate
live-instance limit; tree depth and branching bound the total to seven nodes.
No ten-generation trial is authorized. The existing live chain's ceiling is
unchanged; removing it from a future binary runtime requires that runtime's
explicitly selected lifecycle and updated safety contract.

The existing generation convention is zero-based: max_generation is the highest
generation number, not a count. The sequential runtime uses that number as a
unique identity. In the binary trial, generation is tree depth; node identity is
the cycle plus lineage path (`r`, `r0`, `r1`, and their children). Generation alone
cannot identify a sibling. Existing runtime records and cycles must not be
interpreted using the new convention.

The model expands nodes in breadth-first order before retirement, so all seven
nodes can coexist. A parent remains live until both child subtrees have durable
healthy completion evidence and confirmed termination. Sibling launches no
longer wait for earlier subtrees to terminate. Each nonterminal node produces
exactly two children; depth-2 leaves produce none. The model executes one step
at a time and does not simulate parallel AWS request timing.

This proposes a distinct tree lifecycle rather than single-successor ownership
transfer. An interior node completes conditionally only after its two subtree
receipts. A leaf completes conditionally after its own identity and functional
readiness validation. Completion survives termination. Missing or ambiguous
health, failed completion, stale controls, and unconfirmed termination preserve
ancestors. Stop/HOLD blocks launches, completion, and retirement submissions;
accepted termination may finish and be observed. Reservations and unknown launch
outcomes consume slots; retries reconcile the exact cycle and token without
blind resubmission. This model assumes serial conditional steps, not distributed
atomicity or tested AWS permissions.

The reviewed model represents readiness as explicit cycle/lineage/token/instance,
artifact, ownership, revision, timestamp, workload and continuation evidence.
Completion compares the expected snapshot with current evidence and preserves a
historical receipt. Retirement validates fresh evidence again. Every checkpoint
is fenced by each node's cycle and deterministic token. The fake producer supplies
this evidence; model validation does not establish real workload execution.

The offline experiment is intentionally isolated from the agent entry point and
release tarball. Decision 0004's deployed single-owner contract remains in force.
Before a live release, implement and review cycle-fenced node state, atomic
submission reservation and completion, exact instance/artifact proof, bounded
freshness, successor execution, parent/leaf retirement ownership, cleanup, and
operator integration. Use an explicitly selected offline-initialized cycle;
never switch a live chain. Update the safety contract and runbooks alongside
that implementation. IAM/deployment permission changes need separate assignment.

The model is an initial protocol test, not evidence that EC2 fan-out works.
The simplified Google design document is referenced by decision 0004 but its full
contents are not included in this checkout; production reconciliation with it
remains necessary.
