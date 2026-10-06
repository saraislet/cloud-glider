# Boot and API timing collection

The daemon emits schema-1 JSON diagnostic records locally. Timing is never a
readiness signal or input to controls, admission, ownership or retirement. Raw timing instrumentation makes no extra AWS calls. The optional durable
exporter below adds bounded CloudWatch writes using the existing log group and
permissions. A rebuilt, verified baked image and reviewed immutable pins
are required to activate this source change. Deploying or running a benchmark
requires separate operator authorization.

## Record boundaries

- `boot_timing` / `baked_image_verification`: manifest/hash verification duration,
  including failed verification. User data and the unit's ExecStartPre both run
  the verifier, so expect two records; their journals identify the context.
- `boot_timing` / `entrypoint_started`: earliest instrumented daemon entry point,
  before importing the daemon and boto3. Interpreter startup before this marker
  is not separately measured.
- `boot_timing` / `runtime_imports`, `ec2_runtime_imports`, `sdk_initialization`:
  import and gateway construction durations. Gateway initialization includes
  IMDSv2 lookups and persistent client creation, not individual IMDS timings.
- `phase_timing` / `startup_identity_validation`, `candidate_functional_probe`
  and existing lifecycle phases: aggregate daemon checks. The candidate probe
  ends before STATE publication; it does not prove that the parent accepted it.
- `api_timing`: one record per SDK invocation, including each pagination page,
  success, AWS error and transport failure. It records service, operation,
  duration, outcome, error code, DryRun flag and instance/generation/request
  correlation. No parameters, responses, credentials, exception messages or
  user data are copied into timing records. Expected `DryRunOperation` remains
  an `AWS_ERROR` at the SDK boundary; use the error code and lifecycle result to
  identify a passing continuation check. Request serialization, credentials,
  transport and service latency are combined. Streaming response body reads
  after the invocation returns are excluded.

Durations use a monotonic clock. UTC timestamps indicate record completion;
existing phase records additionally retain phase start. `boot_elapsed_seconds`
is Linux `/proc/uptime`, or null if unavailable. It gives local boot-relative
milestones, not EC2 launch-relative durations. Do not subtract uptime across
instances. Repeated entry points can indicate a service restart.

## Capture before retirement

Local journals disappear with instance deletion. The durable collector below
exports the timing records to the existing operations log group. CloudTrail does
not contain internal durations. Require complete receipts for every independently
identified instance after termination; missing receipts mean failed collection.
Do not add a pause or weaken retirement gates to collect logs.

Kernel/systemd and cloud-init logs are needed
to separate pre-daemon boot delays: uptime alone cannot attribute time to network
readiness, cloud-init or disk reads. Do not sum overlapping systemd unit times.
Retain raw logs privately under the audit logging contract. Timing IDs are for
private correlation; sanitize public summaries.

Compare repeated runs with identical instance type, AZ, image and observer
cadence. Record launch → verifier → entry point → first completed candidate
probe → published STATE → accepted handoff separately. Report API count and
latency by service/operation, expected DryRuns, errors and retries, plus sample
count and median/range. Use retained launch/handoff evidence for whole-hop time;
account for cross-host clock skew and observer sampling delay. Instrumentation
adds local log I/O; quantify its overhead during the next bounded benchmark.

## Durable collection

The revised baked runtime enables bounded background collection into the existing
`daemon_operations_log_group`. The daemon enqueues only allowlisted timing fields;
no request parameters, response bodies, exception messages or secrets are sent.
A dedicated persistent CloudWatch Logs client runs off the measured API path,
with one SDK attempt and 0.5-second connection/read timeouts. It is not instrumented.
Batches contain at most 32 records and are assembled for up to 0.2 seconds. Queued
messages are bounded to 2 MiB/2048 records; an in-flight batch adds at most 512 KiB,
and pre-client import records are bounded to 128 records. Queue overflow and
upload failures are diagnostic losses, never lifecycle failures or retries.

User-data and ExecStartPre verifier records use a bounded 64 KiB spool per boot.
The runtime imports user-data verification and only the current systemd invocation's
pre-start verification. A system-interpreter failed-start helper is externally
bounded to three seconds; it attempts export if verification or the runtime entry
interpreter fails before normal collection starts. Missing SDK/library files or
credentials can still prevent diagnostics: absence of a receipt must be treated
as failed collection, never as an empty successful capture. The helper runs only
on startup failure; it cannot clear failed verification or run the lifecycle.

Every collector producer uses stable boot/cycle/generation/instance/source
correlation, unique sequence IDs, and a terminal count/SHA-256 receipt. Read-only
validation deduplicates identical records and rejects missing sequences, conflicting
duplicates, corrupt messages, mismatched identity, loss counters or absent markers.
Successful boot validation also requires both verifier contexts and all early
entry/import/SDK phases. Validate records retrieved after instance termination.

A drain is limited to 1.5 seconds after `run()` returns. It is not a handoff gate,
and can race an already authorized successor retirement. No receipt means incomplete
collection. For the operator-approved best-effort diagnostic run, prove that
actual timings reach CloudWatch before starting, then measure post-termination
coverage and loss rather than requiring complete receipts for every generation. The read-only executable `--timing-smoke` path
initializes actual imports/SDK/identity and describes only its own instance; it
cannot read/write lifecycle state, launch successors or invoke emergency hold.

Collection has CPU, allocation, network and potential post-run exit overhead.
Measure enabled/disabled initialization, enqueue latency, CPU, and exit/retirement
impact separately. Never claim zero overhead or infer boot speedup from a single
instrumented run. Failed delivery cannot delay lifecycle operations; a bounded
shutdown wait remains visible in measurements.

The failed-start helper imports an independently installed stdlib exporter, not
the failed daemon package or gateway. It still depends on the AMI-installed SDK
libraries for transport; SDK corruption can prevent delivery. Runtime-start
markers are scoped to the current systemd invocation, so an earlier crashed
producer cannot suppress later startup-failure diagnostics. The collector
initialization and client-initialization durations are included explicitly.
The local `collector_shutdown` record supplies drain and process CPU timings
for isolated paired smoke measurements; it is emitted after sealing and is not
a durable timing receipt.

## Initial overhead measurement

Three alternating enabled/disabled synthetic runs on the operator workstation
(Python 3.14, 2000 records each, immediate fake transport) measured median enqueue
latency of 4.96–5.38 microseconds and p99 of 8.42–10.17 microseconds with collection
enabled. Process CPU was 10.65–12.36 milliseconds per 2000 records, including the
background fake-sink worker. These are local bounds for the tested workload, not
measurements of AMI startup, AWS networking, or retirement. Real paired smoke
results and live sample coverage must accompany any deployment/run conclusions.

The operator accepts partial diagnostic collection for the initial live rerun.
Report each phase's observed sample count and instance coverage beside its mean
and median. Never replace a missing duration with zero or present partial captures
as complete. Lost final receipts can obscure exact losses and bias averages toward
instances that survived long enough to upload; retain available sequence gaps,
completion counters and upload errors as evidence. Lifecycle safety requirements
are unchanged by acceptance of diagnostic loss.
