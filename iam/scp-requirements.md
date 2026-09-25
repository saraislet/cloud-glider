# Cloud Glider SCP requirements

Four scoped SCP candidates and an RCP candidate are provided under
`iam/organization/`. The private renderer requires independently verified account,
organization, deployment, security-administration and recovery role inputs. It
does not contact AWS or attach policies. The runtime Region is fixed to us-west-2.
See [the guardrail runbook](permission-guardrails.md) for boundaries, migration,
attachment sequencing, limitations and validation.

It should provide defense in depth for the Cloud Glider account by:

- denying IAM role, policy, instance-profile, permission-boundary, and
  Organizations mutations to Cloud Glider generation principals
- denying direct `ec2:RunInstances` and `ec2:TerminateInstances` to the agent
  role while allowing the approved CloudFormation generation service role
- denying `iam:PassRole` except the approved agent and generation service-role
  relationships
- denying Cloud Glider operations outside the approved Region, except global
  services and explicitly reviewed administrative operations
- denying deletion or disabling of CloudTrail, audit buckets, DynamoDB state,
  and audit log groups except to the break-glass principal
- preserving an independently tested administrative recovery path

The SCP must not be attached until policy simulation, a non-production account
test, organization-trail verification, and break-glass recovery validation pass.
SCP changes and attachments must be visible in the IAM/account audit domain.
