# Human operator identities

Anna Sarai approved the seven-role split, including its existing privilege and
semantic-enforcement risks, on October 6, 2026. The names GliderStateRecovery
and GliderExceptionalCleanup distinguish state repair from resource deletion.
This is an additive migration. GliderManager's direct, group-derived and SSO
permissions remain until replacement paths have been validated alongside them.

## Roles and access

| Identity | Profile | Work |
| --- | --- | --- |
| GliderObserver | glider-observe | Observer, state/Streams, logs including bootstrap, metrics, CloudTrail and quota reads |
| GliderOperator | glider-operate | Idle configuration and supported START/STOP/HOLD/CLEANUP requests |
| GliderStateRecovery | glider-recover | Conditional submission settlement, lifecycle repair and audited HOLD recovery |
| GliderExceptionalCleanup | glider-cleanup | Exceptional verified generation termination, disposable-stack and residual-resource deletion |
| GliderImageBuilder | glider-build | Existing Packer permissions, isolated candidate smoke tests and build cleanup |
| GliderRelease | glider-release | Immutable artifact publication, reviewed CloudFormation releases and idle release-pin coordination |
| GliderSecurityAdmin | glider-security | Scoped Glider role, policy and boundary administration |

Each role is under `/cloud-glider/operator/`, with a one-hour maximum session.
GliderAccess delegates assumption of the six operational roles; the separate
GliderSecurityAccess permission set delegates only GliderSecurityAdmin. Both
are intended for the existing Identity Center user. The existing GliderManager
IAM user is trusted during migration, with a new assumption-only policy on its
existing Gliders group. Existing role policies, trusts and group grants are not
replaced. Task roles do not grant STS assumption of other task roles.

Workload roles retain service-only responsibilities. These human roles do not
replace daemon, bootstrap, hold or CloudFormation service roles. StateRecovery
must never be substituted for the organization policy templates' administrative
RecoveryRoleArn. Organization administration remains a separate access path.

## Local use

`scripts/write_operator_profiles.py` merges the nonsecret profile settings into
a separate output file, refusing to replace an existing section with different
settings. Back up the current AWS config before installing its output. Existing
credentials are unchanged; no new access keys are created. Each task profile has
a `-migration` variant whose source is the existing default GliderManager path.

```sh
# After Identity Center provisioning and interactive sign-in:
aws sso login --profile glider-access
aws sts get-caller-identity --profile glider-observe

# During migration, using the verified existing source:
aws sts get-caller-identity --profile glider-observe-migration
AWS_PROFILE=glider-observe-migration python3 observer/server.py --help
```

Use `glider-security-access` for Identity Center security access and
`glider-security` for its task role. Select the profile in the AWS CLI with
`--profile`, or for boto3 tools with `AWS_PROFILE`. Verify the reported account
and assumed role before writes. Selecting a role does not authorize a new build,
deployment, propagation trial or cleanup operation.

## Rendering and review

`scripts/render_operator_identities.py` makes no AWS calls. Supply a private
config containing verified account/Region, environment, migration user/group,
permission-set names, Identity Center Region and the persistent launch-template
ID. Also supply the freshly inspected image-build operator permissions policy.
The renderer preserves that builder policy's statements exactly, splitting it
into managed policies to leave space for the isolated smoke supplement.

```sh
python3 scripts/render_operator_identities.py \
  --config .artifacts/identity-split/config.json \
  --image-build-policy .artifacts/identity-split/image-build-policy.json \
  --output-dir .artifacts/identity-split/rendered
```

Review the generated CloudFormation template, seven policies, role trusts,
assumption-only migration grant and two permission-set policies. Rendered files
are private operational inputs, not historical snapshots to deploy blindly.
Deploy the IAM resources in a dedicated operator-identities stack under an
administrative identity, not the foundation service role. Retain change-set and
readback receipts. Identity Center provisioning runs in the management account.

The existing launch-template stack has no service role. Its release path uses
the caller's scoped launch-template permissions through CloudFormation. Other
release stacks explicitly use the foundation service role. This migration does
not modify those existing stacks or roles.

## Accepted limits

IAM limits generation recovery to `GEN#r*` partition keys and allowed attributes;
it does not restrict the sort-key value to SUBMISSION, require a conditional
expression, or validate attribute values or rejection evidence. Recovery can
change the accepted identity fields. Tools must still check exact cycle,
deterministic token, physical absence and recorded rejection before settlement.

Cleanup tag and stack-name grants do not prove absence of in-flight launches or
correct cycle ownership. Preserve the supported controller as the normal cleanup
path. Inspect exact physical inventory and control state before exceptional
deletion; preserve receipts and reconcile controller feedback afterward. Cleanup
has no generation-state deletion or launch permissions. Image smoke tests use
`cloud-glider-sandbox-ami-smoke-*` stack names and `image-smoke-test` purpose tags.
The builder's smoke supplement includes `ec2:GetConsoleOutput` only for instances
in the configured account and Region with both `project=cloud-glider` and
`purpose=image-smoke-test`. AWS supports instance resources and
`ec2:ResourceTag/${TagKey}` for this action; see the
[EC2 authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html).
Wrong or missing tags, another account, another Region and generation-compute
instances receive no console-read grant from this supplement.

Operator CONTROL access can change values beyond normal command switches.
Release can publish executable code and exercise its CloudFormation service
role's privileges. Security can change protected Glider roles and policies.
These are trusted human identities; role switching is not an independent human
approval or fresh-authentication mechanism. No broker, new admission service,
organization policy or workload shutdown mechanism is introduced.

## Validation before removing existing access

1. Validate each policy and rendered CloudFormation template; review scope,
   inline/managed-policy quotas and the complete change set.
2. Deploy alongside existing access. Read back all policies, trusts and source
   assumption permissions. Confirm GliderManager still works.
3. Actually assume every role through the local migration profiles, then through
   the new Identity Center profiles. Simulation alone does not prove SSO access.
4. Exercise Observer reads, logs, Streams, artifacts and quota inspection. Test
   transaction authorization with conditions guaranteed to fail and EC2 writes
   with DryRun. These probes must not change live lifecycle state or resources.
5. Validate actual build/smoke, release, normal command/cleanup and exceptional
   recovery/deletion paths during explicitly approved operations. Do not start a
   propagation cycle, alter another chat's cycle, or delete resources merely to
   test IAM. Record paths that still lack actual successful operation evidence.
6. Only after the required replacement paths succeed, inventory the then-current
   GliderManager grants again, remove superseded grants incrementally, and verify
   both replacements and recovery access after each removal. Keep receipts and
   a concrete administrative rollback path. No removal is part of provisioning.

Readback, CloudFormation CREATE_COMPLETE, policy validation, simulation, forced
conditional failures and EC2 DryRun prove different parts of the path. None is
a substitute for a completed operational workflow. Preserve GliderManager's
existing access whenever a required replacement path remains unverified.

The validator checks actual role assumptions, table/Stream reads, bootstrap logs,
quotas, and artifact listing. Supply `--artifact-key` for an existing nonsecret
artifact to test object reads. Transaction probes require the specific
`ConditionalCheckFailed` cancellation reason; any other failure fails validation.
Cleanup termination DryRun is recorded as untested when no matching instance
exists. Identity Center validation uses `--task-profiles` and verifies both
permission-set source identities before testing the task roles.
`--simulate-smoke-console` evaluates the complete proposed builder permissions
with one allowed smoke-instance case and nine denied cases: wrong project,
wrong purpose, generation compute, missing either/both tags, another account,
another Region and a volume resource. Simulation does not prove deployment or
a successful console API call against an actual smoke instance.

## Initial rollout evidence

On October 6, 2026, the additive operator-identities stack reached
`CREATE_COMPLETE`. All seven installed migration profiles successfully assumed
their intended roles. Live IAM readback matched the rendered policies and trusts;
existing GliderManager grants and the inspected existing role trusts remained
unchanged. Nonmutating authorization checks passed, including DynamoDB rejection
conditions, Streams iterator/record reads, artifact reads and the approved
builder launch DryRun. The complete Python suite passed 418 tests, and the
rendered template passed CloudFormation validation and cfn-lint.

A subsequent isolated smoke operation using the migration builder role launched
its instance and received the guest's success signal, but console collection
failed because the original smoke supplement lacked `ec2:GetConsoleOutput`.
Anna Sarai subsequently authorized live IAM deployment. The console-read
correction was applied through an exact one-role CloudFormation change set and
the operator-identities stack reached `UPDATE_COMPLETE`. Both actual console
collection and authorization DryRun succeeded through `glider-build-migration`
on existing smoke instances, including their guest success markers. All ten
scope simulations also passed against the deployed role. Live readback matched
the updated template and confirmed that original grants and inspected existing
role trusts remained unchanged. No smoke instance or lifecycle state was changed
while applying or validating this permission correction. After rebasing onto
the current main branch, the complete Python suite passed 429 tests.

Identity Center provisioning/sign-in validation is pending. Actual build/smoke,
release, normal command/cleanup and exceptional recovery/deletion workflows remain
unverified under the replacements. There were no live generation instances for
the cleanup termination DryRun. All existing broad grants remain in place.
Private deployment and validation receipts are under
`.artifacts/identity-split/`; preserve them with the historical operational inputs.
