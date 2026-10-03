# DynamoDB AWS-owned encryption key

Date: 2026-09-22

Use DynamoDB's AWS-owned encryption key for the state table. Set
`SSESpecification.SSEEnabled: false` in the foundation template and omit
`SSEType`. This keeps encryption at rest enabled while avoiding account-billed
KMS requests for table access. Recent CloudTrail samples showed repeated
DynamoDB Decrypt calls for the sandbox state table on behalf of the agent.

Apply through a CloudFormation UPDATE change set. For an existing stack, base
the update on its deployed template and change only StateTable's
SSESpecification; preserve all parameters and review for no replacement or
unrelated changes. Verify UPDATE_COMPLETE and an ACTIVE table with no
account KMS key in SSEDescription. Do not disable or delete the old KMS key:
existing backups may still depend on it. Historical import/recovery snapshots
retain the settings captured at their creation and must not be redeployed as
current configuration.

No lifecycle gates, control records, IAM permissions, backups, or EC2 volume
encryption change. The previously observed request frequency remains a
separate runtime investigation.

Reference: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-dynamodb-table-ssespecification.html

## Sandbox verification

Applied change set `ddb-aws-owned-encryption-20260922` to
`cloud-glider-sandbox` in `us-west-2`. The only direct modification was
StateTable.SSESpecification, with no replacement; role and Lambda entries were
dynamic dependency reevaluations with unchanged template definitions.
CloudFormation reached UPDATE_COMPLETE, and DynamoDB reported ACTIVE with no
SSEDescription (AWS-owned encryption). All 65 unit tests, repository safety
checks, cfn-lint, and AWS template validation passed. Live propagation and
failure injection were not exercised because lifecycle behavior is unchanged.
