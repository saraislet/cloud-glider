# Sandbox foundation recovery: remaining resources

Target: `cloud-glider-sandbox`, account `123456789012`, Region `us-west-2`.
Always pass `--region us-west-2`, even with a full ARN. IAM is global; its
examples also include the Region explicitly for consistency.

## Foundation completion (2026-09-22)

The missing Lambda execution role was recreated through CloudFormation in
`emergency-hold-role-repair-20260922`. The Lambda quota increase to 1,000 then
allowed restoring `EmergencyHoldReservedConcurrency=2` using
`cfn/foundation-completion-parameters.json`.

The first completion update failed on the instance-profile pre-creation lookup.
Rollback also required `lambda:DeleteFunctionConcurrency`. The administrator
added the creation/read, reservation rollback, and scoped EC2 PassRole permissions.
Rollback reached `UPDATE_ROLLBACK_COMPLETE` with a cleanup warning; an independent
IAM lookup confirmed the failed profile did not exist. No resources were skipped.
The fresh update `foundation-complete-reserved-concurrency-retry-20260922`
completed with **UPDATE_COMPLETE**. Lambda is Active with reservation two, and
the agent instance profile contains the intended agent role.
It contains five additions and eighteen modifications, with no replacements or
removals. The superseded subscription-replacing preview was deleted.

The full scoped recovery supplement remains in
`iam/foundation-completion-repair-supplement.json`. The administrator applied it
as the inline policy `cloud-glider-foundation-completion-repair` on
`cloud-glider-sandbox-foundation-cfn`; the live policy was verified to match the
supplement, including exact-name cleanup permission at both the root and
`/cloud-glider/` instance-profile paths. For reference, an administrator can
reapply the supplement using:

```sh
aws iam put-role-policy \
  --role-name cloud-glider-sandbox-foundation-cfn \
  --policy-name cloud-glider-foundation-completion-repair \
  --policy-document file://iam/foundation-completion-repair-supplement.json \
  --region us-west-2
```

The configured `GliderManager` identity could not apply the policy itself because
AWS denied `iam:PutRolePolicy`; the administrator's update resolves the scoped
cleanup permission gap for a profile that fails before creation.

Do not replay dated imports or the temporary shared-concurrency repair after
completion. For future updates, use `cfn/foundation.yaml`, retain the explicit
reservation of two, and review a fresh change set. Normal repeated deployments
need no GitHub Actions workflow change. Import remains an explicit operator step.

Post-deployment drift detection `62f1c940-b639-11f1-88cc-023e0f368e69` completed
with **IN_SYNC**: all 29 supported resources matched. Both bucket policies were
compared separately; the existing subscription remains confirmed. All five
operational alarms are enabled and OK; all five audit rules have expected targets.
CloudTrail and infrastructure logs show recent delivery. All 38 repository tests,
template linting, repository validation, and five isolated Lambda handler checks
passed. Live Lambda state-changing behavior and end-to-end email delivery remain
untested.

To recheck drift (use the new detection ID returned by the first command):

```sh
aws cloudformation detect-stack-drift \
  --stack-name cloud-glider-sandbox --region us-west-2
aws cloudformation describe-stack-drift-detection-status \
  --stack-drift-detection-id <returned-detection-id> --region us-west-2
aws cloudformation describe-stack-resource-drifts \
  --stack-name cloud-glider-sandbox --region us-west-2
```

Require `DETECTION_COMPLETE` before interpreting the result. Unsupported resources
still require separate inspection. See `cfn/foundation-completion-result.json`
for execution and verification results.
Billing alerts and notification-delivery verification are deferred to V2 by
operator decision and are not first-pass prerequisites. `cloud-glider-billing-alerts`
in `us-east-1` was `REVIEW_IN_PROGRESS`, with no Cloud Glider billing alarms.
Budget and cost-anomaly subscription reads were denied to this operator. The
operational subscription is confirmed, but end-to-end notification delivery has
not been tested. The `CONTROL/GLOBAL` item is absent; initialize it through the
approved operator path with propagation disabled. The remaining control,
health, and failure-path checks still apply before propagation.

## Historical import preparation (do not replay)

The first nine-resource import succeeded. The subsequent foundation update failed
when reserving two Lambda concurrent executions would leave less than the
account's required unreserved minimum. Rollback finished in
`UPDATE_ROLLBACK_COMPLETE`, leaving additional unmanaged resources.

The current recovery files supersede the first import snapshot:

- `cfn/import-foundation-recovery.json` preserves the deployed template's 19
  managed resources and adds seven survivors using observed configuration.
- `cfn/foundation-recovery-resources.json` imports only those seven survivors.
- `cfn/foundation-recovery-parameters.json` preserves the deployed parameters.
- `cfn/foundation-recovery-change-set.json` records the new source/template hashes
  and proposed name `foundation-recovery-import-remaining-20260922`.

**The second import was executed successfully and reached `IMPORT_COMPLETE`.**
Its change-set ARN is recorded in `cfn/foundation-recovery-change-set.json`.
All seven survivors are now managed by the stack. The creation and execution
commands below are historical recovery instructions; do not replay them. The
pre-execution verifier intentionally rejects the completed stack state. Do not
reuse either previous update preview.

| Import logical ID | Resource |
| --- | --- |
| EmergencyHoldFunction | `cloud-glider-sandbox-emergency-hold` |
| EmergencyHoldFunctionLogGroup | `/aws/lambda/cloud-glider-sandbox-emergency-hold` |
| AccountAuditTrail | `cloud-glider-sandbox-audit` |
| OperationalEmailSubscription | Existing confirmed email subscription |
| StateTableReadThrottleAlarm | `cloud-glider-sandbox-ddb-read-throttles` |
| StateTableWriteThrottleAlarm | `cloud-glider-sandbox-ddb-write-throttles` |
| StateTableSystemErrorsAlarm | `cloud-glider-sandbox-ddb-system-errors` |

The complete template has 26 definitions. Each new import has `DeletionPolicy:
Retain` and `UpdateReplacePolicy: Retain`. Existing definitions and top-level
settings are unchanged. CloudFormation's template summary confirms the import
identifiers, including `Arn` for the SNS subscription.

The Lambda definition uses its downloaded deployed `index.py`, current settings,
no reserved concurrency, and a literal execution-role ARN. The execution role
was deleted during rollback and is **not** an import candidate. Import only
restores ownership; it does not make this function operational. Do not invoke it
or enable propagation until a subsequent reviewed update recreates its role and
health checks pass. Notification-delivery verification is deferred to V2. Do not
add a `GetAtt` referencing that absent role to this import template.

## Prerequisites

Later repair: the operator approved temporarily using shared Lambda concurrency
while awaiting a quota increase. `cfn/repair-emergency-hold-role.json` is the
narrow UPDATE template for recreating the absent execution role; its parameters
are in `cfn/emergency-hold-repair-parameters.json`. It sets
`EmergencyHoldReservedConcurrency=-1`, which omits the reservation. The default
for new deployments remains 2. See [the decision](decisions/0002-temporary-lambda-shared-concurrency.md).
The import sections below describe the already completed recovery and must not
be replayed against a stack that has progressed to normal updates.

Keep propagation disabled and do not run GitHub deployment or manual updates
concurrently. Confirm the stack is still `UPDATE_ROLLBACK_COMPLETE` and recheck
survivor configuration if any operator has edited it since preparation.

Use an approved operator identity for import and drift detection. The foundation
service role also needs provider read permissions for the seven imported types.
The IAM supplements in `iam/foundation-import-read-supplement.json` and
`iam/foundation-lifecycle-supplement.json` supplement the existing role policy;
review the live combined policy rather than assuming a local file is attached.
No IAM changes were made during this preparation. See the drift runbook for
required CloudFormation and provider read permissions.

If the reviewed lifecycle supplement is still needed, the operator can apply it:

```sh
aws iam put-role-policy \
  --role-name cloud-glider-sandbox-foundation-cfn \
  --policy-name cloud-glider-foundation-lifecycle \
  --policy-document file://iam/foundation-lifecycle-supplement.json \
  --region us-west-2
```

## Create, review, and execute the second import

Run these commands from the repository root. Creating the change set does not
execute it or create the resources:

```sh
aws cloudformation create-change-set \
  --stack-name cloud-glider-sandbox \
  --change-set-name foundation-recovery-import-remaining-20260922 \
  --change-set-type IMPORT \
  --template-body file://cfn/import-foundation-recovery.json \
  --resources-to-import file://cfn/foundation-recovery-resources.json \
  --parameters file://cfn/foundation-recovery-parameters.json \
  --capabilities CAPABILITY_NAMED_IAM \
  --role-arn arn:aws:iam::123456789012:role/cloud-glider-sandbox-foundation-cfn \
  --client-token foundation-recovery-import-remaining-20260922 \
  --region us-west-2

aws cloudformation wait change-set-create-complete \
  --stack-name cloud-glider-sandbox \
  --change-set-name foundation-recovery-import-remaining-20260922 \
  --region us-west-2

python3 scripts/verify_foundation_import.py

aws cloudformation describe-change-set \
  --stack-name cloud-glider-sandbox \
  --change-set-name foundation-recovery-import-remaining-20260922 \
  --region us-west-2 \
  --query '{Status:Status,ExecutionStatus:ExecutionStatus,Changes:Changes[].ResourceChange}'
```

Require `CREATE_COMPLETE`, `AVAILABLE`, and exactly seven `Import` actions.
The verifier checks artifact hashes, deployed template, stack state, exact import
manifest, and the actual change-set template. It accepts CloudFormation's object
or string representation of JSON templates. It does not prove IAM permissions,
resource health, or absence of external edits.

After review:

```sh
aws cloudformation execute-change-set \
  --stack-name cloud-glider-sandbox \
  --change-set-name foundation-recovery-import-remaining-20260922 \
  --client-request-token foundation-recovery-import-remaining-20260922-execute \
  --region us-west-2

aws cloudformation wait stack-import-complete \
  --stack-name cloud-glider-sandbox --region us-west-2
```

If a request times out or the change-set name already exists, inspect its status
before retrying. If import succeeded, skip execution. If it rolled back, inspect
events and stop; do not delete surviving resources or deploy the full template.
This dated recovery must not be replayed after the stack changes.

## Drift and completing the foundation

After `IMPORT_COMPLETE`, follow [the drift runbook](cloudformation-drift-detection.md).
Require successful drift detection and review each difference. `NOT_CHECKED`
is not proof of correctness. Bucket policies and SNS subscriptions require
separate comparison where CloudFormation drift detection is unsupported.

Before a normal update, inspect regional concurrency with an operator identity
allowed to perform `lambda:GetAccountSettings` and `lambda:GetFunctionConcurrency`:

```sh
aws lambda get-account-settings --region us-west-2 \
  --query 'AccountLimit.{Total:ConcurrentExecutions,Unreserved:UnreservedConcurrentExecutions}'
aws lambda get-function-concurrency \
  --function-name cloud-glider-sandbox-emergency-hold --region us-west-2
```

The failed request reported an unreserved floor of 10 for this account. Adding a
new reservation of two requires at least 12 unreserved slots under that floor.
Do not assume the quota or alter unrelated reservations. Removing the reservation
also removes the function's dedicated capacity and concurrency cap; changing that
behavior or increasing quotas requires explicit review. Zero disables execution.
The import template omits the reservation to reflect reality. The subsequent
operator-approved repair uses the new -1 parameter option temporarily; this
shares account capacity and removes the function's individual concurrency cap.
After quota approval, explicitly override `EmergencyHoldReservedConcurrency=2`
in a reviewed update; ordinary repeat deployments preserve -1, even though the
template default is 2.

The following describes the historical post-import sequence, now superseded by
the completion above. Only after resolving concurrency, prepare a normal UPDATE change set using
`cfn/foundation.yaml`. That update must recreate `EmergencyHoldFunctionRole`,
restore the function's role relationship, and complete remaining resources.
Preserve explicit `Path: /` on the imported generation role and
`EventBusName: default` on imported rules to avoid unwanted replacements.
Review the update for replacements, deletions, and recurring cost before execution.
Do not use earlier update previews based on the 19-resource stack.

No GitHub workflow change is needed for normal repeated deployments. The workflow
uses a fixed stack name, concurrency protection, and empty-change-set handling.
Import remains an explicit operator action; normal deployments must not silently
adopt or delete unmanaged resources.

## Validation and limits

The preparation compares all 19 managed definitions and top-level settings
against the current deployed template and checks the seven-resource manifest.
The template is linted and validated with CloudFormation. Unit tests exercise
repeated verification, wrong-stack and stale/executed change sets, unexpected
actions, and JSON template hashing. Intentionally unused existing parameters
remain in the import template (lint W2001 is suppressed only for this template).

The seven-resource import completed successfully. No role was recreated,
concurrency changed, or propagation enabled. Runtime health remains untested;
import completion does not repair the missing execution role.

References: [manual import requirements](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/import-resources-manually.html),
[resource import and drift support](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html).

## Historical post-import drift result

Detection `05bc7ff0-b629-11f1-90ce-023fa17c6517` failed because the foundation
service role lacks `cloudwatch:ListTagsForResource` on the three imported
DynamoDB alarms. Grant that read action scoped to those alarm ARNs and rerun
drift detection. No differences were returned by the partial check, but the
stack drift status is `UNKNOWN`, not a verified clean result. This permission
is included in the prepared lifecycle supplement; verify it is in the live role.
