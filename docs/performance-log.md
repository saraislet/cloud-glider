# Generation and propagation timing log

Public summaries of generation timing. Keep dated entries in reverse
chronological order, with newest run results and corrections first;
keep raw operational evidence private. New release receipts are retained in
ignored local storage under `.artifacts/release-receipts/`. Use UTC timestamps
and seconds, and write `not measured` for missing durations.

## 2026-10-04 UTC — Functional readiness: ten generations passed

The functional release completed generations 0–9 and nine conditional handoffs
using a verified private baked image built from merged PR #15 source `cd28e26`.
All nine successor handoffs retained accepted functional readiness proof. No HOLD occurred; the sampled live peak was three,
including shutting-down instances. Generation 9 confirmed predecessor retirement.

| Measurement | Earlier overlap | Polling | Functional readiness |
| --- | ---: | ---: | ---: |
| Seed creation → initial ownership | 36.192s | 35.497s | 31.136s |
| Mean ownership interval, nine intervals | 38.777s | 34.168s | 31.012s |
| Seed creation → generation 9 ownership | 385.187s | 343.007s | 310.247s |
| Sampled live-instance peak | 3 | 3 | 3 |

The functional trial took 9.55% less total time than
the polling trial and 19.46% less than the overlap trial.
These are single trials with different immutable images and EC2 startup variance;
they do not isolate causality. Functional proof includes identity, control,
ownership and continuation checks. Its proof timestamp marks check start, not
completion. First sampled publication has different semantics from a polling
heartbeat; individual SDK/DryRun durations remain not measured.

An earlier functional trial was interrupted by an observer file-write permission
error. Its supported stop took effect at generation 8; all ten instances and disks were
verified cleaned before the completed functional trial. An earlier conclusively
rejected start during cleanup launched nothing. Both events, build, deployment
and cleanup are excluded from seed-to-ownership timing.

Supported cleanup terminated all ten functional-test instances, deleted their
ten root disks and seed stack, and cleared cycle records. The next cycle is READY,
CURRENT UNINITIALIZED, propagation disabled, no HOLD or locks.
`max_generation` is restored to **10** and `max_live_generations` remains **3**.
The functional release remains approved and deployed, with both tested private
AMIs retained. Capacity observations include shared observer/controller activity
and metric delivery lag; they are not agent-only usage or an actual bill.
Exact pins, ownership timestamps, comparison, metering and evidence hashes are in
the functional release receipt (retained locally).

## 2026-10-03 UTC — EC2 polling: ten generations passed

The polling release completed generations 0–9 and nine handoffs using a verified
private baked image built from merged PR #14 source `b7c4f04`. One-second
ownership/readiness polling and batched exact instance checks retain the original two-heartbeat gate. No HOLD occurred;
the sampled live peak was three, including shutting-down instances. Generation 9
confirmed predecessor retirement before the supported stop.

| Measurement | Earlier overlap trial | Polling trial |
| --- | ---: | ---: |
| Seed creation → initial ownership | 36.192s | 35.497s |
| Mean ownership interval, nine intervals | 38.777s | 34.168s |
| Seed creation → generation 9 ownership | 385.187s | 343.007s |
| Sampled live-instance peak | 3 | 3 |

The polling trial took 42.180s (10.95%) less total time. Across nine intervals,
ownership → successor launch averaged 2.240s, launch → first sampled healthy
heartbeat 24.885s, and that heartbeat → ownership 7.043s. These are endpoint
intervals, not isolated SDK/DryRun phases. Single trials with different immutable
images and startup variance do not isolate causality. Sampler median cadence
was 3.530s and maximum 3.715s. Build, deployment and cleanup are excluded from
chain duration. Refreshed CloudTrail evidence contains ten successful live
RunInstances, eight expected DryRunOperation responses and ten successful
TerminateInstances including final cleanup; lookup evidence may lag delivery.

Supported cleanup terminated all ten instances and deleted all ten root disks,
the seed stack and cycle records. The next cycle was READY, CURRENT UNINITIALIZED,
propagation disabled, no HOLD or locks, max_generation restored to 10 and the
hard max_live_generations remains 3. Exact pins, timestamps and evidence hashes
are in the polling release receipt (retained locally).

The functional comparison is recorded in the newer entry above.

## 2026-10-03 local / 2026-10-04 UTC — Observer-recorded EC2 propagation

The operator-requested observer test completed generation 0 through 10, with
ten consecutive ownership handoffs. Configuration: us-west-2, t4g.micro,
EC2 backend, pinned numeric launch-template version 4, generation limit 10,
three-instance ceiling, one-second readiness polling and five-second heartbeat
telemetry. CONTROL identifies build `cd28e2662f4451ac77633aab5ea64e9b8967d417`
and the functional-readiness release. This observer change did not modify the
agent or launch pins.

Source boundaries are the earliest retained CURRENT `updated_at` for each
generation, read from raw DynamoDB stream NewImage records in the private local
SQLite archive. All eleven boundaries are present; browser arrival and EC2
reconciliation times are excluded. Source clocks can have cross-host skew.

| Hop | Successor ownership (UTC) | Interval (s) |
| --- | --- | ---: |
| 0 → 1 | 06:04:29.869 | 34.853 |
| 1 → 2 | 06:05:02.469 | 32.600 |
| 2 → 3 | 06:05:29.787 | 27.318 |
| 3 → 4 | 06:06:06.385 | 36.598 |
| 4 → 5 | 06:06:37.795 | 31.410 |
| 5 → 6 | 06:07:06.921 | 29.126 |
| 6 → 7 | 06:07:37.377 | 30.456 |
| 7 → 8 | 06:08:07.230 | 29.853 |
| 8 → 9 | 06:08:36.874 | 29.644 |
| 9 → 10 | 06:09:06.319 | 29.445 |

Initial ownership **06:03:55.016 UTC** → generation 10
ownership **06:09:06.319 UTC**: **311.303 s**.
Mean hop **31.130 s**; median **30.154 s**;
range **27.318–36.598 s** (n=10). Observed propagation rate:
**1.927 ownership handoffs/minute** (0.03212/second).
The configured inclusive limit produces eleven generations and ten hops.

This excludes bootstrap request → initial ownership and final cleanup. Individual
API, readiness, continuation and termination durations, retry count and live
overlap peak were not measured for this entry. Final CURRENT and cycle identity
were independently checked through a consistent DynamoDB read. This is one trial;
it does not isolate a speedup or establish cost savings. No cleanup was requested
as part of this measurement; the terminal instance remains until operator cleanup.

Private evidence SHA-256: `56ffd711fd86a4b0bff1310931d42cf88628cb308527fe4a969bce5ce52d45b5`. Raw operational data stays under ignored
`.observer/`; identifiers and raw records are omitted from this public summary.

### Why 385.187 seconds and 311.303 seconds differ

Those totals use different boundaries and hop counts. The older overlap result
includes **36.192 s of seed creation → initial ownership**, then **nine hops**
through generation 9. The newer result excludes bootstrap and includes **ten
hops** through generation 10. Subtracting the headline totals does not measure
an equivalent-work speedup.

| Comparable measurement | Earlier overlap release | Current functional-readiness run |
| --- | ---: | ---: |
| Generation 0 ownership → generation 9 ownership (nine hops) | 348.995 s | 281.858 s |
| Mean across those same nine hops | 38.777 s | 31.318 s |
| Bootstrap included in headline total | 36.192 s | Excluded |
| Extra generation 9 → 10 hop in headline total | None | 29.445 s |

On matching generation 0 → 9 boundaries, the newer trial was **67.137 s
(19.24%) shorter**. The headline difference of 73.884 s decomposes into
36.192 s of excluded bootstrap + 67.137 s of shorter matching hops − 29.445 s
for the newer run's additional hop.

Both releases already use direct EC2 provisioning and retirement overlap.
The intervening agent changes are [one-second ownership/retry polling with
independently spaced heartbeats](ec2-polling-evaluation.md), one-second readiness
polling in current CONTROL, and [successor-produced functional readiness
(decision 0021)](decisions/0021-successor-functional-readiness.md). The older
release required two eligible heartbeats at least five seconds apart; the new
release accepts a fresh successor capability proof after identity, control,
continuation and handoff checks, removing that fixed observation wait. The
older trial's first-heartbeat → ownership interval averaged 7.852 s, making the
removed wait and faster polling plausible contributors to the shorter hops.
Heartbeat telemetry itself remains five seconds; no additional overlap change
was introduced for this observer run.

These are documented mechanism changes and an observed end-to-end difference,
not a measured causal allocation. New phase timings, startup/termination
variability and isolated polling savings were not captured; source/AMI versions
also differ. The observer's DynamoDB Streams integration improves measurement
fidelity and does not change agent readiness or speed up propagation. One trial
per release cannot determine exactly how much each change contributed.

## 2026-10-03 UTC — EC2 retirement overlap: ten generations passed

The operator-authorized overlap release completed generations 0–9 and nine
conditional handoffs using private AMI `ami-0dd526b0623ff67a3` and numeric Launch
Template version 2. No HOLD occurred; the sampled live peak remained three,
including shutting-down instances. The sampler observed a candidate running
while its owner's predecessor was shutting down, with CURRENT retained by the
owner. At each observed ownership transfer, the older retirement target was
terminated. Generation 9 confirmed predecessor retirement before stopping.
Exact pins, generation timestamps, comparison and evidence hashes are in the
release receipt (retained locally).

| Measurement | CloudFormation baseline | Serialized EC2 | Overlap EC2 |
| --- | ---: | ---: | ---: |
| Seed creation → initial ownership | 50.662s | 37.239s | 36.192s |
| Mean ownership interval, nine intervals | 39.481s | 56.391s | 38.777s |
| Seed creation → generation 9 ownership | 405.991s | 544.756s | 385.187s |
| Sampled live-instance peak | 3 | 2 | 3 |

The overlap run took 29.3% less time than serialized EC2 and 5.1% less time
than the earlier CloudFormation baseline. Across nine intervals, ownership
→ successor launch averaged 3.896s,
launch → first workload-healthy heartbeat 27.029s,
and first heartbeat → ownership 7.852s.
These are endpoint intervals, not individual API or pure termination durations.
The first heartbeat is a startup proxy; authoritative readiness retained two
heartbeats and fresh identity, control, continuation and handoff checks.

This is one trial per release with different source/image and retirement behavior;
it supports the observed benefit of removing serialized retirement from the launch
path but does not isolate provisioning API overhead. Internal phase JSON remains
in local journals, so SDK/DryRun durations are not measured. The sampler's median
cadence was 3.496s and maximum 3.820s. Build, deployment, the conclusively
rejected idle start and cleanup are excluded from chain duration.

Supported cleanup terminated all ten test instances and deleted their ten root
disks, generation alarms, seed stack and cycle records. Request 6 is READY,
CURRENT is UNINITIALIZED, propagation/bootstrap are disabled, and no HOLD or locks
remain. Per operator instruction, max_generation is now **10** and the absolute
max_live_generations remains **3**. The tested private image, encrypted snapshot
and approved Launch Template are retained.

## 2026-10-03 UTC — Direct EC2: ten generations passed, slower than baseline

The corrected, operator-authorized EC2 run completed generations 0–9 and nine
conditional handoffs, with no HOLD and an observed live peak of two. All eight
continuation requests returned the expected `DryRunOperation`; ten actual
RunInstances and nine predecessor TerminateInstances successes were retained
before cleanup. Generation 9 confirmed predecessor termination before stopping.
Supported cleanup terminated all ten test instances and deleted their ten root
disks, generation alarms, seed stack and cycle records. Propagation is disabled,
max_generation is restored to 2, and request 5 is ready with CURRENT uninitialized
and no HOLD or locks. The private image, snapshot and approved Launch Template
remain. Exact identities and per-generation timestamps are in the
release receipt (retained locally).

| Measurement | Previous CloudFormation baked-image run | Direct EC2 baked-image run |
| --- | ---: | ---: |
| Seed stack creation → initial ownership | 50.662s | 37.239s |
| Mean ownership interval, nine intervals | 39.481s | 56.391s |
| Ownership interval range | 38.555–42.458s | 35.323–71.586s |
| Seed creation → generation 9 ownership | 405.991s | 544.756s |
| Sampled live-instance peak | 3 | 2 |

The complete EC2 chain was **34.2% slower**, an increase of 138.765s. Its mean
ownership interval increased 42.8%. Seed startup improved by 13.423s, but the
nine handoff intervals together increased by 152.188s. Build, deployment,
the earlier rejected test and final cleanup are excluded from both totals.
The EC2 sampler included pending and shutting-down instances, with a median
interval of 3.505s and maximum of 3.706s. These are single runs with different
source/image and concurrency behavior, not a controlled measurement of the
provisioning APIs alone.

### Why this implementation was slower

The baseline agent transferred ownership and submitted DeleteStack for its own
stack. The new owner could start provisioning while that deletion continued.
The EC2 agent instead makes its new owner verify and terminate the predecessor,
wait until DescribeInstances confirms `terminated`, mark retirement complete,
and only then call RunInstances for the next generation. This puts retirement
on the launch critical path and explains the observed reduction from three
overlapping instances to two.

Across the eight intervals with a predecessor to retire, ownership → next
EC2 LaunchTime was 20.273–35.617s, averaging 26.104s. The first interval, with
no predecessor retirement, spent only 1.533s before launch and completed its
handoff in 35.323s. Later intervals averaged 59.024s. The measured endpoint
breakdown across all nine intervals is:

| Endpoint interval | Mean |
| --- | ---: |
| Ownership → next EC2 launch | 23.374s |
| Launch → first workload-healthy heartbeat | 25.580s |
| First heartbeat → conditional ownership transfer | 7.437s |
| Complete ownership interval | 56.391s |

Every captured first heartbeat had sequence 1. That first heartbeat alone is
not the authoritative readiness gate: both runs retained two required
heartbeats, a 5s heartbeat interval and 2s readiness polling. The EC2 retirement
loop also sleeps on the 5s cycle cadence while termination is pending. Polling
can delay observing completion, and the prelaunch endpoint includes ownership
checks, template/image verification and other API work as well as retirement.
It must not be labeled an isolated EC2 termination duration.

This EC2 version also restores an unexecuted next-hop RunInstances DryRun,
fresh controls and quota/capacity checks, whereas the comparison baseline had
removed its CloudFormation preflight. Direct status-alarm bookkeeping and
repeated template/instance checks add serial API calls. Their individual costs
are **not measured**: phase JSON stayed in instance journals without remote
export. The heartbeat-to-handoff interval includes readiness, continuation and
handoff; it does not isolate DryRun latency. Retained launch events show no
failed live launch after the profile fix, but cannot rule out transient read
errors hidden in local journals.

Removing CloudFormation from successor provisioning therefore did not remove
the larger chain's waiting. Serialized retirement is the strongest supported
explanation for the slowdown; this evidence does not assign the entire change
to retirement or predict the exact benefit of overlap. The separately requested
overlap implementation requires its own review and benchmark before deployment.

## 2026-10-03 UTC — First direct EC2 attempt stopped at generation 0

The authorized deployment used the EC2-integrated private AMI and Launch
Template `lt-09e12d4ca882601db`, numeric version 1. Seed creation to initial
ownership was 44.093s. The first successor RunInstances request was rejected:
the agent policy and boundary omitted the existing instance profile's
`/cloud-glider/` path. One instance launched, no handoff completed, and the
sampled live peak was one. This is a failed runtime test, not a ten-generation
benchmark; ownership intervals and a comparable total are **not measured**.
No speedup is claimed. The exact profile-path correction is prepared, with
257 tests and template linting passing. The failed cycle was cleaned up before
the successful retry above.

Internal phase records remain in instance journals without remote export in
this release. API evidence and durable ownership timestamps are retained;
unavailable internal phase durations must remain **not measured**.

## 2026-10-03 UTC — EC2-integrated candidate, isolated boot

The candidate receipt (retained locally)
records an encrypted 2 GiB ARM64 image built from the merged EC2 backend source.
Build duration: 686s. Metadata and isolated cold-boot contract checks passed;
temporary resources were cleaned up. This is image compatibility evidence.
Generation startup-to-readiness, ownership intervals, predecessor retirement
and ten-generation propagation duration: **not measured**. Phase timing
instrumentation is present for a later authorized run. No speedup is claimed
against the 39.481s mean interval and 405.991s ten-generation baseline below.

## October 3: fixed baked image, ten generations

The operator-authorized run from merged preflight-removal source completed
generations 0–9 with nine handoffs and no hold. Initial creation-to-ownership:
50.662s. First creation-to-final ownership: 405.991s. Mean ownership interval:
39.481s; range 38.555–42.458s. Observed live-instance peak: three, sampled at
about 3.3s including pending and shutting-down instances. All benchmark stacks
were deleted; propagation is disabled and max_generation restored to 2.
See the release receipt (retained locally)
for exact source/image identities and per-generation timings. These measurements
exclude build/deployment/cleanup; the earlier failed benchmark is historical.

### Comparison with the October 2 successful run

The previous full run launched 11 generations (0–10); this run launched 10
(0–9). Match whole-run totals at generation 9, from generation 0 CloudFormation
CreationTime to generation 9 conditional ownership transfer:

| Measurement | October 2 successful run | New fixed baked-image run | Reduction |
| --- | ---: | ---: | ---: |
| Mean recorded ownership interval (9 intervals each) | 61.802889s | 39.481s | 36.1% |
| Recorded ownership interval range | 53.053–71.962s | 38.555–42.458s | — |
| Generation 0 creation → generation 9 ownership | 584.782s | 405.991s | 30.6% (178.791s saved) |

The previous mean uses the nine recorded generation 1→2 through 9→10
intervals in the October 2 table below. The new mean uses generation 0→1
through 8→9 from the release receipt. These interval samples have different
hop boundaries; the previous final interval also skipped its continuation
preflight. The matched total is derived as 637.835s through generation 10
minus its final 53.053s interval = 584.782s through generation 9. Totals exclude
time before first stack creation, AMI build, deployment and final cleanup.
Different image and source versions mean the improvement cannot be attributed
solely to removing preflight. These are individual runs, not evidence of a
repeated-run statistical improvement.

The immediately preceding failed baked-image attempt launched only two
generations and completed one handoff before the stranded-preview failure.
Its reported creation-to-initial-ownership time was 39.1s and its single
ownership interval was 42.4s. New startup was **slower**, at 50.662s versus
39.1s; the new mean interval was 39.481s versus that one 42.4s observation.
The failed-attempt figures are rounded operational observations supplied with
that incident, not a complete benchmark receipt or a repeated sample. The
completion distinction is ten launches, nine validated handoffs and no failures
in the new run; the single failed-attempt handoff cannot establish a general
speed improvement.

## 2026-10-02 UTC — Run through generation 10

Run `perf-20261002-01`: sandbox, `us-west-2`, `t4g.micro`, ARM64 Amazon Linux
2023 (2023.12.20260914.0, kernel 6.18). Template build revision:
`1786c6e485990953405e67c3ebd12b9ae9a60008`. Ten successful ownership handoffs
produced generations 1–10 after bootstrap generation 0 (11 compute stacks).

Measured from retained CloudFormation stack events and DynamoDB
`CONDITIONAL_HANDOFF` audit timestamps, re-read October 2. Stack creation is
CloudFormation CreationTime → CREATE_COMPLETE; creation → handoff ends at the
APPLIED ownership-transfer audit timestamp. Handoff interval is the time between
successive ownership transfers. Preflight-only stacks are excluded.

| Generation | Stack creation (s) | Creation → handoff (s) | Handoff interval (s) |
| ---: | ---: | ---: | ---: |
| 0 | 21.328 | Bootstrap | — |
| 1 | 20.215 | 47.507 | — |
| 2 | 20.228 | 47.337 | 59.305 |
| 3 | 22.514 | 47.694 | 60.990 |
| 4 | 24.522 | 59.610 | 71.962 |
| 5 | 22.821 | 55.228 | 67.007 |
| 6 | 23.062 | 46.976 | 58.573 |
| 7 | 24.196 | 51.073 | 64.886 |
| 8 | 20.101 | 46.993 | 57.054 |
| 9 | 23.753 | 50.717 | 63.396 |
| 10 | 20.938 | 34.349 | 53.053 |

Generation 0 creation at **04:37:25.723 UTC** → generation 10 ownership at
**04:48:03.558 UTC**: **637.835 seconds (10 min 37.835 s)**. This excludes time
before bootstrap stack creation and final-generation cleanup.

- Stack creation: median **22.514 s**, range **20.101–24.522 s** (n=11).
- Creation → handoff: median **47.601 s**, range **34.349–59.610 s** (n=10).
- Successive handoffs: median **60.990 s**, range **53.053–71.962 s** (n=9).

Generation 10's terminal handoff skips the next-hop change set at the configured
limit, so its timing is not directly comparable to intermediate generations.
Predecessor stacks 0–9 reached DELETE_COMPLETE; stack deletion durations ranged
**26.824–38.675 s**. Generation 10 remained CREATE_COMPLETE at collection.
Separate readiness/preflight durations, overlap peak, and stop response were not
measured; handoff timing includes these gates rather than isolating them.
This entry supersedes the initial provisional observation that completion
through generation 10 was unverified. AWS resource identifiers are omitted.

## 2026-10-02 — Known baseline and observations

Sources: repository revision `b5ee028` and the October 1 local recovery draft.
The initial inventory used repository evidence; the measured run above was
subsequently recovered from retained AWS records.

| Timing setting | Configured baseline |
| --- | --- |
| Heartbeat interval | 5 seconds |
| Successor readiness | 2 eligible heartbeats at least 5 seconds apart |
| Readiness polling | 2 seconds |
| Successor wait timeout | 600 seconds, including stack creation wait in current implementation |
| Continuation preflight timeout | Separate 600-second wait for an unexecuted CREATE change set |

Source: [runtime defaults](../config/runtime-defaults.json) and
[agent implementation](../daemon/cloud_glider/daemon.py). These are settings,
not measured generation times. The source checkout had older 30/15-second
README values and described the timeout as starting after CREATE_COMPLETE.
Current cloud-glider main documents the 5/2-second settings and the timeout
including stack creation; retain the actual run settings when comparing results.

## Future run entry

Record one row per successor generation, including failed or interrupted hops.

```text
UTC window / anonymized run label / public Git revision:
Configuration: Region, instance type, generation limit, actual timing settings
Evidence source and verification status:

Generation N→N+1:
  CreateStack→CREATE_COMPLETE: ___ seconds
  CREATE_COMPLETE→authoritative readiness: ___ seconds
  Continuation preflight start→pass: ___ seconds
  Readiness→conditional CURRENT handoff: ___ seconds
  N CURRENT handoff→N+1 CURRENT handoff (hop interval): ___ seconds
  Predecessor DeleteStack→DELETE_COMPLETE: ___ seconds
  Generation overlap: ___ seconds; peak live generations: ___
  Outcome / retry count / missing evidence:

Whole run: bootstrap request→final verified handoff: ___ seconds
Completed hops / attempted hops:
```

State event boundaries and clock sources; cross-host clock skew can affect
elapsed times. Readiness requires workload health, approved generation/template
identity, fresh control, and ownership. Stack completion alone is insufficient.
Handoff and predecessor deletion completion are separate events. Define overlap
as successor CreateStack submission through predecessor DELETE_COMPLETE,
including provisioning and deletion-in-progress.

Publish only sanitized timings, generic configuration, public revisions, and
anonymized run labels. Exclude AWS identifiers, addresses, credentials, user
data, and raw logs; follow the [audit logging contract](audit-logging.md).

## Next measurements

- Capture the phase timings above during the next authorized bounded run.
- Measure stop/hold response and confirm failed readiness or handoff preserves
  the predecessor; include retries and failed hops in timing summaries.
- Compare hop intervals and overlap across repeated comparable runs; report
  sample count and median/range before proposing timing targets.

Boot and per-API collection boundaries and the pre-retirement capture procedure
are described in [boot/API timing collection](boot-api-timing.md). Instrumented
source alone does not establish a performance improvement.

## 2026-10-04 combined daemon/timing single-successor benchmark

Run D: ten total generations (0–9), nine handoffs, EC2 backend, ARM64
`t4g.micro`, baked 2 GiB image, source `a32e6c9`. Existing readiness and
retirement gates remained active; maximum live generations was three.
Internal boot, SDK-call and phase journals were not collected through an
approved access path and are **not measured**. No causal speedup is claimed.

Ownership boundaries use successor-written UTC `CURRENT.updated_at`; EC2
launch times use EC2 `LaunchTime`. Readiness is the first observer sample of
successor-produced live STATE; this observation can lag readiness by about
two seconds and does not independently prove parent acceptance. Conditional
CURRENT handoff provides the acceptance endpoint. Cross-host clock skew can
affect timestamp differences. Observer scans and EC2 reads are sequential,
not an atomic snapshot.

| Generation | Launch→observed readiness (s) | Launch→ownership (s) | Ownership hop (s) |
| --- | ---: | ---: | ---: |
| 0 | 36.178 | 34.453 | — |
| 1 | 32.243 | 32.236 | 33.783 |
| 2 | 32.314 | 32.813 | 34.577 |
| 3 | 27.375 | 27.864 | 30.051 |
| 4 | 25.430 | 26.184 | 28.320 |
| 5 | 26.496 | 26.974 | 29.790 |
| 6 | 27.557 | 31.626 | 33.652 |
| 7 | 25.614 | 25.599 | 27.973 |
| 8 | 29.668 | 31.623 | 34.024 |
| 9 | 31.735 | 30.579 | 32.956 |

Nine ownership intervals: mean **31.681s**, median
**32.956s**, range **27.973–34.577s**.
Seed stack creation→initial ownership: **38.585s**;
seed creation→generation 9 ownership: **323.711s**.
Initial ownership→generation 9: **285.126s**.
Sampled live peak: **3**; observed instances: **10**. No failed ownership
hops or replacement instances were observed; SDK retries are not measured.
Median observer cadence was **2.005s**, maximum
**2.006s**. All predecessors disappeared from the
live inventory before cleanup; termination is verified separately.

Against the earlier 31.012s mean / 310.247s seed-to-final functional baseline,
this single run was about 2.2% higher in mean interval and 4.3% higher in total.
This is an observational comparison, not evidence of a causal regression or
improvement. Instance boot variation and collection boundaries limit inference.

Supported stop and cleanup completed. All ten EC2 instances are confirmed
terminated; generation disks, records and seed stack are absent. The lifecycle
is READY, propagation disabled, and the prior generation limit of 2 restored.
No pending cleanup retry token remains. Scheduler inventory was not independently
queried after this run because the CLI lacks that permission and the browser
was unavailable.

## 2026-10-04 repeat combined daemon/timing benchmark

Run E repeats run D with the same release and settings: ten generations 0–9,
nine handoffs, EC2 backend, ARM64 `t4g.micro`, baked 2 GiB image, source
`a32e6c9`, existing readiness/retirement gates and three-live ceiling. No boot
optimization or additional collection permissions/infrastructure was introduced.
SSM and SSH are disabled in the runtime image; timing records remain local
and are not exported. Internal boot, API and phase journal durations are
**not measured**, so this repeat is not a boot diagnosis.

EC2 `LaunchTime`, first observed live STATE and successor functional proof
provide distinct endpoints. Live STATE may precede functional readiness; the
run D table's “observed readiness” column likewise measures first live STATE,
not authoritative readiness. `CURRENT.updated_at` is the accepted ownership
endpoint. Functional `proved_at_epoch` has whole-second precision. Sequential
reads are not atomic; two-second sampling and cross-host clock skew limit
phase subtraction. Proof→ownership includes publication and parent validation,
not only boot time. First observed STATE publication timestamps may already
reflect a heartbeat, so an exact initial publication boundary is not claimed.

| Generation | Launch→observed live STATE (s) | Launch→functional proof (s) | Proof→ownership (s) | Launch→ownership (s) | Ownership hop (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 28.379 | — | — | 27.659 | — |
| 1 | 25.432 | 23.000 | 3.719 | 26.719 | 28.060 |
| 2 | 22.480 | 22.000 | 3.684 | 25.684 | 27.965 |
| 3 | 30.554 | 28.000 | 4.157 | 32.157 | 34.473 |
| 4 | 21.608 | 22.000 | 3.741 | 25.741 | 28.584 |
| 5 | 29.682 | 27.000 | 4.701 | 31.701 | 33.960 |
| 6 | 27.742 | 25.000 | 4.265 | 29.265 | 31.564 |
| 7 | 25.805 | 23.000 | 3.767 | 26.767 | 29.502 |
| 8 | 28.872 | 25.000 | 4.199 | 29.199 | 31.432 |
| 9 | 23.932 | 23.000 | 1.397 | 24.397 | 26.198 |

Nine handoffs: mean **30.193s**, median **29.502s**,
range **26.198–34.473s**. Seed creation→initial
ownership: **31.985s**; seed creation→generation 9:
**303.723s**; initial→final ownership:
**271.738s**. Sampled live peak **3**, observed
instances **10**; no failed handoffs or replacement instances observed. SDK
retries remain unmeasured. Observer cadence median **2.005s**,
maximum **2.005s**. All predecessors left the live inventory
before cleanup. Observer UI was supplemental and showed transient lag/status
misclassification; direct AWS/DynamoDB capture supplies these results.

Compared with run D (31.681s mean / 323.711s total), this run was about
4.7% lower in mean and 6.2% lower in total. Against the older functional
baseline (31.012s / 310.247s), it was about 2.6% and 2.1% lower. Both daemon
runs use unchanged software and have overlapping interval ranges; these
observations do not establish causal speedup.

A separately reviewed collection change could export best-effort sanitized
timing records to the existing daemon operations log group, using its existing
log-write permissions. Review volume, cost, failure isolation and contract
before implementing; do not add retirement delays or readiness dependencies.

Supported stop/cleanup completed. All ten instances are confirmed terminated,
and generation root disks, seed stack and records are absent. READY with
propagation disabled, active command NONE and prior generation limit 2 restored;
all installed release pins verified. No pending cleanup retry token remains.
Scheduler inventory was not independently queried after this run; available
CLI permissions do not allow it.

## 2026-10-05 durable timing diagnostic run

Run F / cycle 16 used ten generations 0–9, ARM64 `t4g.micro`, the EC2 backend, numeric launch-template version 6 and the baked durable-timing release described in [rollout evidence](durable-timing-rollout.md). Existing health, conditional handoff and retirement checks remained in force. No collector IAM permissions were added.

Nine ownership handoffs: mean **30.935s**, median **29.942s**, range **28.032–34.570s**. Initial→final ownership took **278.414s**. Launch→ownership averaged **29.249s** (10 instances). Sampled live peak **3**; snapshot cadence median **2.004s**, maximum **2.006s**. Endpoints come from EC2 LaunchTime and CURRENT.updated_at; snapshots are sequential, and cross-host clock skew limits subtraction.

Post-termination logs contain **1,930 timing records from 10/10 instances** and **10/10 complete receipts**, with zero reported drops/upload errors, invalid identities or conflicting duplicates. Strict sequence/digest and both boot-verifier-context checks passed. No log loss was observed in this trial. Failed-start export remains best-effort and was not exercised on a real failing AMI during this run.

Durations below are pooled observed invocation samples. Short reconciliation/no-work paths and repeated phase invocations are included. Different phases overlap and must not be summed into total boot time. Background collector client initialization is not a synchronous readiness gate; its CPU/network use can still contend with startup. Missing samples would be excluded, never treated as zero.

| Phase / context | Mean (s) | Median (s) | Samples | Instances |
| --- | ---: | ---: | ---: | ---: |
| baked_image_verification/user_data | 0.009468 | 0.009428 | 10 | 10/10 |
| baked_image_verification/service_pre | 0.001011 | 0.000987 | 10 | 10/10 |
| runtime_imports | 0.294042 | 0.293128 | 10 | 10/10 |
| ec2_runtime_imports | 0.019681 | 0.019559 | 10 | 10/10 |
| sdk_initialization | 0.259454 | 0.255348 | 10 | 10/10 |
| collector_initialization | 0.023635 | 0.013480 | 10 | 10/10 |
| collector_client_initialization | 0.207832 | 0.204466 | 10 | 10/10 |
| startup_identity_validation | 0.363091 | 0.373183 | 10 | 10/10 |
| predecessor_retirement | 0.374190 | 0.349973 | 24 | 10/10 |
| successor_submission | 1.631200 | 1.954436 | 12 | 9/10 |
| successor_readiness | 19.382496 | 24.009428 | 12 | 9/10 |
| continuation_dry_run | 0.891174 | 0.988923 | 9 | 9/10 |
| conditional_handoff | 0.132168 | 0.132201 | 9 | 9/10 |
| candidate_functional_probe | 1.236823 | 1.373447 | 9 | 9/10 |
| predecessor_retirement_reconciliation | 0.479579 | 0.463779 | 8 | 8/10 |

SDK call spans include the full SDK call, including any internal retries; retry counts and transport-only latency are not collected. Expected `DryRunOperation` responses below indicate successful permission preflight rather than launch failure. These are daemon API calls; operator/controller calls and collector log-write calls are excluded.

| API / result | Mean (s) | Samples | Instances |
| --- | ---: | ---: | ---: |
| dynamodb.transact_get_items | 0.013655 | 336 | 10/10 |
| ec2.describe_launch_template_versions | 0.033366 | 69 | 10/10 |
| ec2.describe_images | 0.038610 | 69 | 10/10 |
| ec2.describe_instances | 0.078788 | 392 | 10/10 |
| dynamodb.get_item | 0.004755 | 324 | 10/10 |
| dynamodb.transact_write_items | 0.019826 | 355 | 10/10 |
| ec2.describe_instance_type_offerings | 0.019900 | 25 | 9/10 |
| service-quotas.get_service_quota | 0.051153 | 25 | 9/10 |
| ec2.describe_instance_types | 0.027358 | 25 | 9/10 |
| ec2.describe_subnets | 0.103987 | 25 | 9/10 |
| ec2.run_instances | 1.282125 | 9 | 9/10 |
| dynamodb.delete_item | 0.006294 | 33 | 10/10 |
| cloudwatch.describe_alarms | 0.083530 | 20 | 10/10 |
| cloudwatch.put_metric_alarm | 0.060378 | 12 | 9/10 |
| ec2.describe_instances / InvalidInstanceID.NotFound | 0.051688 | 3 | 3/10 |
| ec2.run_instances / dry-run / DryRunOperation | 0.572114 | 16 | 9/10 |
| dynamodb.transact_get_items / TransactionCanceledException | 0.015943 | 2 | 2/10 |
| ec2.terminate_instances | 0.395360 | 9 | 9/10 |
| cloudwatch.delete_alarms | 0.097317 | 8 | 8/10 |

Captured SDK exceptions also included three `InvalidInstanceID.NotFound` calls and two `TransactionCanceledException` calls. The cycle still completed all nine handoffs without a hold; these call-level exceptions are recorded rather than counted as failed handoffs.

This single instrumented trial does not establish a causal speedup. Against run E (30.193s mean) the mean is about 2.5% higher, within overlapping ranges. The image kernel also changed. The isolated one-pair smoke measured roughly 0.263s additional process CPU and 0.095s enabled shutdown drain; caches/order confound that comparison.

Supported stop/cleanup verified all ten instances terminated and generation disks, records, seed stack, holds, locks and cleanup schedules absent. Lifecycle READY, propagation disabled, active command NONE, `max_generation=9` retained. A second supported stop refreshed stale DELETING controller feedback after cleanup completed. The Observer shows run 16 complete with ten terminated instances; AWS captures are authoritative.

Full sanitized means/medians/ranges and outcome counts are in [the release receipt](../config/releases/2026-10-05-durable-timing-diagnostic.json). Historical runs D/E and their restored limit 2 describe their own end states; the current retained limit is 9.
