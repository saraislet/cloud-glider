# Generation and propagation timing log

Public summaries of generation timing. Keep dated entries in reverse
chronological order, with newest run results and corrections first;
keep raw operational evidence private. Use UTC timestamps and seconds, and
write `not measured` for missing durations.

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
