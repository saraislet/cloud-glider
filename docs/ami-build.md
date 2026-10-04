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

A rebuilt image does not update deployed controller/schema or CONTROL approvals.
Follow [the coordinated release runbook](minimal-ami.md) after validation.

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
