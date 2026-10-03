# EC2 functional readiness: source review

This change builds on the polling work merged into main in PR #14. It implements
[decision 0021](decisions/0021-successor-functional-readiness.md), explicitly
approved by the operator. No AWS deployment, live benchmark, AMI build or control
update was performed. The released overlap image and pins remain deployed.

## Evidence and stop behavior

The exact successor publishes a nested functional_readiness map in its GEN STATE:
schema_version, producer_instance_id, handoff_token, control_sha256,
proved_at_epoch, continuation_generation and continuation_status. The outer STATE
binds cycle, generation, instance, numeric template version, artifact identity,
predecessor and token. The DynamoDB codec stores the proof as a typed map and
absent/failed proof as NULL; it never stringifies a Python dictionary.

Only an enabled candidate whose verified predecessor owns CURRENT executes the
probe. It verifies its own approved instance and Launch Template, capacity and
its next-hop DryRun using its own credentials. Boundary candidates omit capacity
admission and DryRun for the nonexistent next hop while still validating identity,
parent and fresh controls. Probes neither launch nor retire instances, acquire
leases nor modify CURRENT. Both before and after continuation they check current
controls; parent identity is reread before and after it. The parent independently
rechecks continuation before handoff, so a candidate proof cannot reserve capacity
or replace the parent's safety fences.

Readiness proof refresh is five seconds, freshness is 15 seconds from the start
of the checks, ownership polling is one second, and readiness polling still uses
CONTROL (new EC2 initialization selects one second; legacy initialization selects
two). Existing deployed CONTROL remains unchanged. Heartbeat telemetry retains its configured
cadence. Proof-only STATE updates leave heartbeat sequence/time unchanged. There
is no two-heartbeat or minimum-duration gate in this EC2 source implementation;
readiness_required_heartbeats remains in the control schema/fingerprint for
compatibility with legacy configuration but does not gate this EC2 protocol.
Missing, failed, mismatched, stale or future proof prevents handoff. Slow checks
exceeding the freshness budget must retry. A failed refresh publishes NULL to
invalidate prior readiness before retrying; an ambiguous failed write cannot prove
invalidation, so the existing proof can remain usable only within its bounded
freshness and the parent's final controls/continuation checks.

Handoff transaction checks the complete published candidate STATE and retains
conditional CURRENT, CONTROL, HOLD, lifecycle and lease fencing. CURRENT stores
the accepted proof. Retirement requires this durable successor evidence, exact
identity, matching approved controls and fresh enabled operator state. The proof
need not stay fresh forever after ownership transfer; durable retirement remains
retryable after a long outage. Stop/HOLD preserves running instances. Existing
submission intent and retirement reconciliation continue to prevent duplicate
creation, ambiguous relaunch and ownership advancement before older termination.

The operator accepted losing sustained-health evidence. Functional readiness
proves recent agent operations; it is not independent application health and does
not guarantee the agent will survive after handoff. EC2 running/status checks
cannot substitute for the proof. At max_generation the boundary remains terminal;
max_generation=10 and max_live_generations=3 are unchanged in deployed controls.

## Request and cost review

These are counts inferred from source, not metered AWS savings. DynamoDB rounds
reads per 4 KiB and writes per 1 KiB; transactional reads/writes use twice the
corresponding strongly consistent read/ordinary write units. See
[AWS request-unit definitions](https://aws.amazon.com/dynamodb/pricing/).
Prices depend on region, capacity mode and table class. No dollar forecast or
net cost reduction is claimed without live metering.

Compared with PR #14, a candidate observation replaces a three-item transactional
read plus a strongly consistent CURRENT read (seven RRUs for small items) with
one four-item transactional read (eight RRUs). This saves one serial API call but
adds one RRU per observation. At one observation/second that is 40 extra RRUs over
40 seconds of waiting, or 2,592,000 over an uninterrupted 30-day wait. Persistent
waiting is an incident/recovery case, not an expected bounded propagation cycle.

A successful nonboundary candidate proof adds two three-item control transactions
(12 RRUs), two exact CURRENT rereads and one CURRENT read inside capacity admission
(three more RRUs). It also adds approved-template/image lookups, exact self lookup,
capacity inventory/identity/quota/type/subnet calls and one EC2 DryRun. These calls
repeat at most every five seconds after successful probes while the candidate
waits. Failed probes may retry each second; pagination and duplicate processes
raise counts. Boundary proofs omit capacity admission and DryRun: 14 RRUs for the
two control transactions and two CURRENT rereads, before state publication.

Each publication is the existing lifecycle-fenced STATE write. With the default
five-second telemetry cadence, successful proof publication usually shares that
write. A slower heartbeat interval can add proof-only writes every five seconds;
a faster interval adds telemetry writes but reuses proof until refresh is due.
Budget the STATE item at its actual serialized size: a transactional item write
uses two WRUs per rounded KiB. The nested proof can cross a size boundary in both
STATE and CURRENT, and condition evaluation also consumes capacity. Retry and
handoff/lease transaction costs remain. Parent continuation rechecks are retained.

Removing the fixed observation wait may save readiness polls, lease renewals and
instance runtime, but the added candidate checks take time and incur requests.
The prior 5s-versus-6s fixture is historical polling evidence, not a prediction
for this protocol. The new deterministic fixture accepts an immediately available
valid proof without sleeping with either polling interval. End-to-end AWS
performance, cold ARM64 timing and net recurring cost are unmeasured. Review these
costs and observe bounded propagation before enabling a new release.

## Activation and recovery

Build the exact reviewed source archive and a new matching AMI, run the image
checks, then review the immutable artifact/image/template pins. Deploy only with
separate authorization and propagation disabled, after supported cleanup leaves
no live cycle. Old EC2 agents require two heartbeats and new agents require a
functional proof; live mixed-version migration is unsupported and fails closed.
Preserve the current AMI/pins until that review. IAM and workflow changes are not
part of this change. The existing approved agent role already has the read APIs
and DryRun permissions used here.

If readiness is missing, inspect GEN STATE, CURRENT lineage, proof timestamps,
control fingerprint and candidate logs. Fix access, identity, quota or control
ambiguity through the approved operator path; never inject a proof, clear intent
to relaunch, bypass HOLD or disable a gate. Stop normally by disabling propagation;
for an incident also establish HOLD. Existing supported cleanup remains the
recovery path. No live AWS validation was performed for this source change.

## Local verification

All 285 SDK-backed unit tests pass, including actual candidate-produced proof
consumed by the parent, candidate credential/DryRun failure, foreign/missing/stale/
future/malformed proof, stop/HOLD/control/parent races, freshness budget overrun,
failed-refresh invalidation, explicit terminal boundary, conditional-state fencing,
replay-safe creation and bounded retirement recovery. The codec and four-record
transaction have transport-level typed-request tests. Telemetry spacing remains
independent of proof-only publications. Existing legacy CloudFormation tests pass.
Repository safety checks, generated-bootstrap consistency, Black and diff checks
pass. CloudFormation lint reports only the four existing W2001 unused-parameter
warnings in import-retained.yaml. No live AWS test or cold AMI test was performed.
The separate simplified design document was unavailable; review uses repository
architecture decisions and the supplied first-pass invariants with decision 0021's
explicitly approved EC2 readiness exception.
