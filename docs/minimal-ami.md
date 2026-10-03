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

The existing AMI build checkout owns the Packer recipe and its pending boot-copy
fixes. Those files and uncommitted work were left intact. Build a matching
current-contract archive/image there using this reconciled source; do not
replace its image with an archive built from an older branch. Repeat image
metadata validation (`scripts/validate_baked_ami.py`), cold-boot smoke tests,
wrong-digest/file startup checks, and observed bounded propagation before
claiming a current-contract release is ready.

Upload the same baked archive and corresponding generation template as immutable
versions. Review their exact hashes and version IDs together with the image and
root snapshot. Through the existing approved CloudFormation path, update the
foundation image allowlist and bootstrap's image/mode/root parameters. Pause
operator provisioning and follow the current lifecycle migration runbook for
the separate generation table and schema-2 control/request records. Do not
replace the current controller with the legacy deployed template. Prepare a
fresh request only after verifying cleanup and reconciling old ownership.

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
This source reconciliation makes no further AWS changes. Do not apply current
main to that deployment until a coordinated schema/image migration is reviewed.
