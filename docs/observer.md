# Local Cloud Glider Observer

The observer preserves the interface and simulation from Sites project
`appgprj_6abefa3d28a08191aa30dfb37bcc2ea4`, version 1, source commit
`f8527659f5282f0dd5e9bbaf227fb994121f0b75`. HTML, CSS, and the final JavaScript
were recovered from the original Codex workstream's build commands. The local
adaptation is under `observer/static`; no hosted deployment was changed.
The original CSS typo corrected before hosting is also corrected locally.

```sh
python3 -m venv .venv
.venv/bin/pip install -r observer/requirements.txt
.venv/bin/python -m observer.server --profile default --region us-west-2
```

Open http://127.0.0.1:8000. Ctrl-C stops both serving and collection. No agent,
bootstrap, lifecycle, EC2, IAM, or CloudFormation mutation API is used. Credentials
stay in Boto3's existing profile provider chain. No credentials are stored in
SQLite or supplied to the browser. Only loopback is bound; Host and Origin are
checked. Use the standard port 8000. This is a local tool for a trusted computer,
not a multiuser service.

The interface opens in Live mode, but AWS collection starts stopped. Use Start
observer to explicitly enable reads and Stop observer to pause them. Stop waits
for the current read pass to finish before confirming; all browser tabs share
this backend setting. Disconnect only disconnects the browser feed. Simulation retains the original synthetic binary
fan-out; the actual Glider system is a bounded chain. Replay → Load SQLite
recording loads collected instance observations; JSON/JSONL import/export and
seek, speed, hover, focus, and pinned ancestry remain available. Once started, collection continues while the browser is in simulation or replay
until Stop observer is clicked. Close the Python process
to stop AWS reads. Scans incur existing DynamoDB read charges; no infrastructure
or recurring cloud resources are created.

## Data and fidelity

Both tables are paginated and consistently scanned initially. Existing Streams
are consumed through DescribeStream, GetShardIterator, and GetRecords, with
parent-before-child discovery, iterator renewal, per-stream/per-shard sequence
checkpoints, and event-ID deduplication. SQLite archives full scan snapshots, and commits raw stream records and checkpoints
in one transaction. Pre-snapshot stream events are archived without regressing
the initial view. SSE sends a current atomic snapshot/cursor on connection and
reconnection, then observations and keepalives. Reconnection replaces browser
state. A restart reuses local records and checkpoints and reconciles AWS state.

Read access needed: DynamoDB DescribeTable and Scan on the two selected tables;
DescribeStream, GetShardIterator and GetRecords on their enabled streams.
Stream API IAM actions use the `dynamodb:` prefix. The observer never grants these.

Both sandbox tables now have NEW_AND_OLD_IMAGES streams, enabled through the
owning foundation stack. The default profile has narrowly scoped stream read
access. The UI reports each table's actual collection mode. Tables with usable
streams reconcile every 30 seconds; denied or disabled streams fall back to
5-second snapshots. Stream discovery retries every 30 seconds.

Snapshot polling can miss intermediate updates or entire short-lived records.
Streams have finite retention; trimmed checkpoints produce a visible gap warning
and restart from retained records. SQLite cannot recover events never collected.
The raw_events table includes retained pre-snapshot stream records; the replay UI
uses events, the normalized observations made during collection. There is no
invented reconstruction of old benchmark runs from summary timings.

CloudFormation completion, EC2 running, and workload heartbeats do not establish
readiness. CURRENT ownership is displayed as accepted readiness; candidate
functional-readiness proof is reported telemetry, not a replacement for agent
validation. Missing candidate proof displays Booting. Retirement-completed
CURRENT evidence establishes predecessor termination. A deleted record alone
shows Waiting, physical state unknown. HOLD/ACTIVE takes precedence. Controls
come from CONTROL/GLOBAL, BOOTSTRAP/REQUEST, and HOLD/ACTIVE. Request ID plus
instance ID fence lineage across cycles; generation alone is never an identity.

The browser's lifecycle/readiness intervals and lifetime use observation times
unless timestamps were present in an imported log. An initial snapshot cannot
establish original launch time. Ownership and source update timestamps are
preserved separately; heartbeat age uses the recorded heartbeat clock. These
metrics are not the isolated API phase durations discussed in
[performance-log.md](performance-log.md). Stale heartbeats retain their dashed
outline. The interface makes no lifecycle decisions.

Local `.observer/events.sqlite3` (WAL) is ignored by Git and contains private AWS
state. Protect it like an operational log. History grows with collection; stop
collection and archive the database when no longer needed. Only one collector
should use a database at a time. No system startup service is installed.

Validation (the HTTP/SSE tests bind temporary loopback ports):

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_observer*.py' -v
node tests/test_observer_ui.cjs
.venv/bin/python scripts/validate_repository.py
```

GenerationTable StreamSpecification is the only foundation-template change.
Live handoffs, shard rollover, and collection during a real propagation run
remain untested; the operator has deferred propagation until later.

## Local test results — October 3, 2026

The test pass exercised paginated scans, SQLite integrity/persistence, delayed
duplicate protection, transaction rollback, cycle fencing, stop/hold/ambiguous
health, ERROR precedence, parent/child shard order, trimmed checkpoints, expired
iterators, denied stream access, and preservation of lifetime/history across SSE
reconnection. Temporary HTTP fixtures tested incremental SSE delivery, reconnect
snapshots, 1,005-event replay pagination, invalid cursors, Host/Origin rejection,
and missing routes. These fixture tests perform no AWS writes or propagation.

The real browser imported a 12-event JSONL fixture, sought forward and backward,
and verified pinned ancestors, 13-second creation-to-readiness, a 20-second stale
heartbeat, two 22-second ancestor intervals and 44-second lineage elapsed time.
The browser disconnect badge now remains disconnected while collection continues.
The deterministic UI check covers simulation's 255 nodes/128 active frontier,
JSONL import, duplicate events, ancestry, and feed state/snapshot replacement.
Live stream discovery/read access is separately verified below. Mock stream
tests do not establish live propagation correctness.

## Observer stream permission supplement

[iam/observer-stream-read.json](../iam/observer-stream-read.json) contains a
narrow operator permission supplement for the observer's existing IAM identity.
Replace placeholder account `123456789012` with the selected sandbox account.
It allows only DescribeStream, GetShardIterator, and GetRecords on the two
sandbox table streams in us-west-2; it does not enable Streams, grant table
writes, create access keys, or change any agent role. An IAM administrator must
apply the supplement to the existing observer identity.

The operator explicitly approved the recipient/action/resource scope on October 3.
The approved application then failed with AWS AccessDenied: the default
GliderManager identity lacks iam:PutUserPolicy on itself. No live IAM changes
were made. An existing IAM administrator identity is required to apply the policy,
after which actual stream reads must be verified. The generation table still requires a separate operator decision to
enable Streams; this IAM supplement alone does not change its configuration.

## Permission fix verified — October 3, 2026

Using the operator's Chrome IAM session, created customer-managed policy
`CloudGliderObserverStreamRead` and attached it only to `GliderManager`, after
explicit browser confirmation. The inline approach was rejected by the user's
aggregate inline-policy size limit, so existing policies were preserved.
The policy matches `iam/observer-stream-read.json` with the sandbox account
substituted. Actual DescribeStream, GetShardIterator, and GetRecords calls using
the existing default profile succeeded. The earlier access-denied account above
is historical; state-table stream reads are now authorized. Generation-table
Streams remain disabled and that table continues snapshot polling. No table
configuration, runtime role, or propagation controls were changed.

## Generation stream deployment — October 3, 2026

Decision 0022 enables generation-table Streams through CloudFormation. The first
update rolled back because the foundation service role lacked UpdateTable.
After explicit operator approval, inline policy
`cloud-glider-foundation-observer-stream` grants only `dynamodb:UpdateTable` on
the sandbox generation table. Its existing boundary is unchanged. The source
supplement is `iam/foundation-observer-stream-supplement.json`; substitute the
sandbox account before applying. UpdateTable also permits other table settings;
this is a deployment-role capability, never an observer/runtime-agent capability.

The retry change set contains only GenerationTable StreamSpecification, with no
replacement. Both streams retain NEW_AND_OLD_IMAGES. Snapshots remain the
bootstrap/reconciliation and retention-gap recovery path; DynamoDB stream history
expires after 24 hours. Cost savings have not been measured. The generation table
is empty: real generation events cannot be validated without an independently
authorized propagation run. Fixture tests cover INSERT/MODIFY/REMOVE ingestion,
deduplication, checkpoint restart, SSE and SQLite replay without AWS data writes.

Deployment validation: foundation stack UPDATE_COMPLETE; both tables report
StreamEnabled true and NEW_AND_OLD_IMAGES. Actual DescribeStream,
GetShardIterator and GetRecords succeed with the default profile. Local status
and browser show both tables as stream + reconciliation without warnings.
SQLite persists four empty generation shard checkpoints (NULL sequence, open);
there are no generation records or normalized replay events yet. Empty-checkpoint
restart, INSERT/MODIFY/REMOVE history and replay are covered by fixture tests.
All 309 Python tests, deterministic UI checks, cfn-lint and repository safety
validation pass. Propagation remains disabled, CURRENT UNINITIALIZED.

## Live retirement projection correction

Run 9 exposed retained CANDIDATE/readiness records being displayed as active after
CURRENT advanced. Confirmed termination now remains terminal for the exact
cycle/instance identity. Read-only EC2 DescribeInstances reconciles known instance
IDs every five seconds, in batches of 100, and records physical state in SQLite.
Running is telemetry only and never establishes readiness. Terminated overrides
retained readiness; shutting-down displays Draining. The existing profile must
allow ec2:DescribeInstances. This adds API reads, no lifecycle mutations.
Correction observation times cannot reconstruct missed historical termination
times; lifetime remains an observation metric. Regression tests cover retained
readiness after termination and CURRENT advancing to another predecessor.

A compact run selector lists history newest first. Only the selected run is
displayed, with explicit generation labels and state text; rows wrap within the
panel instead of adding horizontal width. Summary counts refer to that run. Each completed run freezes its observation duration at its last confirmed
termination; prior run history remains available. The lifecycle controller, not
the read-only Observer, performs automatic terminal cleanup (decision 0023).
