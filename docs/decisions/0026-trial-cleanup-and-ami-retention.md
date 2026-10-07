# Decision 0026: verified trial cleanup and a small AMI retention set

Status: accepted by the operator on 2026-10-07. Manual operating policy; no
automatic deletion or new AWS infrastructure is introduced.

## Decision

Every live propagation or isolated AMI smoke trial must end with collected
evidence and verified resource cleanup. STOP alone is not cleanup. A trial is
not operationally complete while its resources are still accruing charges,
unless the operator explicitly requests retaining named resources for inspection.

Retain the deployed AMI, at most one useful compatible rollback AMI, and at most
one candidate for the next approved release. These are distinct images only
when needed; the normal set is two or three images, not a quota to fill. Do not
retain every build. A failed or superseded candidate is eligible for removal
after its evidence is collected and all active references are reconciled.

Preserve immutable build, release, trial and cleanup receipts locally even when
the corresponding AMI is retired. A historical launch-template version or
receipt is not by itself a reason to keep an image indefinitely. Record when
an old launch-template version is no longer launchable. Never remove an image
needed by current deployment pins, an active trial, another operator's planned
work, or the selected rollback path without reconciling that dependency first.

Use the manual checks in [the cleanup and retention runbook](../trial-cleanup-and-ami-retention.md).
Cleanup follows existing operator authorization and lifecycle safety gates;
this policy does not authorize unrelated deletions, concurrent interference,
new trials, release changes or removal of broad migration permissions.

## Rationale and validation

Stopped smoke instances retain billed root volumes. Retained AMI snapshots
also incur storage charges. Verification must inspect actual AWS resources,
not only lifecycle status or accepted deletion requests. Keep recovery evidence
and the current release while removing superseded artifacts deliberately.

On 2026-10-07, the operator explicitly authorized smoke-stack and six superseded
AMI/snapshot removals. The scoped SSO image-builder role retrieved the explicit
guest PASS, deleted the smoke stack and removed the six image/snapshot pairs.
Readback confirmed DELETE_COMPLETE, terminated instance, absent root volume,
and exactly two retained AMIs with their snapshots. Private receipts are under
`.artifacts/cost-review-20261007/`; IDs and account telemetry remain local.
