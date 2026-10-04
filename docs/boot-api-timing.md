# Boot and API timing collection

The daemon emits schema-1 JSON diagnostic records locally. Timing is never a
readiness signal or input to controls, admission, ownership or retirement. No
additional AWS calls, remote log destinations, IAM permissions or resource
changes are needed. A rebuilt, verified baked image and reviewed immutable pins
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

These records remain in local journals and disappear with instance deletion.
CloudTrail does not contain these internal durations. The next supervised run
must have an authorized observer capture every generation's journal while it
is alive, including candidate records before handoff. Do not add a pause or
weaken retirement gates to collect logs. If the observer cannot capture them,
report the corresponding phases as **not measured**.

On each instance, using an already approved access path:

```sh
journalctl -u cloud-glider.service -b -o cat --no-pager
journalctl -u cloud-final.service -b -o cat --no-pager
systemd-analyze time
systemd-analyze critical-chain cloud-glider.service
```

The service journal contains ExecStartPre and daemon records; cloud-final
contains the user-data verifier. Kernel/systemd and cloud-init logs are needed
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
