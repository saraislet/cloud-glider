# Decision 0015: lifecycle deployment corrections

Preserve the confirmed operational email subscription by keeping its original
`TopicArn: !Ref OperationalAlertsTopic`. An equivalent constructed ARN caused
CloudFormation to plan an unnecessary replacement.

The foundation boundary must include the retained generations table and the
cleanup reconciliation rule. Give that rule the fixed name
`cloud-glider-${Environment}-cleanup-reconciliation`; CloudFormation truncates
its default generated stack prefix, making permissions based on that prefix
unreliable. Keep the expanded boundary below IAM's 6,144-character limit.

The foundation's separate cleanup schedule supplement allows only
AddPermission, RemovePermission, and GetPolicy on the bootstrap Lambda.
GliderManager's generation-read supplement permits table inspection and the
empty-table migration check; it grants no generation data writes.

A failed rollback attempted to remove the already attached bootstrap boundary.
Recovery skips only BootstrapRole, preserves that ceiling, and reapplies the
reviewed template to reconcile the role. Do not grant boundary removal to the
foundation role to work around rollback.
