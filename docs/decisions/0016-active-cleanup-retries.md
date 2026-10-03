# Decision 0016: schedule retries only for active cleanup

DynamoDB starts operator requests. Replace the permanent minute reconciliation
rule with EventBridge Scheduler one-time schedules about 60 seconds apart, only
while the same cleanup cycle is QUIESCING, DELETING, or VERIFYING.

BOOTSTRAP/REQUEST holds the pending retry token, sequence, and UTC time. Names
combine the request ID with a monotonically increasing retry sequence. Successful
processing replaces the token; duplicate or stale deliveries cannot perform work
or arm the next retry. A timeout preserves its token for bounded AWS delivery
retries. Never recreate a one-time schedule whose time has passed.

Scheduling failures mark cleanup NEEDS_ATTENTION. Completion, inspection failures,
or stale cycles schedule nothing further. Explicitly resuming an inspected cleanup
invalidates the pending token. ActionAfterCompletion=DELETE removes completed
schedules; raced completion also attempts to cancel an outstanding exact name.
There is no idle polling and no scheduled bootstrap retry.

The independent boundary stack owns the retained cleanup schedule group and a
separate bounded delivery role. That role trusts Scheduler only for this account
and group, and can invoke only the lifecycle Lambda. The Lambda can create, read,
and delete only cleanup-* schedules in that group, passing only this delivery role
to Scheduler. Foundation deployment cannot modify these security resources.

Scheduler and Lambda delivery retries are bounded; exhausted delivery can require
manual recovery from the visible cleanup status and Lambda logs. DynamoDB and
Scheduler cannot update atomically: cycle/token checks make a raced stale delivery
harmless. Preserve stack identifiers and gates whenever cleanup is ambiguous.

References: [automatic schedule deletion](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-delete.html)
and [schedule-group trust constraints](https://docs.aws.amazon.com/scheduler/latest/UserGuide/cross-service-confused-deputy-prevention.html).
