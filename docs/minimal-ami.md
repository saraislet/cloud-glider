# Minimal AMI integration and deployed-release reconciliation

Generation and bootstrap templates support `AgentDeliveryMode=baked`,
`RootDeviceName=/dev/sda1` and `RootVolumeGiB=2`. Baked boot writes the current
request/generation configuration, verifies the installed release, and starts
its existing systemd service. It does not download or overwrite the agent,
install packages, or replace the baked unit. A missing manifest or failed
integrity verification prevents startup. S3 mode rejects a baked image.
Both templates require baked mode for roots below 8 GiB.

The agent carries the AMI, delivery mode, root device and size through successor
creation and the unexecuted continuation change set. RequestId,
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
