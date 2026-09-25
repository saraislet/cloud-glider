# Decision 0004: reduced-cost operation

Status: accepted for implementation; deployment requires a reviewed release.

## Decision

Use EC2 basic monitoring (`Monitoring: false`) and DynamoDB's AWS-owned
encryption key (`SSEEnabled: false`). The table remains encrypted at rest.
Retain the existing per-generation `StatusCheckFailed` alarm: its metric is
available every minute without paid detailed monitoring. Application readiness
continues to depend on eligible DynamoDB health and identity evidence.

Reduce each ordinary agent cycle from five DynamoDB calls to three by sharing
one CURRENT read within the initial ownership/heartbeat decision and removing
the scheduling-only control read. Do not reuse that snapshot across cycles or
at the post-lease ownership, provisioning, preflight, or handoff gates.

Only current owners that are disabled, held, or at `max_generation` use a
minimum 60-second interval. Candidates keep the configured heartbeat cadence,
so fast readiness checks still receive fresh candidate evidence. A stopped
owner can take one idle interval plus API latency to notice re-enablement.
The slow interval is reset on each cycle and does not apply to incomplete
control reads. No instance is stopped or deleted by this optimization.

Log successful cycle-result transitions instead of every identical idle cycle.
Retain error, recovery, lifecycle, and transactional audit evidence.

This increment originally retained decision 0001's AWS CLI transport.
[Decision 0007](0007-persistent-sdk-clients.md) subsequently implements persistent
SDK clients with pinned dependencies and a required reviewed artifact/AMI release.

## Cost interpretation and later fan-out design

The steady per-instance compute/IPv4/root-volume estimate becomes approximately
$10.42 per 730-hour month before requests, alarms, logs, and retained storage.
The earlier $12.52 estimate included seven paid detailed-monitoring metrics;
seven was a modeling assumption, not a measured instance metric count.

The simplified Google design document owns the future fan-out plan: 1,023
ancestors and 1,024 leaves, basic monitoring, a few aggregate metrics/alarms,
bounded creation/deletion rates, concise durable lifecycle logs, and reduced
polling. Leaves must persist healthy-completion evidence before CloudFormation
retirement; parents must distinguish completion from missing/ambiguous health.

The modeled incremental cost is $1.40 with four-minute ancestor and one-minute
leaf lifetimes, or $2.65 with eight-minute ancestor and two-minute leaf
lifetimes, with a $5 planning allowance. These are unverified targets for the
full future design, not the cost achieved by this patch. They assume reduced
request counts and shared alarms; existing per-generation alarms remain here.
The earlier monitoring-only scenario was approximately $3.38 per run.

Fan-out, aggregate alarm infrastructure, log shipping, automated leaf cleanup,
and concurrency reservations are not implemented by this decision. The
sequential single-owner model and absolute three-live-instance ceiling remain
in force. A fan-out release needs an explicit invariant revision and review.

## Release and verification

Review a foundation UPDATE change set and require no state-table replacement,
IAM permission expansion, or unrelated resource changes. Preserve PITR and
retention. Do not edit historical import/recovery snapshots to match this
new desired configuration.

Rebuild the deterministic agent tarball, upload immutable agent and generation
template versions, and approve their new hashes/identities while propagation
is disabled. Already-running agents do not acquire the new behavior from a
repository edit. Follow the bootstrap/runbook path for the next bounded test;
do not rearm an old request or reset CURRENT without reconciling live resources.

Run the lifecycle tests, repository validator, and CloudFormation linting.
In a reviewed sandbox release, measure requests per idle interval, verify
candidate health and stop/hold behavior, confirm the status alarm remains
configured, and verify AWS-owned encryption from the deployed template/table.
Keep propagation limits small. No deployment or paid experiment is authorized
merely by these estimates.

## References

- [EC2 metric availability](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/viewing_metrics_with_cloudwatch.html)
- [DynamoDB encryption keys](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/encryption.howitworks.html)
- [CloudWatch billing](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_billing.html)
