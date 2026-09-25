# Permission guardrails: review and rollout

Status: local implementation and policy candidates. Nothing in this change
attaches organization policies, edits live IAM, enables propagation, or launches
compute. Account membership, effective policies, administrator trusts and
recovery access must be inspected in AWS before deployment.

## Components and administration

| Component | Purpose | Administration |
| --- | --- | --- |
| `cfn/permission-boundaries.json` | Five managed ceilings: agent, generation CloudFormation, bootstrap, hold, foundation | Separate security stack and security deployment principal |
| `cfn/foundation.yaml`, `cfn/bootstrap.yaml` | Require the four runtime ceilings; require an approved template URL | Foundation deployment service role |
| `iam/foundation-boundary-supplement.json` | Permit attaching only the corresponding runtime ceiling | Reviewed supplement to the foundation role; grants no policy editing |
| `iam/deployment-operator-policy.json` | Narrow foundation/bootstrap change-set deployment permissions | Candidate replacement for broad operator deployment grants |
| `iam/organization/scp-runtime.json` | Runtime action ceiling, Region and exact PassRole relationships | Organizations administrator |
| `iam/organization/scp-boundaries.json` | Required boundary mapping and protected boundary versions | Organizations administrator |
| `iam/organization/scp-role-administration.json` | Protect runtime, foundation and administrative role configuration | Organizations administrator |
| `iam/organization/scp-protected-resources.json` | Protect audit, state, retained object versions and hold function | Organizations administrator |
| `iam/organization/rcp-data-perimeter.json` | Trusted identities for S3/DynamoDB/Logs, S3 TLS and service-source restrictions | Organizations administrator |

Boundaries grant no access. Existing identity grants still determine which
operations a role can perform. The foundation ceiling uses service-level action
ceilings on named audit/log/alarm/notification resources to fit IAM's 6,144
character managed-policy limit. IAM, state-table infrastructure and Lambda
operations remain enumerated. The account-level `logs:PutResourcePolicy`
permission is an explicit residual administrative capability needed by the
current account-scoped EventBridge log policy; it is not confined to one policy
name by this ceiling. Do not treat this role as an untrusted deployer.

Do not administer the boundary stack with the foundation service role, grant
that role boundary-policy editing, or add an operator grant to manage the
security stack. Attach the exported `FoundationBoundaryArn` to the existing
foundation service role through the separately reviewed administrative path;
that external role is not newly created or imported by this template. Protect
the security deployment principal and recovery principal themselves.

The generated bootstrap role has a CloudFormation-generated suffix. Policies
use only `cloud-glider-{environment}-bootstrap-BootstrapRole-*` in the target
account. Changing stack name or logical ID requires a reviewed policy change.

## Private organization-policy rendering

Copy `config/guardrails.example.json` to an ignored local configuration, such as
`.artifacts/guardrails-config.json`. Supply the verified member account and
organization, environment, and three distinct role ARNs: boundary administrator,
recovery, and foundation CloudFormation service role. No runtime identity may
serve as any of these administrators. Their role ARNs must be in the member
account; management-account recovery assumes the appropriate member-account
role when necessary.

```sh
python3 scripts/render_guardrails.py --config .artifacts/guardrails-config.json
```

The script performs no AWS calls. It rejects missing/example account values,
wildcard or session ARNs, cross-account exception roles, duplicate administrator
roles, unresolved substitutions, and oversized policies. It emits compact JSON
under ignored `.artifacts/guardrails/` with owner-only file permissions. These
checks validate syntax and relationships, not existence, trust, account
membership, or permission to recover. Do not commit rendered policies or private
configuration. Keep the `${...}` source templates out of deployment commands.

Render the standalone foundation supplement and deployment-operator candidate
using the existing `scripts/render_account_policy.py` and independently supplied
`AWS_ACCOUNT_ID`. These two references are for `sandbox` and the existing role
and stack names. Review all effective user/group policies before replacing
operator permissions: adding a scoped policy does not cancel a broad grant.
The deployment candidate is only the deployment slice, not an operator's whole
permission set. Preserve separately reviewed inspection, artifact publishing,
CONTROL updates, bootstrap requests and audited HOLD clearance permissions.
It intentionally does not grant foundation deletion or security-stack access.

## Required predeployment inventory

1. Confirm Organizations all-features mode, member-account placement, enabled
   SCP/RCP types, inherited policies and available attachment quota. SCPs do not
   restrict management-account principals; RCPs do not protect resources in the
   management account. Neither limits service-linked roles. The generation
   CloudFormation role is an ordinary service role and is subject to these
   controls. This is one-account workload hardening, not multi-account propagation.
2. Inspect actual roles, attached/inline policy versions, role trust, resource
   policies and current boundary attachments. Verify the exact administrator and
   recovery ARNs. Foundation deployment must not be able to modify either.
3. Test recovery from the Organizations administration path. An account's
   AdministratorAccess or root does not override an SCP/RCP deny. Keep a tested
   organization-policy administrator able to correct or detach the relevant
   policy without depending on the runtime or foundation role.
4. Verify organization-policy changes are audited in the administrative account.
   The workload account's trail alone cannot establish SCP/RCP change history.
5. Review Access Analyzer external-access findings and service-call paths.
   These candidates intentionally deny external identities access to Glider data.
   Preserve only explicitly approved integrations and verify audit delivery.

## Staged deployment

1. Set propagation disabled and activate HOLD through the existing operator
   paths. HOLD also blocks the independent bootstrap path. Verify no generation
   creation or handoff is in flight. Preserve current instances and state.
2. Create the separate boundary stack through security administration, supplying
   the approved AMI, subnet and security group. For a brand-new installation use
   the default empty `ApprovedGenerationTemplateUrl`; it prevents compute creation
   while the foundation and artifact bucket are prepared. Do not attach SCPs yet.
3. Review the foundation role's existing grants against `FoundationBoundaryArn`,
   including current read-before-update/rollback API calls. Add only the reviewed
   boundary-attachment supplement, then attach the foundation ceiling through
   security administration. Investigate missing permissions rather than adding
   wildcard account/role access. Historical recovery snapshots are not deployable
   inputs in this sanitized repository.
4. Prepare reviewed UPDATE change sets for foundation and bootstrap. These attach
   the required runtime boundaries and install the template-identity gate.
   A first deployment may keep the approved URL empty until artifacts exist.
   Use the existing foundation service role explicitly. Review role updates and
   ensure no generation replacement or new compute is proposed.
5. Upload and verify the immutable approved template and agent versions through
   the operator release path. Set the identical, versioned S3 URL in the boundary,
   foundation and bootstrap stacks, and the matching identity tuple in CONTROL.
   The URL must use HTTPS, the regional S3 endpoint and `generation/`, contain an
   explicit non-null version ID, and be at most 512 characters. Independently
   verify that its bucket belongs to this account and is the approved artifact
   bucket. Compare the actual URL encoding produced by the agent/bootstrap.
6. Run IAM/Access Analyzer validation and the sandbox request tests below. Review
   and attach runtime, boundary, and role-administration SCPs at the target
   test-account/OU level. Keep the existing SCP allow baseline and inherited
   restrictions. Four SCP candidates consume attachment slots; inspect quotas
   and current attachments rather than removing existing policies to make room.
7. Attach `scp-protected-resources.json` only after foundation reconciliation and
   retention configuration are complete. It intentionally blocks ordinary
   foundation updates to protected bucket controls, state durability, trail
   selectors and log retention. It is a steady-state protection policy, not a
   transparent replacement for unrestricted deployment. See maintenance below.
8. Attach and test the RCP at the test-account/OU level. Preserve the mandatory
   `RCPFullAWSAccess` baseline. Test external denial and successful internal and
   service delivery before restoring the normal operator state.
9. Inspect effective policies, drift, CURRENT and heartbeats. Clear HOLD only
   through the audited operator procedure. Enabling propagation remains a
   separate intentional operator action under the existing bounded trial rules.

No workflow, deployment secrets or repository settings are changed here. The
existing workflow does not deploy the boundary stack. On an existing stack,
confirm that deployment retains the reviewed parameter value; initial automated
deployment with the default empty URL intentionally cannot start generations.

## Release, maintenance and recovery behavior

Change the approved template URL only while propagation is disabled and HOLD is
active. Coordinate the three stack parameters and CONTROL approval tuple. A
mismatch blocks creation. URL conditions apply only to CreateStack and
CreateChangeSet: retiring a predecessor or deleting an unexecuted preflight does
not require the old release URL to remain approved. Existing change sets do not
carry a template URL in ExecuteChangeSet authorization; discard stale change
sets during a release, inspect their origin, and retain the agent's identity and
ownership checks. Do not interpret the URL condition as an execution-time digest
or arbitrary parameter validator.

The protected-resources SCP's recovery exception is a principal ARN, not an
exception for whoever clicked Deploy. CloudFormation calls with its service
role's identity. For protected maintenance, have the Organizations administrator
review the exact change set and arrange a time-bounded, exact execution-role
exception, or a separately reviewed recovery service role with the required
trust and resource grants. Restore protection and verify drift afterward. Do not
blanket-exempt the normal foundation role or change runtime IAM to bypass a deny.

Boundary attachment rollback may attempt to restore an absent/incorrect
boundary, which the protections intentionally reject. Recovery should restore
the correct boundary under the security administrator, inspect the failed
CloudFormation operation, and resume controlled reconciliation. Keep live
generations intact. Deleting the security stack retains the boundary policies;
it is not a mechanism to remove protections.

## Validation and required sandbox matrix

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_repository.py
python3 scripts/render_bootstrap_template.py --check
cfn-lint cfn/permission-boundaries.json cfn/foundation.yaml cfn/bootstrap.yaml cfn/generation.yaml cfn/network.yaml cfn/billing-alerts.yaml
```

The offline request-fixture evaluator covers only the constructs in these
policies. It does not implement AWS service authorization, resource-policy
evaluation, Organizations inheritance or credential propagation. Use IAM
simulation for identity/boundary/SCP checks and real isolated API tests for
supported operations. The IAM simulator does not support RCPs.

Before production attachment, verify:

- Correct versioned template and service role succeed; a changed version,
  alternate URL, omitted URL/TemplateBody, wrong role and unrelated stack fail.
- Approved RunInstances resources and launch tags succeed; different AMI,
  subnet, security group, instance type, IMDSv1 and post-creation retagging fail.
- Arbitrary PassRole, IAM mutation, STS role chaining, direct agent compute,
  boundary removal/replacement/version editing, and off-Region runtime calls fail.
- Actual CloudFormation launch and rollback tagging behavior works with
  `ec2:CreateAction=RunInstances`; do not broaden tagging to pass a failed test.
- Agent CONTROL/HOLD writes fail, required CONTROL/HOLD ConditionCheckItem reads
  succeed, hold creation works, and only the operator can clear HOLD.
- Disabled propagation, emergency hold, duplicate execution, ambiguous health
  and failed conditional handoff preserve the predecessor. Existing unit tests
  exercise these application paths; they do not prove AWS authorization.
- Log writes, S3 metadata/version reads, CloudTrail delivery and bootstrap stream
  processing succeed. Wrong-organization data access and insecure S3 requests
  fail. Test service requests with present and absent source context.
- Protected-resource changes fail under normal foundation deployment; the
  separately approved recovery procedure works and remains audited.

No new billable services or instance sizes are introduced. Existing encryption,
concurrency and billing decisions remain unchanged. Optional new data-event
logging, Access Analyzer paid features or other monitoring need cost review.

## Limits and deferred work

The RCP protects only the named S3, DynamoDB and Logs resources. It does not cover
ordinary EC2 or CloudFormation APIs, grant access, block an in-organization
principal already authorized by IAM, or impose a network egress boundary. S3
service requests are source-checked when SourceAccount exists; other service
paths continue to rely on their resource policies. Existing CloudTrail writes
are already constrained to the exact trail ARN.

STS RCPs and a GitHub OIDC trust-policy replacement are deferred until the actual
deployment role, repository, environment subject and recovery federation are
verified. For GitHub, review `aud=sts.amazonaws.com` and the exact intended `sub`
claim; do not treat GitHub federation as an ordinary PrincipalOrgID check. Keep
the existing short-lived OIDC path; do not introduce access keys.

Neither boundaries nor organization policies enforce three live generations,
conditional write expressions, instance-specific handoff ownership, successor
health, or the latest DynamoDB stop state. Resource grants directly to role
sessions can bypass implicit boundary denies; explicit denies and the SCP/RCP
layers add protection, but resource policies still need review. Do not use Deny
with NotPrincipal for bounded roles. Independent semantic enforcement remains
the deferred broker design.

References, checked 2026-09-25:

- [IAM boundary evaluation](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html)
- [CloudFormation template and role conditions](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/control-access-with-iam.html)
- [CloudFormation service-role reuse](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-iam-servicerole.html)
- [EC2 creation-time tagging](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/supported-iam-actions-tagging.html)
- [SCP scope and rollout](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)
- [RCP support and limits](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_rcps.html)
- [Service-source protection](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)
- [IAM simulation limits](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html)
