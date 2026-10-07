# Independent inherited EC2 propagation

The depth-2 release was deployed and tested live on 2026-10-06. Larger tests
require separate operator approval. This runbook covers version-3 EC2 family cycles. Architectural
changes and preserved gates are recorded in [decision 0023](decisions/0023-independent-inherited-ec2-families.md).

Every live trial ends with the [verified cleanup checklist](trial-cleanup-and-ami-retention.md).
STOP alone leaves instances running. Collect evidence, use supported CLEANUP,
verify resources are gone and record any operator-approved retained exceptions.

## Operator configuration

On an offline, cleaned-up environment, prepare CONTROL/GLOBAL with:

| Field | Initial value | Meaning |
| --- | --- | --- |
| propagation_backend | ec2 | Direct successor APIs; retained infrastructure and seed use CloudFormation |
| configuration_inheritance | true | Bootstrap pins configuration for a new family cycle |
| binary_fanout_enabled | false or true | One or two children per node |
| max_generation | 2 | Three levels; seven nodes in binary mode |
| control_poll_seconds | 2 | Async control-read cadence |
| control_max_age_seconds | 15 | Latch stop when control observations expire |
| retry_backoff_max_seconds | 30 | Bound local exponential retry backoff with jitter |

The initializer now sets inheritance for new EC2 configurations. `--binary-fanout`
selects binary mode; omission selects one child. It remains dry-run by default
and cannot overwrite existing records. Existing table migration requires a
reviewed conditional operator update while no cycle is live. Keep propagation
disabled until the complete handoff can be observed. Use the normal START
command, which pins settings and supplies enabled initial state to the seed.
Low-level paused bootstrap is not supported for inherited cycles.

BOOTSTRAP/REQUEST stores the cycle configuration digest, initial enabled state
and immutable control snapshot. The seed receives CycleConfiguration and
ConfigurationSha256 through CloudFormation. Agents inherit this configuration;
they do not poll CONTROL to obtain depth, mode, timing or artifact values.
Edits during a run are next-cycle settings only. Legacy max_live_generations is
ignored by families; no live-instance, total-allocation, rate or permit gate is
implemented for binary mode.

STOP/HOLD is asynchronous for launches. Nodes latch it and publish STOP records
for themselves and their immediate children. Extra descendants can be launched
before observation. Running instances remain. Local stop survives service restart.
There is no resume after a stop or freshness expiry: inspect, CLEANUP, then START
a new cycle. Handoff/retirement retain atomic fresh-control checks.

GEN#r, GEN#r0, GEN#r1 and later lineage keys identify families. NODE reports
CANDIDATE, OWNER, LEAF or RETIRING; STATE contains refreshed functional readiness;
SUBMISSION records deterministic launch identity and settlement; RESOURCE records
exact EC2 instances. CURRENT stays UNINITIALIZED and is not the binary status
view. Leaves remain running until CLEANUP. The first three-level binary trial
should leave four leaf instances after interior retirement.

## AWS work required before deployment

The deployment steps below were completed for the depth-2 candidate; the live trial findings are recorded at the end.

1. Stop and clean up the existing cycle using the approved operator path. Verify
   no live generations or unresolved submissions. Never activate the new schema
   or change the backend in a live cycle.
2. Build the deterministic agent archive with `scripts/build_daemon_artifact.py`.
   It now includes inherited configuration, family-agent and family-SDK modules.
   Build a fresh private ARM64 baked AMI from that exact archive using the existing
   Packer path. Preserve IMDSv2, the integrity manifest, installed pinned SDK,
   disabled service, encrypted 2 GiB root and existing networking constraints.
   Existing AMIs do not contain this implementation. Run isolated cold-boot smoke
   tests, including the new family imports and existing EC2 baked contract.
3. Publish the exact archive and changed ec2-seed template as immutable versioned
   artifacts. Record their digests and S3 versions. Prepare a new numeric launch
   template version with the new AMI and matching static artifact pins through
   CloudFormation. Describe/hash that exact version and prepare its CONTROL pins.
   Review the seed/successor user-data override: executable startup is fixed source;
   only inherited configuration varies. Do not use $Latest or $Default.
4. Deploy the changed bootstrap Lambda/template, including per-child cleanup
   settlement checks and pinned-control handling, before activating inheritance.
   The Lambda is still source-embedded through render_bootstrap_template.py.
   Deploy the foundation emergency-hold function update too: it accepts node
   lineage so a family error updates the correct STATE instead of conflating
   siblings at the same generation. Its existing IAM resource scope is unchanged.
5. Review runtime and bootstrap IAM policies, permission boundaries and any SCPs.
   The implementation introduces no new AWS API family or direct IAM/network
   mutations. Existing EC2 policies in source allow condition checks, get, put
   and update for GEN#* and condition checks/read access to CONTROL/HOLD/BOOTSTRAP.
   Confirm deployed policies allow these for per-node NODE/STATE/SUBMISSION/STOP
   and RESOURCE keys, exact-template RunInstances with user-data overrides,
   existing-role PassRole to EC2, tagged self-termination, EC2 describes and unique
   per-node status alarms. Confirm the CloudFormation service role can launch the
   seed with inherited user data. If a deployed boundary/SCP denies this contract,
   prepare a narrowly scoped policy change for separate review; never broaden
   IAM merely to pass deployment. No policy files were expanded by this change.
6. Review account vCPU headroom, subnet capacity and the cost of retained leaves
   and per-node alarms. These are pre-run review steps, not distributed launch
   admission gates. Keep max_generation=2 for the initial test. A future depth-9
   trial requires its own authorization and capacity/cost review.
7. Prepare the new offline CONTROL configuration and request START while observing
   the cycle. Measure launch/startup/readiness/handoff/retirement times and retries,
   verify exactly two children per interior node and four retained terminal leaves,
   exercise asynchronous stop/HOLD and failure paths, then explicitly CLEANUP.
   Verify instances, root volumes, alarms, seed stack and generation state settle
   before accepting the release.

## Reconciliation

An ambiguous child launch retains SUBMISSION with settled=false. Reconcile the
exact deterministic client token, cycle, lineage, pinned template and instance.
The parent restores inventory and its alarm before marking it settled. Missing
or multiple matching instances preserve the intent and require inspection; do
not delete it to force a new launch. Accepted bookkeeping can finish during
QUIESCING, but cannot write after DELETING/VERIFYING. Cleanup reports an unresolved
submission after its bounded wait and preserves the evidence.

Tests exercise the actual FamilyDaemon with fake AWS gateways and SDK request
models. They do not prove live IAM authorization, real EC2 latency, image boot,
or service-level DynamoDB concurrency behavior. The earlier offline trial remains
a scheduling experiment and is not the deployed agent.

## Source verification — 2026-10-06

The SDK-installed suite passed all 333 tests with no skips. Repository safety
validation, bootstrap renderer consistency and diff whitespace checks passed.
CloudFormation lint passed for permission boundaries, network, billing,
foundation, legacy generation, bootstrap, EC2 seed and launch-template sources.
The deterministic candidate archive built locally with SHA-256
`e567ff1014ac7c872276da364bc59dca3e494ecda9aebf93ec347bdc29664f04`.
No AMI was built, no artifacts uploaded, no permissions changed in AWS, and no
live propagation or cold-boot test was run. Source tests do not establish live
IAM authorization, AWS concurrency behavior or propagation performance.

## Live deployment and first binary trial — 2026-10-06

Deployed private ARM64 AMI `ami-0336aa2a14014e35f`, source commit
`6288931b99fab6363e4f391c777709a08f5249ce`, daemon archive SHA-256
`c79c25d22093134c54fd1731bd08c4edbc20dfc5a3f9a3cf9a1ffe92d01da95c`,
and numeric launch-template version 10. Cold smoke passed its explicit signal
and console PASS checks; its stack and instance were cleaned up. All four
deployment stacks reached UPDATE_COMPLETE. Inherited configuration and binary
fan-out were enabled for the new cycle at max_generation=2.

The operator explicitly approved CreatePolicyVersion and DeletePolicyVersion
for GliderManager on only the three sandbox boundary policies. Runtime daemon
permissions were not expanded. Boundary rollback recovered without resource skips.

Cycle 19 launched exactly seven instances with paths r, r0, r1, r00, r01, r10,
and r11. The root retired; r00 and r01 reached LEAF. Node r0 then reported
INSTANCE_IDENTITY_MISMATCH and emergency hold latched descendant STOP records.
This trial does not establish a successful complete binary handoff cycle.
STOP was applied and cleanup requested; all seven instances were confirmed
terminated. Preserve the hold until the identity failure is inspected.

Raw evidence and complete collected timing validation are under ignored
`.artifacts/family-rollout/cycle-19/`. Bootstrap START initially remained pending
while its enabled stream mapping reported OK, then processed normally. A direct
synthetic diagnostic replay was rejected by automatic approval review and never
executed. No diagnostic bypass was used.

## Follow-up candidate

Terminated parents are validated against exact cycle/configuration retirement receipts and retained lineage tags; live parents still require full launch identity. Family hold requests use asynchronous Lambda Event invocation and require HTTP 202 acceptance. Identity errors name mismatched fields and are logged before stopping. The next binary test is depth 3 (15 nodes), explicitly held until the audit-separation AWS rollout completes and the releases are reconciled. No depth-3 configuration or launch is applied during the AMI build.

### Audit separation integration

The family branch is rebased onto audit separation commit
`88c0d42b05ad75a4cacfc234c99d1e3158568505`. Bootstrap includes the audit
destination in the inherited configuration digest. Each daemon requires that
destination to match its configured audit table; its asynchronous control monitor
latches stop when fresh CONTROL names a different destination. The terminated
parent receipt validation and asynchronous emergency-hold fixes are preserved.
AMI `ami-020e1d14a1a081269` predates this integration and must not replace the
audit-separated deployment. Build and cold-smoke a new immutable image before
coordinating release pins or starting the authorized depth-3 family trial.

### Audit-compatible depth-3 live validation — 2026-10-06 Pacific

After the audit separation AWS rollout completed, source commit
`a76063076c9cd4a85311f32a24b85d682b3dc7d0` combined that implementation with
inherited families and the terminated-parent/asynchronous-hold fixes. All 407
Python tests, repository checks, bootstrap rendering and CloudFormation template
validation passed. The immutable daemon artifact digest is
`d2d16fd54e330f959225b8bda47b507df46b44b204c3133ed42eb7808dce7276`.

AMI `ami-0e287059866c01bac` passed metadata and isolated guest cold-boot smoke
validation, then was deployed through coordinated image permissions, bootstrap,
launch-template version 12 and CONTROL pins. Release templates preserved the
already deployed explicit IAM permissions and audit resources; they did not
apply the older instance-profile wildcard from the rebased template. The exact
release templates and change-set reviews are retained in local artifacts.

The inspected cycle-19 hold was cleared with an exact-record conditional operator
recovery transaction after checking the deployed fix, idle state and cold-smoke
result. Normal stream delivery processed START for cycle 20 at depth 3. All 15
expected nodes launched, all seven parents terminated after handoff, and eight
terminal leaves reached LEAF. No new identity mismatch, generation error or hold
occurred. Root EC2 launch to the last leaf handoff took 147 seconds in this single
trial; this excludes the operator START stream-delivery delay and is not a
repeated performance benchmark.

Normal STOP disabled propagation and all eight leaves published durable stop
records. Timing validation captured 2,313 records from all 15 producers with
complete collection. Normal cleanup terminated every instance, deleted the seed
stack and remaining volumes, and removed generation records. Resource cleanup
finished before the operator busy flag acknowledged completion; an additional
idempotent STOP through the normal stream path reconciled that flag. This
acknowledgement issue remains an implementation follow-up.

Final state: propagation disabled, CURRENT UNINITIALIZED, lifecycle READY,
cleanup COMPLETE, CONTROL command NONE, next request 21, no hold, no generation
records and no live Glider instances. The retained binary depth setting is 3.
Evidence is under ignored `.artifacts/family-audit/`, especially `completion.json`
and `cycle-20/`; no diagnostic Lambda replay was used.

### Atomic cleanup command acknowledgement

Cleanup now clears its matching CONTROL command in the same conditional
DynamoDB transaction that resets CURRENT and advances the lifecycle cycle.
Stream-driven completion therefore needs no extra STOP to clear the busy flag.
The acknowledgement preserves newer operator feedback, never clears another
cycle's command, and fails atomically if an operator changes CONTROL concurrently.
This is a bootstrap Lambda source/template change; daemon artifacts and baked
AMI contents are unchanged. Deploy the updated bootstrap stack to activate it.

### Depth-4 throttling and Observer repair

Cycle 21 (generations 0–4, 31 intended launches) stopped after 29 actual launches.
EC2 throttled requests; lifecycle retry sleeps starved the tick-driven control
monitor for longer than its unchanged 15-second freshness bound. Local STOP
latched correctly, but that expiry was avoidable. Control polling now has its own
scheduler and remains active during backoff and blocking launch calls. Explicit
RunInstances RequestLimitExceeded responses record a cycle-fenced rejection;
retry first reconciles the exact token and clears that receipt before repeating
the same request. Transport errors or other ambiguous responses never authorize
resubmission. Failed receipt recording also preserves ambiguity.

First functional-readiness time is retained separately from its five-second
refresh. NODE records retain startup, handoff, leaf and retirement timestamps.
Family cycle durations are exported through the existing bounded timing channel.
Observer joins family records independently of scan/stream arrival order, shows
accepted family ownership, functional proof, stop and retirement states, and
uses EC2 launch time with retained first-readiness time for intervals. Old images
lack first-readiness timestamps; missing historical timings are not invented.

Cleanup also needs a durable record of physical termination: EC2 eventually stops
returning terminated instances. A successor now stores a cycle-fenced RESOURCE
receipt only after exact EC2 TERMINATED state and the accepted parent retirement
identity have been verified. The receipt preserves the first observation time
and evidence digest. Cleanup accepts an absent exact ID only with that receipt,
matching cycle and launch-template pins. A present instance still requires exact
ownership validation; API authorization, throttling and transport errors never
count as absence. Historical cycles without receipts require an explicit operator
recovery using retained physical-termination evidence before normal cleanup can
continue. STOP alone preserves running instances; CLEANUP terminates them.

The operator submission-reconciliation supplement in
`iam/operator-family-cleanup-reconciliation.json` is restricted to generation
keys and submission receipt attributes. It does not permit termination receipts,
NODE ownership updates, or CONTROL changes.

Cycle 21 cleanup was subsequently completed through the normal controller path
after operator recovery recorded the twelve exact, previously observed physical
terminations. The remaining seventeen instances were terminated. Verification
found no surviving generation instances, generation stack, sandbox volumes,
generation alarms, or generation inventory records. BOOTSTRAP advanced to request
22 with READY/COMPLETE, CONTROL acknowledged NONE/COMPLETE, and propagation stayed
disabled. Both in-progress candidate image builds were canceled with Packer's
normal cleanup; this repair has not yet completed a fresh 31-instance live trial.
Local validation passed 420 Python tests, the deterministic Observer UI check,
repository safety checks, generated bootstrap consistency and CloudFormation lint.
