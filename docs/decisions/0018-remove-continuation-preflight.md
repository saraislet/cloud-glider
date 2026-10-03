# Decision 0018: remove the separate continuation preflight

The operator explicitly chose removal of the N+2 CloudFormation CREATE change
set instead of repairing placeholder cleanup. A benchmark preview left a
parameterless REVIEW_IN_PROGRESS stack, which conflicted with deterministic
successor reconciliation and triggered an emergency hold.

Real N+1 creation and verification supply the required evidence before handoff.
Remove the preview creation, polling and discard path entirely. Revalidate the
real successor's exact stack ID, approved parameters, designated service role,
CREATE_COMPLETE status, instance identity and fresh eligible workload heartbeat
immediately before conditional ownership transfer. Fresh control and the
transactional hold/lease/CURRENT conditions remain authoritative. Missing or
ambiguous evidence preserves the predecessor.

This supersedes the continuation requirements of decisions 0001 and 0005 and
all of decision 0006. There is no advance proof that N+2 can launch: if its
actual creation later fails, its predecessor N+1 remains available. Generation
limits, actual-create capacity/quota checks, deterministic retries, cleanup and
the absolute three-live-generation ceiling remain unchanged.

The persisted `PREFLIGHT_THEN_RETIRE` concurrency-model value is retained as a
legacy compatibility identifier, not a requirement to create previews. Existing
IAM change-set permissions are unchanged; permission narrowing is separate
operator release work. No infrastructure or recurring-cost increase is added.
Historical preview inventory remains readable by lifecycle cleanup.

Apply through a new immutable artifact and rebuilt baked AMI; existing images
retain the old implementation. No deployment or benchmark is part of this PR.
