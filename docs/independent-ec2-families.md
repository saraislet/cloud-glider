# Independent inherited EC2 propagation

Source implementation only. This runbook covers new version-3 EC2 family cycles;
it does not authorize live deployment or a larger propagation test. Architectural
changes and preserved gates are recorded in [decision 0023](decisions/0023-independent-inherited-ec2-families.md).

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

None of these steps has been executed for this source change.

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
