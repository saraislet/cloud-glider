# EC2 polling evaluation (October 3, 2026)

Source-only evaluation of propagation recommendations 2–4, based on the
retirement-overlap release at `5596457`. Decisions 0019 and 0020 supersede the
legacy CloudFormation-only lifecycle rules for explicitly selected EC2 cycles.
No architectural exception, backend switch, deployment, IAM or infrastructure
change is introduced here. The supplied simplified-first-pass invariants remain
binding; its separate Google design document was not available in this checkout.

The later [functional-readiness source change](ec2-functional-readiness.md)
supersedes the two-heartbeat gate and separate CURRENT read described below.
These local measurements describe PR #14 before that subsequent design change;
its unit-count estimates must not be reused as a forecast for the new protocol.

## Changes and local evidence

EC2 daemons observe ownership every second instead of coupling observations to
heartbeat spacing. Each observation still reads transactional control/lifecycle/
HOLD and exact CURRENT. Heartbeats use monotonic elapsed time and publish at
most once per configured heartbeat interval (five seconds). Lease acquisition,
fresh controls, readiness, continuation DryRun, conditional handoff and confirmed
predecessor termination retain their gates. Stopped owners retain their existing
60-second backoff. Deferred active attempts now retry at one second too; this
can increase requests under persistent failure and must be monitored.

Readiness already supports a CONTROL `readiness_poll_seconds` value of 1.
The operator subsequently requested activation of one-second readiness. New EC2
CONTROL initialization now selects `ec2_readiness_poll_seconds=1`; the legacy
CloudFormation default remains 2. Existing DynamoDB CONTROL records are not
overwritten by initialization, and no live AWS setting was changed.
The EC2 failure-path suite now uses one-second readiness polling. A deterministic
healthy fixture becomes eligible after 5 seconds with one-second polling versus
6 seconds with two-second polling, and requires fresh controls and lease renewal
on each observation. Repeated sequences and heartbeats less than five seconds
apart cannot satisfy the existing two-heartbeat gate. Ownership polling tests
observe six controls but only two heartbeats over seconds 0–5, and verify that
stop/HOLD on an ownership transition between heartbeats blocks mutation.

Capacity checks batch exact CURRENT and unfinished predecessor identities into
one DescribeInstances request. Returned identities must match the complete
requested set exactly, without duplicates. Missing/extra/duplicate results fail
closed. Filtered inventory, shutting-down slots, confirmed retirement exclusions,
account-wide vCPU checks and subnet headroom remain in place. This saves one
serial API request when both identities need checking; network-time savings are
unmeasured. Filtered or missing identities never establish termination.

`python scripts/measure_image_verification.py --samples 30` builds the current
archive and reproduces the baked manifest's archive, extracted files, service,
verifier and smoke-test file set in a temporary directory. On this developer
machine using Python 3.14, 11 files / 159,950 bytes took median 0.03188 seconds
per pass (range 0.02990–0.03435), including interpreter startup and imports.
Two passes imply about 0.06376 seconds; removing one would save about 0.03188
seconds in this warm local fixture. This is not cold ARM64 EC2 boot evidence.
Both user-data verification and service ExecStartPre remain unchanged; every
service start/restart retains fail-closed verification. Consolidation is not
warranted by this evidence.

The prior ~2 seconds/hop ownership and ~0.5 seconds/hop readiness estimates
assume uniformly distributed observation phase. They remain hypotheses, not
measured AWS savings. The local readiness comparison demonstrates quantization
only; it does not predict end-to-end speedup. No live benchmark was run.

## Request cost and deployment review

For items no larger than 4 KiB, one candidate observation uses six transactional
read request units (CONTROL, HOLD, BOOTSTRAP) plus one strongly consistent unit
for CURRENT. Moving from five seconds to one adds approximately 0.8 observations
per second, or 5.6 RRUs/second of candidate waiting. Heartbeat writes do not
increase. Larger items, retries and duplicate processes increase this estimate.

Moving readiness from two seconds to one adds about 0.5 observations/second:
six transactional control RRUs, one generation-state RRU and a fenced lease
transaction per extra observation, plus one exact EC2 lookup. Budget
conservatively eight RRUs and four WRUs per added readiness observation,
including transaction condition evaluation; this is a planning allowance, not
a metered bill. Lease renewal is a write and must be included in review.

At illustrative DynamoDB Standard on-demand rates of $0.125/million RRUs and
$0.625/million WRUs, 40 seconds of candidate waiting and 30 seconds of readiness
per hop add at most approximately 344 RRUs and 60 WRUs: $0.0000805/hop, or
$0.0007245 for nine hops. A continuously waiting candidate alone adds about
$1.8144 per 30-day month; this is not expected bounded-cycle usage. EC2 API
throttling/latency and persistent retries need observation even where API calls
have no separate request charge. No instance, storage, IAM or recurring
infrastructure configuration is changed.

Rates are illustrative: verify us-west-2 table class, billing mode, actual item
sizes, retries and regional rates during deployment review. Sources checked
October 3, 2026: [AWS pricing](https://aws.amazon.com/dynamodb/pricing/),
[request-unit sizes](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Constraints.html).

Before deployment, explicitly review this request-cost increase, bake the changed
daemon into a matching verified image, review immutable artifact/template pins,
and apply the operator-approved EC2 readiness polling value of 1. Keep heartbeat interval 5,
required heartbeats 2 and all readiness/continuation/retirement gates. Any future
bounded benchmark needs separate operator authorization and comparable trials
with request counts and all safety gates recorded. This work authorizes none.

## Validation receipt

All 269 local unittest cases passed with the pinned SDK dependencies available,
including stop/HOLD, duplicate execution, failed handoff, ambiguous health,
retirement/restart recovery and SDK request validation. Repository safety checks,
Black checks on changed Python files and `git diff --check` passed.
CloudFormation lint reported only the four existing unused-parameter W2001
warnings in `cfn/import-retained.yaml`; excluding W2001, all templates passed.
No CloudFormation templates were changed. Live AWS authorization, ARM64 timing,
actual request metering and propagation speed remain untested by this task.
