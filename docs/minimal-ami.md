# Minimal AMI integration and deployed-release reconciliation

Generation and bootstrap templates support `AgentDeliveryMode=baked`,
`RootDeviceName=/dev/sda1` and `RootVolumeGiB=2`. Baked boot writes the current
request/generation configuration, verifies the installed release, and starts
its existing systemd service. It does not download or overwrite the agent,
install packages, or replace the baked unit. A missing manifest or failed
integrity verification prevents startup. S3 mode rejects a baked image.
Both templates require baked mode for roots below 8 GiB.

The agent carries the AMI, delivery mode, root device and size through successor
creation. RequestId,
GenerationTableName, lifecycle fencing, cross-table handoff and operator
controls remain part of the current contract. No IAM or security-policy
expansion is included in this source reconciliation.

## Current-source release requirements

The approved image must contain an archive built from this branch/current
contract, including AgentDeliveryMode, RequestId, GenerationTableName and the
SDK transport. Bake the pinned `agent/requirements.txt` into the service's
`/opt/cloud-glider/venv` interpreter. The image must install the verifier at
`/usr/local/lib/cloud-glider/verify_image.py`, the unit supplied in
`ami/files/cloud-glider.service`, and a manifest at
`/etc/cloud-glider/image.json` containing `agent_sha256` and hashes of the
installed release files. Do not include credentials or generation state.

The reconciled repository includes the Packer recipe and the boot-copy fixes.
Build an archive from the current lifecycle source, then bake that exact archive.
The 2 GiB target uses builder scratch space for initramfs generation and removes
obsolete kernels and build headers while preserving the selected AWS kernel.
Metadata validation and an isolated cold-boot test gate release deployment.
The smoke stack reports explicit runtime evidence through a scoped CloudFormation
wait-condition callback; it has no instance profile or propagation configuration.

Upload the same baked archive and corresponding generation template as immutable
versions. Review their hashes and version IDs with the image and root snapshot.
Update the exact image/template approval parameters through CloudFormation and
bootstrap's baked mode and 2 GiB root settings. Preserve the current lifecycle
controller, schema-2 request and separate generation table. Conditionally update
the approved control artifacts and request fingerprint only while provisioning
is idle, CURRENT is uninitialized, requests are disabled and no hold or lease exists.
Keep propagation disabled after deployment. An observed bounded propagation run
is a separate operational validation; cold-boot success alone does not prove handoff.

## EC2 overlap release on October 3, 2026

The current tested release uses private ARM64 AMI `ami-0dd526b0623ff67a3` and encrypted
2 GiB snapshot `snap-011e5b3e4f10a0106`, baked from source `04bc90f` with archive
SHA-256 `03d6d617574a4d82ec3cd5307eed69e42029562cc7fdff10c3df05ff11063203`.
Its isolated cold boot passed both runtime and EC2
contract checks with Python 3.12.3 and 632,483,840 free bytes.
All 262 tests and template/repository checks passed. The reviewed image-only
runtime permission substitutions preserve the exact agent profile restriction.
Launch Template version 2 and immutable archive pins are recorded in the
[release receipt](../config/releases/2026-10-03-ec2-overlap-minimal-ami.json).

The supervised ten-generation run passed nine handoffs with no HOLD and a
sampled peak of three. Candidate boot overlapped accepted predecessor retirement,
while ownership waited for termination. Supported cleanup removed all test
instances, disks, alarms, seed stack and cycle records. The image/snapshot and
persistent Launch Template remain available. Propagation/bootstrap are disabled,
CURRENT is uninitialized, max_generation is 10 and max_live_generations is 3.
Only documentation/receipt changes followed the bake; they do not require another
AMI build. See the [measured comparison](performance-log.md) and its limits.

## Current-source rebuild on October 3, 2026

[Release receipt](../config/releases/2026-10-03-current-minimal-ami.json):
`ami-081b2ebbf3d760365`, encrypted 2 GiB snapshot `snap-0ad13fad7806f969d`,
baked archive from commit `932096b584a275940b296e91fe49ebb58d692375`.
Metadata checks passed. The isolated cold boot returned
`CLOUD_GLIDER_AMI_SMOKE_PASS`, the matching archive hash, Python 3.12.3 and
632,647,680 free bytes. Both smoke instances were terminated through
CloudFormation, with no attached volumes remaining. All 212 tests passed.
A live propagation/handoff run has not been performed with this image.

The exact archive, generation template and bootstrap template were uploaded,
version-pinned, downloaded and verified. The initial CLI deployment was denied
because GliderManager lacks policy-version administration; its failed boundary
update was rolled back with the effective policies unchanged. The operator then
authorized deployment through the signed-in AWS browser session.

The boundary, foundation and bootstrap stacks all reached UPDATE_COMPLETE.
The reviewed IAM changes substitute only the exact image ARN and immutable
generation template URL, preserving actions, principals and resource patterns.
Browser verification confirmed the deployed Lambda configuration uses the new
image, baked delivery, 2 GiB root and separate generation table.

A conditional DynamoDB transaction updated the approved artifact tuple and READY
request fingerprint, retained the previous control/request in the release audit,
and preserved request ID 1, CURRENT=UNINITIALIZED, max_generation=2 and
max_live_generations=3. It required exact prior records, absent hold/leases and
idle provisioning. Independent verification confirmed the fingerprint matches
`scripts/request_bootstrap.py`. Propagation and bootstrap remain disabled, and
no generation was launched. Use the approved start operation only when ready to
observe a bounded handoff test.

## Legacy release actually deployed on October 2, 2026

The prior task ran in `saraislet/glider` rather than `saraislet/cloud-glider`.
It deployed the old generation/bootstrap contract using the SDK archive from
the AMI build checkout. This reconciliation records that fact without treating
those deployed objects as a current-main release.

[The historical receipt](../config/releases/2026-10-02-legacy-minimal-ami.json)
records the AMI (`ami-027263770a87f2ce1`), root snapshot, exact archive/template
hashes and S3 versions, and release audit key. The archive digest is
`44a1ba87ff77acd4ca9a7d770bd4085e1fc43e50db3c6bf363a8f25ec8f81522`.
That image predates RequestId and GenerationTableName and is incompatible with
the current generation configuration. Its smoke-test success does not establish
current lifecycle compatibility. Keep its recorded release identities as history;
do not approve a rebuilt archive digest for that unchanged image.

The foundation update replaced only the allowed AMI ARN in the existing
CloudFormation generation-service role. The bootstrap update changed only its
generation environment parameters. Both reached UPDATE_COMPLETE, and uploaded
artifact versions were downloaded and hash-checked. The operator authorized
CloudFormation deletion of generation 10, including its instance/root/alarm.
No live generation stacks or instances remained at verification.

A conditional transaction archived the previous control/current/bootstrap and
11 generation records into the release audit item before resetting CURRENT and
freeing reusable keys. It required the expected records, disabled propagation,
absent hold/lease, and confirmed deletion of all historical stack identities.
The fresh legacy request was prepared READY with bootstrap_requested=false;
propagation remained false and max_generation=2. No new chain was launched.

The deployment credentials denied lambda:GetFunctionConfiguration; stack
parameters, property-level change contexts and UPDATE_COMPLETE were verified.
The subsequent current-source release must use the migrated lifecycle controller
and a rebuilt compatible image; the legacy receipt remains historical evidence.

## Preflight-removal rebuild and benchmark on October 3, 2026

[Release receipt](../config/releases/2026-10-03-preflight-removal-minimal-ami.json)
records private ARM64 AMI `ami-0be3b7fd3c68a61f6`, encrypted 2 GiB gp3 root
snapshot `snap-00925dc8f83f6339b`, and merged fixed source
`ee59e7d206c003634ff4329833c7fc724a16dfa6` ([PR 9](https://github.com/saraislet/cloud-glider/pull/9)).
The exact baked archive digest is
`63ac10d5da88143c66967f41156b00954c5a2274a03fd63a696f3af063c98838`.
Packer completed in 14m 17s using the pinned source AMI, SSM release and patched
plugin. Metadata validation and isolated cold boot passed; Python was 3.12.3
and free root space was 632,659,968 bytes. All 211 source tests passed.
Builder and smoke instances were terminated and the smoke stack deleted.

The operator explicitly authorized deployment and the ten-generation benchmark.
Immutable agent/generation/bootstrap versions were uploaded and downloaded for
hash verification. The existing templates were retained; release change sets
substituted only the exact image and generation-template URL, with no resource
replacement or permission expansion. Boundary, foundation and bootstrap stacks
reached UPDATE_COMPLETE. The conditional state release required exact disabled
records, uninitialized CURRENT, no live generations, idle provisioning and the
existing hold; its artifact tuple and canonical fingerprint were independently
verified. The hold was then cleared through the audited recovery helper after
confirming the stranded placeholder and prior generation resources were absent.

Request 2 ran generations 0–9 on the new image: ten launches and nine validated
handoffs, with no hold or failed generation. Generation 0 creation-to-ownership
was 50.662s; first creation to generation 9 ownership was 405.991s (6m 46s).
Ownership intervals averaged 39.481s (range 38.555–42.458s). The observed peak
was three live generations, counting pending and shutting-down instances.
Sampling was about 3.3s, so peak observation does not prove continuous absence
of a shorter spike. The receipt contains per-generation creation/ownership
timestamps, stack/instance identities and local evidence hashes. These timings
exclude AMI build, release deployment and benchmark cleanup.

Standard lifecycle cleanup fenced propagation and deleted all ten generation
stacks. Independent checks confirmed zero live Cloud Glider instances, no
attached build/test volumes, CURRENT=UNINITIALIZED, absent hold and provisioning
leases, propagation/bootstrap disabled, cleanup COMPLETE, and fresh request 3.
A conditional transaction restored max_generation=2, retained the absolute
ceiling of three and updated the request fingerprint and benchmark audit. The
verified image remains deployed but paused; no further deployment is needed.

## EC2-integrated candidate: isolated verification

[Candidate receipt](../config/releases/2026-10-03-ec2-integrated-minimal-ami.json)
records private image `ami-01072534d8819af9f` and encrypted 2 GiB root snapshot
`snap-02226e5e1058469fb`. It was built from `e027317`, whose source tree exactly
matches merged EC2 backend commit `24212a5` (PR #11). The agent archive SHA-256
is `8015a49ed4a6bd96c2b770d6a2cb9c14c52433c67fa5725caefa3bb2bd66ff94`.
The existing build completed in 686s and was reused after confirming source
equivalence; no additional build was necessary after the squash merge.

Metadata checks passed. A fresh isolated `t4g.micro`, with no instance profile
or propagation configuration, returned explicit `CLOUD_GLIDER_AMI_SMOKE_PASS`
and `EC2_BAKED_CONTRACT_PASS` through its CloudFormation WaitCondition. It
verified the baked archive, installed SDK request contract, disabled service,
Python 3.12.3 and 632,487,936 free bytes after boot. The console API returned
no output; the explicit WaitCondition payload is the retained boot evidence.
All 256 tests passed, along with affected-template linting, repository checks
and rendered-bootstrap verification.

Both build and smoke instances were confirmed terminated, the smoke stack
reached `DELETE_COMPLETE`, exact target and smoke root volumes were deleted,
and no temporary key pair, active builder session or attached volume remained.
The new AMI and root snapshot are retained as a private candidate. Lifecycle
state stayed unchanged: request 3, propagation disabled, max_generation 2,
CURRENT uninitialized, and no HOLD or provisioning/propagation lock.

The subsequent authorized deployment pinned this image to Launch Template
`lt-09e12d4ca882601db`, version 1. The first live seed passed startup identity
validation, claimed generation 0 and emitted healthy authoritative heartbeats.
Its successor launch was denied because the agent policy and boundary used an
instance-profile ARN without the existing `/cloud-glider/` path. That policy-only
correction does not change the baked agent or require rebuilding the AMI.
The corrected retry completed ten generations and nine handoffs, including
confirmed predecessor retirement and supported cleanup. All 257 tests and CI
passed. Propagation is disabled with max_generation 2, no live test compute
remains, and the approved image/snapshot/template are retained. The chain took
544.756s versus the earlier CloudFormation baseline's 405.991s; no speedup is
claimed. See the [timing analysis](performance-log.md),
[EC2 activation runbook](ec2-propagation.md) and release receipt for evidence.
