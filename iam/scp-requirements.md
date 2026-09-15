# Cloud Glider SCP requirements

The account SCP will be generated only after the account, organization, Region,
deployment-principal, and break-glass ARNs are supplied and verified.

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
