# CloudFormation drift detection runbook

Use this runbook to detect configuration drift on a Cloud Glider stack without
changing stack resources or propagation control state. Drift detection is an
inspection operation; it does not reconcile differences.

The examples default to the sandbox foundation stack in `us-west-2`. Run them
with an approved operator identity. Keep propagation disabled while reviewing
foundation drift or recovering an incomplete deployment.

## Prerequisites

The calling principal needs these CloudFormation permissions:

- `cloudformation:DetectStackDrift`
- `cloudformation:DetectStackResourceDrift`
- `cloudformation:BatchDescribeTypeConfigurations`
- `cloudformation:DescribeStackDriftDetectionStatus`
- `cloudformation:DescribeStackResourceDrifts`

CloudFormation also needs read permission for every supported resource in the
stack. When a stack has a CloudFormation service role, make these provider read
permissions available to that role. For CloudWatch Logs drift detection, the
foundation service role currently needs both account-level list actions:

```json
{
  "Effect": "Allow",
  "Action": [
    "logs:DescribeLogGroups",
    "logs:DescribeIndexPolicies"
  ],
  "Resource": "*"
}
```

`logs:DescribeIndexPolicies` does not support resource-level IAM scoping. Add
only the provider read actions reported as missing; do not broaden the service
role merely to make drift detection pass.

## Run drift detection

Set the target stack and Region in Bash:

```bash
glider_stack_name="cloud-glider-sandbox"
glider_region="us-west-2"
```

Run the setup and subsequent commands in the same shell. Every AWS command
passes `--region "$glider_region"` explicitly; keep it even when the stack is
identified by ARN. An ARN does not override the CLI's selected endpoint Region.
If a stack or drift operation cannot be found, check the Region and operator
account before starting another operation.

Start drift detection and store its ID in the Bash variable
`glider_drift_id`:

```bash
glider_drift_id="$(aws cloudformation detect-stack-drift \
  --stack-name "$glider_stack_name" \
  --region "$glider_region" \
  --query StackDriftDetectionId \
  --output text)"

printf 'Drift detection ID: %s\n' "$glider_drift_id"
```

Check the operation status:

```bash
aws cloudformation describe-stack-drift-detection-status \
  --stack-drift-detection-id "$glider_drift_id" \
  --region "$glider_region" \
  --output json
```

Repeat that command until `DetectionStatus` is `DETECTION_COMPLETE`. The AWS
CLI installation used for this project does not provide a
`stack-drift-detection-complete` waiter. If the status is `DETECTION_FAILED`,
inspect `DetectionStatusReason` and do not treat partial results as a clean
stack.

After detection completes, print a concise summary:

```bash
aws cloudformation describe-stack-drift-detection-status \
  --stack-drift-detection-id "$glider_drift_id" \
  --region "$glider_region" \
  --query '{DetectionStatus:DetectionStatus,StackDriftStatus:StackDriftStatus,DriftedResourceCount:DriftedStackResourceCount,Reason:DetectionStatusReason}' \
  --output json
```

List resources requiring review:

```bash
aws cloudformation describe-stack-resource-drifts \
  --stack-name "$glider_stack_name" \
  --region "$glider_region" \
  --stack-resource-drift-status-filters MODIFIED DELETED NOT_CHECKED \
  --query 'StackResourceDrifts[].{Resource:LogicalResourceId,Type:ResourceType,Status:StackResourceDriftStatus,Differences:PropertyDifferences}' \
  --output json
```

To inspect all checked resources, including those that are in sync, omit the
`--stack-resource-drift-status-filters` option:

```bash
aws cloudformation describe-stack-resource-drifts \
  --stack-name "$glider_stack_name" \
  --region "$glider_region" \
  --output json
```

## Interpret the result

- `DETECTION_COMPLETE` means CloudFormation finished inspecting supported
  resources. It does not mean the stack is in sync.
- `StackDriftStatus: IN_SYNC` means no checked resource differed from the
  template.
- `StackDriftStatus: DRIFTED` requires review of every `MODIFIED` or `DELETED`
  resource before a deployment.
- `NOT_CHECKED` does not establish that a resource is healthy or in sync.
- `DETECTION_FAILED` is not a clean result. Inspect `DetectionStatusReason`,
  grant only the missing provider read permission when appropriate, and start a
  new detection.

CloudFormation can return partial drift results when detection fails. Treat
those results as diagnostic evidence only; repeat detection successfully before
approving the next foundation change set.

## Review before reconciliation

Do not repair drift with direct resource mutations merely to clear the report.
Compare each difference with the approved template and safety contract, then
prepare and review a normal CloudFormation change set. Stop for unexpected
replacement or deletion of the state table, retained buckets, audit log groups,
IAM roles, or active generation resources.

Resource types that do not support CloudFormation drift detection require a
separate read-only comparison against the approved template. Record those
checks alongside the drift-detection ID so the review remains auditable.
