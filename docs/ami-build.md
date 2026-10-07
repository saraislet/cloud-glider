# Build the current-contract minimal AMI

Build from a clean committed reconciliation branch. The versioned source includes
the reviewed boot/EFI fixes from the existing AMI build checkout and the local
Packer plugin patches; no changes are made to that checkout. Use the dedicated
`glider-image-build` profile, existing builder instance profile, zero-ingress
sandbox subnet/security group, pinned Canonical source image and SSM checksum.

Run `scripts/build_daemon_artifact.py`, record its archive SHA-256, and fill the
ignored `.artifacts/ami/build.pkrvars.json` with the full source commit and archive
path. Install/verify the local patched plugin using
`scripts/install_packer_amazon.py` and `ami/packer-plugin/README.md` when needed.
Then run Packer init, validate and build with cleanup on error. Preserve build
logs, the Packer manifest and the exact inputs with the release evidence.

The candidate contains the current request/table lifecycle daemon, pinned SDK
packages in its venv, integrity manifest, verifier and disabled systemd service.
It is private, ARM64, UEFI, IMDSv2, encrypted and has a 2 GiB gp3 root. It contains
no AWS CLI, credentials or generation state. Review metadata with
`scripts/validate_baked_ami.py`; this is validation, not approval to propagate.

Deploy `cfn/ami-smoke-test.yaml` using the exact candidate ID and archive digest
through the operator CloudFormation path. It launches one t4g.micro with no
instance profile or propagation configuration. Inspect console output for the
smoke PASS and absence of FAIL; stack creation alone is insufficient. The helper
checks integrity, pinned SDK versions, current request/table/delivery contract,
disabled daemon, empty state and at least 384 MiB free space. Successful tests
shut down the instance. Collect evidence and delete the smoke stack through
CloudFormation to end storage charges. Preserve the AMI and its root snapshot.

Complete the [verified cleanup and AMI retention checklist](trial-cleanup-and-ami-retention.md)
after each smoke trial. Keep only the deployed image, an optional compatible
rollback and one next-release candidate; reconcile references and preserve
receipts before retiring superseded image/snapshot pairs.

A rebuilt image alone does not update deployed controller/schema or CONTROL
approvals. Every build must finish the coordinated sandbox release below; an
available, smoke-tested candidate is still an incomplete build task.

## Mandatory sandbox release completion

The operator requires every future AMI build to leave the sandbox immediately
operable for propagation. No separate request to deploy the candidate is needed
within that build task. Leave propagation disabled and do not start a trial as
part of release completion.

1. Verify idle, cleaned lifecycle, uninitialized CURRENT, absent HOLD, no pending
   operator command and no in-flight or surviving generation instances. Stop and
   clean a live cycle before changing its pins; never change a running cycle.
2. Build, validate metadata, capture explicit cold guest smoke PASS for the exact
   AMI/digest, and verify smoke stack, instance and volume cleanup. Preserve all
   private receipts. Packer's `approval=candidate` manifest is intermediate.
3. Publish immutable versioned artifacts. Review and deploy the sandbox boundary
   image allow-list through SecurityAdmin, foundation image permissions through
   Release, current bootstrap code/configuration and the pinned numeric launch
   template. Preserve unrelated live IAM and audit configuration; do not blindly
   replace live stacks from an older checkout. Wait for successful completion.
4. While the stream trigger is paused, conditionally update the idle CONTROL and
   BOOTSTRAP fingerprint to the same release, exact versioned artifact tuples,
   digests and numeric launch-template version. Preserve the approved generation
   bound. Restore the normal stream listener; keep lifecycle propagation false.
5. Run the read-only completion gate and retain its owner-only receipt:

```sh
python scripts/verify_sandbox_release.py \
  --profile glider-observe \
  --image-id AMI_ID --daemon-sha256 ARCHIVE_SHA256 --source-commit FULL_COMMIT \
  --smoke-stack-id DELETED_SMOKE_STACK_ARN \
  --smoke-console .artifacts/release/smoke-console.txt \
  --receipt .artifacts/release/propagation-ready.json
```

Use the deleted smoke stack's full ARN, not its reusable name. The gate checks
actual enabled/healthy listener state, completed image pins in all four stacks,
CONTROL/bootstrap fingerprint agreement, exact launch-template configuration,
metadata, explicit guest proof, deleted smoke stack and empty live inventory.
Its result is a point-in-time readiness receipt, not a guarantee against later
changes. Run it again immediately before START. Permission denial or absent
proof fails completion; never substitute stack success for guest proof.

The supplemental policies `iam/security-admin-boundary-release.json` and
`iam/observer-bootstrap-listener-read.json` record the narrow live permission
repairs. They are separate inline policies alongside the identity stack's
managed inline policy. Keep them in reconciled identity deployments; the
listener read grant names the exact UUID and must be updated if it is replaced.
Neither policy grants propagation or arbitrary stack access.

For inherited family cycles, the supported switch is CONTROL `start_requested`
(or `scripts/lifecycle.py start --apply` / Observer START). The controller pins
the approved configuration and enables the new cycle. A bare legacy
BOOTSTRAP `propagation_enabled` toggle does not bootstrap an inherited family.
With a passing receipt, START requires no AMI rebuild or release deployment.
Follow [the coordinated release runbook](minimal-ami.md) for deployment context.

## Bounded-root build workspace

The October 2 rebuild encountered target-disk exhaustion while generating an
initramfs after Ubuntu installed a newer AWS kernel alongside the source kernel.
The builder now verifies and retains the selected `/boot/vmlinuz` AWS kernel
and its modules, removes obsolete kernels and build headers, and bind-mounts a
builder-root scratch directory over the target's `/var/tmp` during initramfs
construction. The target remains 2 GiB and the 384 MiB final/boot headroom checks
remain required. Build failure creates no approved image; Packer cleans up the
builder with `-on-error=cleanup`. The selected kernel and package inventory are
recorded by the normal image manifest/package evidence.

## Private trial automation

Use the implemented `scripts/trial_receipt.py preflight`, `capture`, and `verify`
commands in [the trial evidence runbook](trial-cleanup-and-ami-retention.md#implemented-private-evidence-commands).
Capture exact resource IDs and evidence before supported cleanup, then verify
fresh resource/state readback afterward. STOP alone is not cleanup. The tool
performs read-only AWS calls, preserves owner-only ignored receipts, and leaves
missing evidence, incomplete cleanup and retained exceptions explicit. Live
trials, cleanup writes, deployment and AMI retention changes still require their
existing operator authorization; the tool adds no scheduled deletion workflow.
