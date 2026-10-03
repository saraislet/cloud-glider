# 0020: Bounded EC2 launch and retirement overlap

Status: accepted at the operator's request; implemented and validated by a
separately authorized deployment and bounded benchmark on October 3, 2026.

Supersedes decision 0019's serialized launch rule for EC2. Decisions 0005/0006
inform this overlap, but EC2 keeps its continuation DryRun and health gates.
Launch and boot may overlap accepted predecessor termination. CURRENT remains
with the immediate successor until its predecessor is exactly `terminated`.
Only then may the booted candidate pass fresh readiness, continuation and
conditional handoff. This bounds recovery without an ancestor queue: CURRENT
is the durable retirement intent and no handoff can overwrite it prematurely.

Termination failures or ambiguous responses block launch until retry or an
exact shutting-down observation resolves acceptance. Stop/HOLD blocks every
new submission. No process relies on its retiring parent for recovery. Retries
reconcile the existing cycle-fenced candidate, retaining deterministic tokens
and provisioning markers. A terminated predecessor permits alarm cleanup and
conditional retirement completion; absence never substitutes for termination.

Admission under the lease includes exact CURRENT and pending predecessor
identities plus filtered EC2 inventory. Shutting-down instances occupy slots.
Three is the absolute ceiling; two forces waiting before candidate launch.
At three occupied slots no further launch is possible, and the candidate cannot
own the chain until the older retirement finishes. Terminal owners reconcile
retirement before exiting. Runtime IAM, infrastructure and backend selection do
not change. A current-owner process failure requires service restart or operator
recovery; this change does not introduce autonomous general cleanup.

The serialized benchmark reported 544.8 seconds and 56.4 seconds mean ownership
interval versus the earlier CloudFormation 405.991 and 39.481 seconds. Different
releases and single trials cannot isolate backend causality. The separately
authorized release at `04bc90f` has now completed a
ten-generation live test with sampled overlap, nine handoffs and full cleanup.
See the [timing log](../performance-log.md) and
[release receipt](../../config/releases/2026-10-03-ec2-overlap-minimal-ami.json)
for the measured comparison and single-trial limits.
