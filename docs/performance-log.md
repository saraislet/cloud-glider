# Generation and propagation timing log

Public summaries of generation timing. Keep dated entries in reverse
chronological order, with newest run results and corrections first;
keep raw operational evidence private. Use UTC timestamps and seconds, and
write `not measured` for missing durations.

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
[release receipt](../config/releases/2026-10-03-ec2-overlap-minimal-ami.json).

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
[release receipt](../config/releases/2026-10-03-ec2-integrated-minimal-ami.json).

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

The [candidate receipt](../config/releases/2026-10-03-ec2-integrated-minimal-ami.json)
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
See the [release receipt](../config/releases/2026-10-03-preflight-removal-minimal-ami.json)
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
[agent implementation](../agent/cloud_glider/agent.py). These are settings,
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
