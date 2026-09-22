# GliderManager and bootstrap deployment permission review

Reviewed live in account `123456789012`, Region `us-west-2`, on 2026-09-22.
The current caller is IAM user `GliderManager`. No live IAM changes were made.

## Caller result: no additional deployment permissions needed

The read permissions now work. Reviewed the user inline policy, all six attached
managed policies (including their default versions), the Gliders group inline
policy, and both inline policies on `cloud-glider-sandbox-foundation-cfn`.
The group and service role have no attached managed policies. No permissions
boundary is set on either the user or the service role. No explicit deny was
found in those identity policies. Organization SCPs were not inspected and
this is not an IAM simulation or proof of end-to-end deployment authorization.

| Requirement | Existing grant |
| --- | --- |
| Create, inspect, execute, and discard bootstrap change sets | `Gliders` group inline `GliderOps`: `cloudformation:*` on `*` |
| Validate templates and inspect stack resources | Same group grant |
| Pass the foundation service role to CloudFormation | `PassFoundationServiceRoleOnly`, restricted to the exact role and `iam:PassedToService=cloudformation.amazonaws.com` |

Do not attach the previous candidate as an additional policy: every deployment
allow it contains is already granted. It has been renamed to
`glider-manager-bootstrap-deploy-reference.json` as a possible future scoped
replacement, **not** a missing-permissions supplement. Replacing the existing
broad group grant would require a separate review of other operational needs.
The Gliders group also grants broad EC2, CloudWatch, and Secrets Manager access;
these existing grants were not changed as part of this bootstrap review.

Always supply the service role explicitly when previewing deployment:

```sh
--role-arn arn:aws:iam::123456789012:role/cloud-glider-sandbox-foundation-cfn
```

`iam/glider-manager-policy-read.json` remains the scoped read-policy reference.
New attachments or boundaries require reviewing their exact ARNs as well.

## Service-role result: apply a separate reviewed supplement

`foundation-bootstrap-supplement.json` belongs on
`cloud-glider-sandbox-foundation-cfn`, **not** on GliderManager and not on the
Lambda's runtime role. It adds only the bootstrap resource permissions missing
from the inspected service-role policies:

| Resource | Added coverage |
| --- | --- |
| Generated `cloud-glider-sandbox-bootstrap-BootstrapRole-*` role | Create/read/update/delete inline policies and role; tags; PassRole only to Lambda |
| `cloud-glider-sandbox-bootstrap` Lambda | Create/read/update/delete, reserved-concurrency management, and tags |
| Bootstrap event source mapping | Create and reconcile mapping, filtered to the bootstrap function and identifying tags |
| `/aws/lambda/cloud-glider-sandbox-bootstrap` log group | Creation, retention, reads, tags, and rollback deletion permission |
| Generated bootstrap error alarm | Missing `cloudwatch:UntagResource` for tag updates |

Correction to the earlier candidate notes: the generated alarm name starts with
`cloud-glider-sandbox-bootstrap-`, which **already matches** the existing
`cloud-glider-sandbox-*` alarm permission. Alarm create/read/delete permissions
need not be duplicated. Existing DescribeLogGroups/DescribeIndexPolicies and
approved EC2-input inspection permissions are reused too.

The generated role and alarm ARN patterns assume the exact stack name
`cloud-glider-sandbox-bootstrap` and the current logical IDs. Do not use this
policy unchanged for a differently named stack. The template now explicitly tags
BootstrapTrigger with project, environment, owner, and purpose. Deploy that
updated template together with the supplement: old untagged mappings do not meet
the ownership conditions and need separate inspection before migration.

The mapping UUID is assigned by AWS, so its ARN suffix cannot be known before
creation. CreateEventSourceMapping requires Resource `*`; its statement is
restricted to the exact function, Region, and request tags. Read/update/delete
use the account/Region mapping ARN pattern, function condition, and resource
tags. Tag-on-create uses request tags; later tag reconciliation requires existing
ownership tags. Keep the project/environment/purpose ownership tags stable.

No managed-policy attachment, permissions-boundary modification, VPC networking,
customer-managed KMS key, Lambda invocation, direct EC2 mutation, or data-plane
DynamoDB write permission is added to the deployment service role. Optional
provider features absent from this template are intentionally excluded. The
existing runtime role policy in the template supplies DynamoDB stream/table,
S3-version, CloudFormation, and scoped PassRole permissions to the Lambda.

Apply this supplement as a **customer-managed policy**, not another inline
policy. The inspected role's existing inline policies total 7,117 compact JSON
characters; the supplement adds 3,649, bringing the total to 10,766, above IAM's
10,240-character aggregate role inline-policy quota. The supplement alone fits
the 6,144-character managed-policy quota. Splitting it into more inline policies
would not change the aggregate limit.

An identity authorized to create policies and attach them to the foundation
role can run:

```sh
aws iam create-policy --region us-west-2 \
  --policy-name CloudGliderBootstrapResources \
  --policy-document file://iam/foundation-bootstrap-supplement.json

aws iam attach-role-policy --region us-west-2 \
  --role-name cloud-glider-sandbox-foundation-cfn \
  --policy-arn arn:aws:iam::123456789012:policy/CloudGliderBootstrapResources
```

If that managed policy already exists, inspect its default version rather than
assuming it matches or recreating it. Keep its operator-managed source in sync.
Attach it as a permissions policy, not a permissions boundary. Existing inline
policies remain in place. No live permissions were changed by this documentation
correction.

The scoped policy-read reference also includes this managed-policy ARN so its
contents can be inspected after attachment. An operator must update the live
read grant if it still contains only the original six managed-policy ARNs.

## Validation and remaining limits

Compared the supplement with live CloudFormation provider schemas for IAM Role,
Lambda Function, Lambda EventSourceMapping, Logs LogGroup, and CloudWatch Alarm,
including read/update/tag/rollback paths. Parsed JSON, checked policy scopes,
and ran repository tests and cfn-lint. Live IAM simulation, resource creation,
and rollback are not yet proved. Any provider failure should be inspected against
its exact action/resource; do not broaden permissions blindly.

References:
- [Lambda action/resource/condition support](https://docs.aws.amazon.com/service-authorization/latest/reference/list_lambda.html)
- [CloudFormation service roles](https://docs.aws.amazon.com/prescriptive-guidance/latest/least-privilege-cloudformation/service-roles-for-cloudformation.html)
