# Durable timing collection rollout

Status on 2026-10-05: source merged, AMI built, isolated cold-boot/log-delivery smoke passed, and selectors deployed. The ten-generation diagnostic run and cleanup are complete.

PRs 19 and 20 were squash-merged in that order. The AMI retains runtime source
`88c653edc44f1ffe9bc1f52d96a7a394460a34ac` and daemon archive SHA-256
`a71035d5880ed3ce69cc0081ebf4eb4a51be8c9056785b7981c9cfc1ac2934ca`.
Runtime, AMI and CloudFormation files on merged main match that build source;
the squash revision does not replace the baked identity.

The corrected build completed with a private ARM64/UEFI/IMDSv2 image and one
completed encrypted 2 GiB gp3 root snapshot. Root headroom was 632,987,648 bytes,
above the 402,653,184-byte minimum. These checks do not prove cold boot or log
delivery. The candidate kernel is `7.0.0-1014-aws`; do not attribute differences
from an older image solely to the collector.

Validation: 341 tests passed; the final failed-start identity correction passed
the 13-test exporter suite. Seven relevant CloudFormation templates and repository
safety checks passed. Failed verification remains fail-closed. The independent
failed-start helper obtains cycle/generation tags through a bounded read of its
own EC2 instance, without importing the failed daemon package. Collection adds no
runtime IAM permissions or lifecycle control/readiness/retirement dependency.
See [collection contract and initial overhead](boot-api-timing.md).

The operator accepts partial collection for the initial ten-generation rerun.
After an isolated actual-entrypoint smoke demonstrates that timing records reach
the existing log group, run generations 0–9 with `max_generation=9` and the existing
three-live ceiling. Report each duration's observed sample count, coverage, available
receipt/loss counters and possible selection bias; missing durations remain missing.
Complete receipts are an observation, not a prerequisite for that authorized run.
Use supported stop/cleanup and retain the ten-total-generation limit afterward.

The existing administrator session was restored. All four release selector stacks
completed, followed by conditional idle CONTROL/BOOTSTRAP pin updates. The deployed
AMI is `ami-0d79bff01a0f1a1db`; numeric launch-template version is **6**, with data
SHA-256 `485051b01d45b7dd4f1d2566f1373cd8054d964a0ed2559c4c2eb3cb69bab486`.
Readback verified image, artifact, seed, network, profile, boundary and state agreement.
Bootstrap delivery was restored through a reviewed trigger-only CloudFormation update.

The corrected isolated smoke ran normal runtime integrity/contract checks before
creating its disposable bootstrap configuration. The first harness attempt reversed
that order and failed the pristine-image check; its instance was removed. The corrected
smoke passed without an image rebuild. Fresh post-termination CloudWatch retrieval
validated 11 timing records plus one complete receipt, including both verifier contexts,
early imports, SDK initialization and the actual own-instance EC2 read.

One same-instance enabled/disabled smoke pair measured entry-to-exit 1.202140/0.936687s
and process CPU 0.943600/0.680384s. Enabled shutdown drain was 0.095129s. Ordering,
caches and one-pair sampling limit attribution; the collector is not claimed to have
zero overhead. This does not measure lifecycle retirement delay.

Both builders and disposable smoke instances were terminated, with temporary builder
volumes/key pairs absent. The release image and snapshot are retained. Run 16 uses the
supported lifecycle start, generations 0–9 and the existing three-live ceiling. The
read-only Observer is collecting; direct AWS/DynamoDB snapshots supply endpoint evidence.

Run F (cycle 16) completed ten generations and nine handoffs. Post-termination
retrieval validated all expected producer identities, boot contexts, sequences and
digests: **1,930 timing records, ten complete receipts, zero reported dropped records
or upload errors**. Complete receipts cover emitted records; they do not prove that
every possible phase ran or that logging can never fail. Partial collection remains
acceptable for later trials.

All ten instances are terminated; generation volumes/records, seed stack, holds,
locks and cleanup schedules are absent. The request is READY/COMPLETE with propagation
disabled and `max_generation=9` retained. Supported stop refreshed stale controller
DELETING feedback after cleanup reached READY, without manual state correction.
Release pins were reverified. See [measured averages](performance-log.md#2026-10-05-durable-timing-diagnostic-run)
and the [sanitized receipt](../config/releases/2026-10-05-durable-timing-diagnostic.json).
