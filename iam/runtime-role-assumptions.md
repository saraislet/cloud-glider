# Runtime IAM assumptions

The v1 public-IPv4 design does not add an IAM allow permission.

- The generation CloudFormation service role already has `ec2:RunInstances`
  access to the one approved AMI, subnet, security group, instance type, volume,
  and launch network interface. Setting `AssociatePublicIpAddress: true` on the
  primary ENI is part of that reviewed launch request.
- The agent role continues to call CloudFormation rather than EC2 networking
  APIs. Explicit denies prevent Elastic IP and network-interface mutation.
- The emergency-hold Lambda role is unchanged and has no EC2 permissions.
- The GitHub deployment and foundation CloudFormation roles require no broader
  runtime permissions for this change; they continue to publish the versioned
  template and manage only the reviewed foundation stack.

Creation now also requires the exact `ApprovedGenerationTemplateUrl` in both
the role policy and an independently administered boundary. An empty URL prevents
creation. Stack names and RoleARN alone do not restrict template content. The URL
gate does not enforce instance counts, arbitrary parameter semantics, execution
of pre-existing change sets, or current stop state. The immutable S3
VersionId/SHA-256/build tuple, parameter validation, change review and negative
tests remain necessary. Brokered semantic enforcement remains deferred to v2.
See [the guardrail runbook](permission-guardrails.md).

## RunInstances authorization by resource

EC2 evaluates RunInstances for each participating resource. Apply
`ec2:InstanceType=t4g.micro` and IMDSv2/tag requirements to the instance ARN only.
The network-interface and volume ARNs need their own RunInstances allow without
that instance-only condition. The instance allow still rejects other instance
types, and separate resource ARNs restrict the approved AMI, subnet, and security
group. No direct CreateNetworkInterface/CreateVolume permissions are added.

The first bootstrap failed with CREATE_FAILED because the auxiliary-resource
statement incorrectly required ec2:InstanceType. Correct this through a reviewed
foundation CloudFormation update. Preserve the failed generation stack and its
bootstrap record while inspecting resources; do not reset CURRENT or rearm the
Boolean to work around the failure. After the policy update, explicitly review
the failed-stack recovery path and any surviving resources before retrying compute.
The correction changes only foundation IAM: existing generation/agent artifact
versions and digests remain valid.

Reference: [EC2 RunInstances policy examples](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ExamplePolicies_EC2.html).

## Agent startup identity reads

The agent calls the singular `cloudformation:DescribeStackResource` to verify
that its IMDS instance ID is the GenerationInstance resource in its own stack.
`DescribeStackResources` (plural) does not authorize that call. The agent role
must allow both needed read actions on approved generation stacks. Without the
singular action, startup fails before CURRENT ownership and heartbeat writes;
systemd restarts the agent. Apply the reviewed foundation IAM update while
propagation remains disabled, then inspect CURRENT and heartbeats. Do not reset
DynamoDB state or replace the running instance to fix this permission failure.

## Handoff and telemetry authorization

The existing handoff transaction condition-checks both CONTROL/GLOBAL and
HOLD/ACTIVE. Authorize ConditionCheckItem for both partition keys while keeping
operator-state writes excluded. Agent log writes use canonical log-stream ARNs;
do not append `:log-stream:*` to a CloudFormation LogGroup.Arn already ending in
`:*`. Only ListBucket carries the s3:prefix condition; GetBucketLocation and
GetBucketVersioning must remain separate. Generation tagging is permitted only
with ec2:CreateAction=RunInstances, never as a standalone retagging request.
