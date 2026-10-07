# Trial cleanup and AMI retention

Apply [decision 0026](decisions/0026-trial-cleanup-and-ami-retention.md) after
every live propagation and isolated AMI smoke trial. This is a manual operator
workflow, not an automatic cleanup service.

## Complete each trial

1. Verify the account, Region, task identity, trial/cycle identifiers and other
   operators' active work. Inventory only resources belonging to the trial.
2. Collect timing, health, identity, failure and cost-relevant instance evidence
   before removing resources. For smoke tests, save console output proving
   explicit PASS and absence of FAIL. If evidence is missing, record the trial
   as unvalidated; do not label stack creation or a stopped instance a PASS.
3. For propagation trials, use the supported STOP path, verify propagation is
   disabled, and reconcile in-flight launches before CLEANUP. Preserve instances
   when ownership or cleanup scope is ambiguous. STOP retains instances.
4. Use supported lifecycle CLEANUP for generation resources. For isolated AMI
   smoke tests, delete the exact smoke CloudFormation stack. Do not directly
   delete resources owned by a live stack to shortcut stack cleanup.
5. Verify stack deletion, actual instance termination and disappearance of
   trial-owned volumes and any temporary billable network resources. Verify
   no unresolved launches, generation records or locks; for propagation trials,
   confirm disabled propagation, reconciled CURRENT, lifecycle READY and cleanup
   COMPLETE. Retained control-plane infrastructure is not trial residue.
6. Save an owner-only cleanup receipt listing targets, identity, timestamps,
   deletion results, fresh readback and any explicitly retained exceptions.
   Report remaining billable resources or incomplete evidence. Do not mark
   cleanup complete merely because a deletion API returned successfully.

## Retain only useful images

Keep the currently deployed AMI and its root snapshot. Keep at most one
compatible, deliberately selected rollback image and at most one next-release
candidate, each with its backing snapshot. If no safe rollback or candidate
exists, do not retain an arbitrary old image just to fill that slot.

After each build/release/trial, classify images against live CONTROL pins,
bootstrap/foundation/boundary parameters, the exact numeric launch-template
version, live instances/stacks, active or planned work in other chats, and the
chosen rollback release. Check the rollback's artifact, configuration and schema
compatibility; age alone does not establish a usable rollback.

For superseded images, preserve build/test receipts, record retired historical
launch-template references, deregister the exact owned AMIs, and delete only
their owned snapshots after checking that no retained AMI shares them. Verify
the image registrations and snapshots are gone and retained image/snapshot
pairs remain available. Do not delete the pinned upstream source AMI or treat
it as one of the account-owned retention slots.

When an image is needed beyond this set, record the operator-approved reason,
owner and review point. Do not silently accumulate build candidates or apply
an age-based deletion rule to an active dependency.
