# Decision 0022: generation-state Streams for the local observer

Status: approved by the operator October 3, 2026.

Enable NEW_AND_OLD_IMAGES on the existing generation-state DynamoDB table through
its owning foundation CloudFormation stack. The control-table stream is already
enabled. The local observer consumes both streams using its existing operator
profile; the previously approved observer policy covers their exact stream ARNs.
No runtime agent role, lifecycle operation, instance limit, bootstrap state,
propagation switch, emergency-hold trigger, or deployment workflow changes.

Snapshots bootstrap/reconcile current state; Streams capture intermediate record
changes between polls. Event IDs deduplicate records and SQLite transactions
commit records and shard checkpoints together. Streams retain records for only
24 hours; a stopped collector cannot promise continuous or permanent history.
Expired checkpoints produce a gap notice and retained-record recovery. Source
record timing and local observation timing must remain distinguishable.

This change is for event fidelity; cost savings have not been measured. No extra
cloud consumer, database, Lambda subscription, or always-on cloud service is
created. The observer remains read-only; enabling the stream is an operator
CloudFormation configuration update, not an observer runtime capability.

The foundation deployment role required a separately approved supplement for
`dynamodb:UpdateTable` on the generation table only. Its existing permissions
boundary is unchanged. This API can alter other table settings; the deployed
change set is reviewed to contain only StreamSpecification. The first denied
update rolled back without changing the table before the approved retry.
