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

Stack-name and `cloudformation:RoleARN` IAM conditions do not restrict template
content, instance count, or public-IP settings. V1 therefore relies on the
immutable S3 VersionId/SHA-256/build tuple, exact parameter validation, change
review, and negative tests. Brokered semantic enforcement remains deferred to
v2.
