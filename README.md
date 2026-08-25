# Cloud Glider

Cloud Glider is a deliberately self-propagating EC2 compute pattern. Each
generation may create its successor only through an approved, immutable
CloudFormation template and only after configuration, security, health,
coordination, concurrency, and cost-safety gates pass.

The repository currently implements the safety foundation. Propagation is
disabled by default, and the first deployment is limited to a single sandbox
generation.

## Project goals

- Safely replace EC2 generations without retiring generation `N` until
  generation `N+2` has been provisioned and validated.
- Keep propagation operator-controlled through durable DynamoDB state and an
  emergency hold mechanism.
- Make lifecycle operations idempotent, observable, and safe to retry after
  partial failures.
- Enforce least-privilege AWS access, immutable deployment artifacts, and
  explicit health validation.
- Bound concurrency and cost so autonomous propagation cannot grow without
  approved limits.
- Manage infrastructure declaratively through reviewable CloudFormation
  changes with a complete audit trail.

## Repository layout

- `cfn/foundation.yaml` creates the retained control table, audit archive,
  category-specific log groups, artifact bucket, and IAM roles.
- `cfn/generation.yaml` defines one generation EC2 instance. It deliberately
  contains no IAM or networking resources.
- `scripts/initialize_control.py` creates the initial DynamoDB control records
  transactionally. It is dry-run unless `--apply` is supplied.
- `docs/safety-contract.md` records the invariants that implementations and
  infrastructure changes must preserve.
- `docs/audit-logging.md` defines audit categories, fields, retention, and known
  cross-account limitations.
- `iam/scp-requirements.md` records the future account SCP requirements that
  cannot be safely rendered until the account and administrative principals
  are known.

## Safe starting values

- environment: `sandbox`
- propagation: disabled
- emergency hold: disabled
- maximum generation: `1`
- absolute live-generation ceiling: `3`
- concurrency model: `PREFLIGHT_THEN_RETIRE`
- approved instance type: `t3.micro`
- readiness polling: 15 seconds
- heartbeat freshness: 5 minutes
- readiness timeout: 15 minutes

## Validation

Run the dependency-free checks locally:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_repository.py
```

When `cfn-lint` is installed, also run:

```sh
cfn-lint cfn/foundation.yaml cfn/generation.yaml
```

## Deployment order

1. Review the deferred values in `docs/decisions.md`.
2. Validate and deploy `cfn/foundation.yaml` from an administrative deployment
   principal, not from a generation instance.
3. Upload immutable generation template and agent artifacts to the versioned
   artifact bucket.
4. Initialize DynamoDB with `scripts/initialize_control.py`; inspect the dry run
   before using `--apply`.
5. Bootstrap generation `000000` or `000001` through the approved operator path
   while propagation remains disabled.

Do not enable propagation until the negative-security and failure-path tests in
the safety contract pass in the sandbox account.
