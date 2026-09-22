# Sandbox foundation recovery, 2026-09-22

## Prepared operation

Stack: `cloud-glider-sandbox`, account `123456789012`, Region `us-west-2`.
All AWS CLI examples below specify `--region us-west-2`; do not rely on a
profile or environment default. A stack or change-set ARN does not select the
CLI endpoint's Region. IAM is global, but its examples also pass the Region
explicitly for consistency.
The stack reached `UPDATE_ROLLBACK_COMPLETE` with undeleted resources.
The prepared change set is `foundation-recovery-import-20260922`:

```text
arn:aws:cloudformation:us-west-2:123456789012:changeSet/foundation-recovery-import-20260922/cfd2714d-3ecc-43ae-a37a-06eb6353ad3e
```

It was verified `CREATE_COMPLETE` / `AVAILABLE`, with exactly nine `Import`
actions. It has not been executed. No live IAM policies were changed.

Files:

- `cfn/import-foundation-recovery.json`: the deployed template's ten existing
  resources, unchanged, plus nine surviving resources at their observed settings.
- `cfn/foundation-recovery-resources.json`: exact import identifiers verified
  using CloudFormation `get-template-summary`.
- `cfn/foundation-recovery-parameters.json`: preserves every deployed parameter.
- `cfn/foundation-recovery-change-set.json`: change-set identity and artifact hashes.

| Logical ID | Observed survivor |
| --- | --- |
| GenerationServiceRole | `cloud-glider-sandbox-generation-cfn`, including its existing inline policy |
| OperationalAlertsTopic | `cloud-glider-sandbox-operational-alerts`, no subscriptions |
| ComputeAuditRule | `cloud-glider-sandbox-compute-audit` |
| IamAccountAuditRule | `cloud-glider-sandbox-iam-account-audit` |
| InfrastructureServiceAuditRule | `cloud-glider-sandbox-infrastructure-audit` |
| NetworkAuditRule | `cloud-glider-sandbox-network-audit` |
| CloudFormationStatusAuditRule | `cloud-glider-sandbox-stack-status-audit` |
| ArtifactBucketPolicy | Policy on `cloud-glider-sandbox-123456789012-us-west-2-artifacts` |
| AuditArchiveBucketPolicy | Policy on `cloud-glider-sandbox-123456789012-us-west-2-audit` |

All five rules have **empty target lists**. Import preserves that partial state;
the subsequent foundation update restores the configured targets. Neither the
emergency-hold Lambda log group nor the EventBridge log resource policy exists,
so neither is included in the import. No SNS subscription exists to import.

The snapshot is sandbox-specific. Do not use it for another account, environment,
or a fresh deployment. Imported resources have `DeletionPolicy: Retain` and
`UpdateReplacePolicy: Retain` during recovery. The normal foundation template
keeps its existing lifecycle policies; review their restoration in the later
update change set. Blanket retention on every resource is not a substitute for
rollback permissions and would leave more unmanaged resources after failures.

## Permissions before execution

The latest live foundation service-role policy includes the corrected Lambda
log-group ARN forms, account-scoped `logs:DeleteResourcePolicy`, and the reported
EventBridge/IAM/S3/SNS cleanup actions. Two additional read permissions listed in
the live CloudFormation resource schemas are absent:

- `iam:ListAttachedRolePolicies` on the generation role.
- `sns:GetDataProtectionPolicy` on the operational topic (even though no data
  protection policy is configured).

Review `iam/foundation-import-read-supplement.json` and have the operator who
manages `cloud-glider-sandbox-foundation-cfn` merge those statements into its
managed configuration, or attach this separate inline supplement:

```sh
cd /path/to/glider
aws iam put-role-policy \
  --region us-west-2 \
  --role-name cloud-glider-sandbox-foundation-cfn \
  --policy-name cloud-glider-foundation-import-read \
  --policy-document file://iam/foundation-import-read-supplement.json
```

This command changes deployment-role permissions; it has **not** been run.
It does not change the instance agent's permissions or create access keys.
The execution principal also needs the appropriate CloudFormation execution,
inspection, drift-detection, and service-role pass permissions.

Before the subsequent full deployment, review and merge
`iam/foundation-lifecycle-supplement.json`. It covers additional scoped provider
read operations, tag reconciliation, removal of an agent instance profile,
passing only the agent role to EC2, SNS subscription update/removal, and rollback
of foundation alarms, the Lambda function, and the audit trail. It includes the
two import reads, so using it makes the smaller supplement redundant.

```sh
aws iam put-role-policy \
  --region us-west-2 \
  --role-name cloud-glider-sandbox-foundation-cfn \
  --policy-name cloud-glider-foundation-lifecycle \
  --policy-document file://iam/foundation-lifecycle-supplement.json
```

This command also has **not** been run. Both documents supplement the current
policy; neither replaces it. Their names make repeat application update the same
inline policy. Keep the source of the existing operator-managed role in sync.
No permissions for optional managed-policy attachments, permissions boundaries,
organization trails, VPC Lambda, or unrelated resources were added.
Provider permissions were inspected, but no live deployment or IAM simulation
has proved the combined policy: the current operator cannot call
`iam:SimulatePrincipalPolicy`. Access Analyzer `ValidatePolicy` was also denied
to that operator; the supplements require operator review before application.

## Review and execute the import

Keep GitHub deploys paused operationally while doing this: do not dispatch a run.
Verify propagation remains disabled and no generation is provisioning; this
procedure does not enable propagation or modify DynamoDB control records.
Run the read-only guard immediately before execution:

```sh
python3 scripts/verify_foundation_import.py
aws cloudformation describe-change-set \
  --stack-name cloud-glider-sandbox \
  --change-set-name foundation-recovery-import-20260922 \
  --region us-west-2 \
  --query '{Status:Status,ExecutionStatus:ExecutionStatus,Changes:Changes[].ResourceChange}'
```

Require exactly the nine imports above. The guard rejects changed artifact
hashes, a changed deployed template, a different stack, extra/missing actions,
and an already executed or obsolete change set. It does not freeze AWS state or
validate current authorization. Recheck the inventory if resources were edited
since preparation. Never substitute the full foundation template for this import.

When the reviewed permissions are in place, execute:

```sh
aws cloudformation execute-change-set \
  --stack-name cloud-glider-sandbox \
  --change-set-name foundation-recovery-import-20260922 \
  --client-request-token foundation-recovery-import-20260922-execute \
  --region us-west-2

aws cloudformation wait stack-import-complete \
  --stack-name cloud-glider-sandbox --region us-west-2

aws cloudformation describe-stacks \
  --stack-name cloud-glider-sandbox --region us-west-2 \
  --query 'Stacks[0].StackStatus'
```

Expected status: `IMPORT_COMPLETE`. A waiter timeout is not permission to retry
execution. Inspect stack events and the change set first. If import rolled back,
stop and diagnose; do not delete resources or attempt the full deployment.
If it already succeeded, skip execution and proceed to drift detection. Do not
recreate this dated import against an updated stack.

## Check drift

```sh
glider_drift_id=$(aws cloudformation detect-stack-drift \
  --stack-name cloud-glider-sandbox --region us-west-2 \
  --query StackDriftDetectionId --output text)

aws cloudformation describe-stack-drift-detection-status \
  --stack-drift-detection-id "$glider_drift_id" --region us-west-2
```

Repeat the status command until `DetectionStatus` is `DETECTION_COMPLETE`.
For `DETECTION_FAILED`, inspect `DetectionStatusReason`; do not treat partial
results as a clean stack. Then inspect individual differences:

```sh
aws cloudformation describe-stack-resource-drifts \
  --stack-name cloud-glider-sandbox --region us-west-2 \
  --query 'StackResourceDrifts[].{Resource:LogicalResourceId,Status:StackResourceDriftStatus,Differences:PropertyDifferences}'
```

Review every `MODIFIED` or `DELETED` entry. `NOT_CHECKED` does not mean healthy.
In particular, `AWS::S3::BucketPolicy` supports import but not CloudFormation
drift detection. Check both policies explicitly against their imported
`PolicyDocument` values, normalizing JSON before comparing:

```sh
aws s3api get-bucket-policy \
  --region us-west-2 \
  --bucket cloud-glider-sandbox-123456789012-us-west-2-artifacts \
  --query Policy --output text
aws s3api get-bucket-policy \
  --region us-west-2 \
  --bucket cloud-glider-sandbox-123456789012-us-west-2-audit \
  --query Policy --output text
```

An `IN_SYNC` recovery stack is still an incomplete foundation: the imported rules
intentionally have no targets and the remaining foundation resources are absent.

## Complete the foundation and repeat deployments

Follow-up: the import completed successfully. The first normal deployment
preview, `awscli-cloudformation-package-deploy-1790039877`, proposed replacing
`GenerationServiceRole` because removing the imported explicit `Path: /` counts
as a replacement. It also proposed conditional rule replacement when removing
`EventBusName: default`. Do not execute that old preview. The foundation template
now preserves those explicit defaults and `MaxSessionDuration: 3600`.
The corrected unexecuted preview is
`foundation-recovery-update-preserve-identities-20260922`.

Always pass `--region us-west-2`, including when using a full change-set ARN.
The local CLI default was `us-east-1`; a request sent there cannot find the
change set in `us-west-2`.

After import and drift review, apply the lifecycle permission corrections and
prepare a normal **UPDATE** change set using `cfn/foundation.yaml`. For a local
preview, the following uses existing parameter values and does not execute:

```sh
aws cloudformation deploy \
  --stack-name cloud-glider-sandbox --region us-west-2 \
  --template-file cfn/foundation.yaml \
  --role-arn arn:aws:iam::123456789012:role/cloud-glider-sandbox-foundation-cfn \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset --no-execute-changeset
```

Review the returned change-set ID. Expect the remaining resources to be added,
the five rules to receive their targets, and concrete recovery values to return
to normal parameterized definitions. Stop for unexpected replacements/deletions
or changes to approved AMI/network inputs. Creating the remaining foundation can
increase recurring charges, so review it separately from this import.
Once approved, execute that update change set and wait for `UPDATE_COMPLETE`.
Use the name or ARN of the reviewed update preview in the same shell:

```sh
glider_update_change_set="foundation-recovery-update-preserve-identities-20260922"

aws cloudformation describe-change-set \
  --stack-name cloud-glider-sandbox \
  --change-set-name "$glider_update_change_set" \
  --region us-west-2
```

After reviewing that exact change set, execute and wait:

```sh
aws cloudformation execute-change-set \
  --stack-name cloud-glider-sandbox \
  --change-set-name "$glider_update_change_set" \
  --region us-west-2

aws cloudformation wait stack-update-complete \
  --stack-name cloud-glider-sandbox \
  --region us-west-2
```

Recheck drift, rule targets, log delivery, SNS email confirmation, operational
notifications, and cost notifications before any propagation test.

No GitHub Actions workflow change is required for normal idempotence. It already
uses the same stack name, deterministic logical/physical resource names,
`aws cloudformation deploy`, `--no-fail-on-empty-changeset`, and a concurrency
group with cancellation disabled. Once ownership is repaired, repeated identical
deployments update that stack or do nothing. Select the reviewed code revision
when dispatching; do not run an old revision that predates the rule dependencies.

The full template now makes each audit rule depend on
`EventBridgeLogResourcePolicy`, so its delivery authorization is created before
targets are configured and removed after the rules during rollback. Runtime
roles, propagation gates, resource names, and capacity limits are unchanged.

Routine deploys must not silently import unknown resources, delete collisions,
or automatically clean up failed stacks. If rollback again leaves unmanaged
resources, use an explicit reviewed import. GitHub concurrency does not serialize
manual CLI operations, so avoid running the two paths together.

## References

- [CloudFormation manual import requirements](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/import-resources-manually.html)
- [Import and drift support by resource type](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html)
- [SNS authorization scopes](https://docs.aws.amazon.com/service-authorization/latest/reference/list_sns.html)
- [Instance-profile role passing](https://docs.aws.amazon.com/IAM/latest/APIReference/API_AddRoleToInstanceProfile.html)

## Preparation checks

All 37 repository unit tests and the repository safety validator passed.
`cfn-lint` passed for the full foundation and the import template; only W2001
(intentionally unused existing parameters) was suppressed for the import.
AWS `validate-template` passed for both templates. A structural comparison
verified that all ten existing resource definitions and top-level template
settings were preserved. The live read-only guard confirmed exactly nine
imports and an available, unexecuted change set.

Import execution, post-import drift detection, full deployment, live rollback,
and notifications remain untested because this task prepared an unexecuted
recovery. Existing agent tests cover the unchanged lifecycle failure gates;
no propagation or generation stack operation was performed.
