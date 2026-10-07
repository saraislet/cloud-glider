# 0023: Independent EC2 families with inherited configuration

Status: source implementation approved by Anna Sarai on 2026-10-06; not deployed.
The first authorized live trial remains generations 0–2. No ten-generation AWS
test is authorized by this implementation request.

## Scope and explicit invariant changes

For newly initialized inherited EC2 cycles, bootstrap reads approved initial
settings once and pins them to BOOTSTRAP/REQUEST. A canonical SHA-256 envelope is
provided to the seed through reviewed CloudFormation user data. Parents pass that
envelope and static bootstrap inputs to children through the approved numeric
launch template plus fixed-source user data. The digest is an integrity check,
not a signature; launch privileges and the cycle's pinned digest provide the
trust boundary. Static user-data-only decision 0019 is explicitly revised for
this path. The runtime cannot mutate the launch template or IAM.

`configuration_inheritance=true` selects lifecycle version 3. Within it,
`binary_fanout_enabled=false` creates one child and `true` creates two. The mode,
depth, artifact pins and timing settings cannot change during a cycle. Existing
cycles without the envelope keep their current implementation. CloudFormation
propagation is not converted; both successor options here use direct EC2.

Node identity is cycle plus lineage (`r`, `r0`, `r1`, ...), and generation is
zero-based depth. Parents compute child names, generations and deterministic
tokens locally. No global CURRENT owner, live-instance ceiling, launch counter,
rate gate, or concurrency permit is used by binary families. `max_generation=2`
gives seven nodes; the supported configuration range extends through 9 for future
reviewed tests. Depth, one durable intent per child, and deterministic client
tokens bound expansion. Single-successor mode permits the next launch after exact
predecessor shutting-down/terminated observation, without a global inventory count.
Own retirement still waits for confirmed predecessor termination. This preserves
the three-instance chain bound through local retirement ordering and allows overlap.
The old
CONTROL max_live_generations attribute is legacy metadata for inherited cycles.

## Controls, independence and performance

Each agent makes a local stop check and maintains at most one asynchronous DDB
control read. It reads dynamic operator/lifecycle/HOLD and self/parent STOP
records, but never derives runtime settings from those reads. The initial
operator-enabled state comes from bootstrap. The inherited polling default is
two seconds and maximum observation age is fifteen seconds, measured from read
submission. Failure or delay does not extend freshness. Expiry latches stop;
agents cannot resume that cycle. Stop is persisted locally and in cycle-fenced
family records and forwarded to children, including not-yet-booted children.
Parents that have retired are not required to forward future commands: surviving
descendants also monitor global controls. Accepted operations are reconciled,
not cancelled or automatically cleaned up.

This explicitly replaces the fresh synchronous prelaunch control-read rule for
the inherited path. Multiple descendants may be submitted before stop arrives.
There is no claimed bound of one accidental launch and no global throughput
guarantee. Configuration changes affect the next cycle. START does not resume a
stopped inherited cycle; inspect and clean up before preparing another one.

Independence does not eliminate DDB coordination. Each actual child launch first
persists a per-child submission intent, fenced by cycle, configuration digest,
parent ownership and cleanup state. It does not check global stop/HOLD or obtain
a shared permit. Child intent writes and family ownership/readiness reads remain
synchronous correctness operations. The two child launches can overlap.
Ambiguous outcomes consume their intents indefinitely until exact reconciliation;
they are never automatically resubmitted. Reads/writes unrelated to the same
family do not contend for a global propagation owner or mutable counter.

## Readiness, ownership and retirement

The running successor verifies its own exact EC2/template/image/configuration
identity and executes a continuation authorization dry run for its next children.
Terminal leaves use the inherited boundary instead. It publishes a proof with
exact cycle, lineage, instance, predecessor, handoff token, configuration digest,
generation and timestamp. Functional proof refresh is five seconds and freshness
is fifteen seconds, preserving decision 0021's contract. EC2 status alarms remain
corroborating telemetry, named by unique node path for successors.

A parent verifies that exact child instance and full proof snapshot and transfers
ownership conditionally. Fresh operator/HOLD/cancellation checks remain atomic
at handoff and retirement. A binary child can propagate after its own handoff
without waiting for its sibling. An interior parent retires itself only after
both child handoffs and fresh validation. Retirement intent is durable and is
revalidated on retry. A node cannot retire ahead of its own parent's confirmed
termination; this keeps its parent's retirement proof available while allowing
descendant launches to overlap. Missing exact EC2 observations preserve instances.

Leaves retain live-agent readiness and a durable LEAF state for inspection;
explicit operator cleanup terminates them. No automatic leaf cleanup is added.

## Cleanup and release

GEN#<lineage> records replace generation-number-only records for families. They
contain NODE, STATE, SUBMISSION, STOP and exact RESOURCE inventory. CURRENT remains
UNINITIALIZED in this lifecycle; it is not a tree inventory or authoritative
owner. Bootstrap's existing CloudFormation seed stack remains in inventory.

Cleanup fences new intents, waits for every submission to settle, and uses pinned
cycle controls instead of subsequently edited deployment pins. Accepted child
launches can finish inventory/alarm recording during QUIESCING. These bookkeeping
writes are rejected after cleanup enters DELETING/VERIFYING, preventing state
recreation during reset. An unresolved intent requires inspection after the
existing bounded wait; cleanup does not infer absence or issue another launch.
Verified instance, volume, alarm and seed-stack cleanup uses the existing operator
path. Old configuration pins are removed on reset.

The full simplified design document is not present in this checkout; production
reconciliation with that document and live authorization remain release checks.
See [the deployment runbook](../independent-ec2-families.md). No AWS resources,
permissions, Organizations policies, workflows or deployment roles were changed.
