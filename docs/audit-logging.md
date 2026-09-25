# Audit logging contract

Cloud Glider keeps audit evidence distinct from diagnostic and alert data.
CloudTrail is the canonical account-level record; category log groups are
searchable projections and must not be treated as the sole evidence source.

## Canonical archive

The foundation creates a retained, versioned S3 bucket and a multi-Region trail
with global service events and log-file validation enabled. The bucket denies
non-TLS access and is not writable by either generation role.

CloudTrail records management API calls, including calls made through
CloudFormation. EventBridge delivery is best effort, so the retained CloudTrail
objects remain authoritative when a projected category stream is incomplete.

## Audit categories

| Category | Log group | Examples |
| --- | --- | --- |
| IAM and account | `/cloud-glider/{environment}/audit/iam-account` | Role/policy/profile changes, permission revocation, account and Organizations changes visible in this account |
| Networking | `/cloud-glider/{environment}/audit/network` | VPC, subnet, route, gateway, endpoint, security-group, network ACL, and address changes |
| Infrastructure | `/cloud-glider/{environment}/audit/infrastructure` | CloudFormation changes, instance lifecycle, DynamoDB/S3/logging/audit-resource changes |
| Propagation | `/cloud-glider/{environment}/audit/propagation` | Control observations, hold creation/clearance, lease changes, gate results, handoff, quiescence, retries, and predecessor retirement decisions |

`RunInstances` is projected to both infrastructure and networking audit because
the approved launch request creates the generation and its primary ENI and
assigns the ephemeral public IPv4 address. CloudTrail remains the canonical
record of the complete request parameters.

Every audit event should include:

- schema version, event ID, UTC occurrence time, category, and correlation ID
- actor/principal, source account and Region, generation when applicable
- action, affected resource type and identifier
- requested or observed before/after values with secrets removed
- result, reason, and related CloudFormation request token or stack ID

Audit records are append-only. Corrections are new events that reference the
incorrect event; existing entries are never edited.

## Separate non-audit logs

These should remain separate because their retention, volume, and responders
differ from change evidence:

1. **Agent operations** — retries, latency, SDK errors, and diagnostic output.
2. **Security findings** — IAM Access Analyzer, AWS Config, GuardDuty, Security
   Hub, and policy-validation results.
3. **Cost alerts** — Budgets, estimated charges, anomaly detection, and quota
   warnings.

CloudFormation stack status changes belong in infrastructure audit. Application
health metrics belong in operations, while the health decision used to permit a
handoff belongs in propagation audit.

## Important limitations

- SCP creation and attachment normally occurs in the AWS Organizations
  management or delegated-administrator account. A trail in the Cloud Glider
  member account cannot prove those administrative actions. The later SCP work
  must also configure or verify an organization trail in the administrative
  account and deliver relevant evidence to the IAM/account audit destination.
- EventBridge category rules use an enumerated API list for EC2 networking and
  compute calls. New AWS API names must be classified when introduced. The
  canonical CloudTrail archive captures management calls even if a projection
  rule has not yet been updated.
- Secrets, user-data contents, credentials, and full policy documents should not
  be duplicated into log messages. Record a digest and reviewed source revision
  for large definitions; CloudTrail preserves the API request subject to its
  documented field handling.

## Retention

The baseline uses 365-day CloudWatch retention and retains log-group resources
when the stack is deleted or replaced. The canonical S3 archive is retained
until an operator follows a separately reviewed disposal procedure. Retained
resources continue to incur charges and must be included in cost review.

## Permission guardrail administration

Boundary versions, role-boundary attachments, SCP/RCP updates and policy
attachments require independently retained administrative audit evidence. The
workload account trail cannot prove Organizations changes made elsewhere. The
steady-state protected-resource SCP also blocks normal foundation changes to
retention and trail selectors. Use the reviewed maintenance and recovery path
in [the guardrail runbook](../iam/permission-guardrails.md); preserve canonical
audit delivery while testing RCP service-source restrictions.
