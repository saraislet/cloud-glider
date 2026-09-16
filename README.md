# Cloud Glider

Cloud Glider is a deliberately self-propagating EC2 compute pattern. Each
generation may create its successor only through an approved, immutable
CloudFormation template and only after configuration, security, health,
coordination, concurrency, and cost-safety gates pass.

The repository currently implements the safety foundation. Propagation is disabled by default, and the first trial is bounded through generation `2`.

## Project goals

- Safely replace EC2 generations without retiring generation `N` until
  generation `N+1` is healthy and `N+2` has passed its continuation preflight.
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

- `cfn/network.yaml` creates the dedicated VPC, public subnet, internet gateway,
  route, and no-ingress generation security group.
- `cfn/foundation.yaml` creates the retained control table, audit archive,
  category-specific log groups, artifact bucket, and IAM roles.
- `cfn/generation.yaml` defines one generation EC2 instance. It deliberately
  contains no IAM or networking resources.
- `scripts/initialize_control.py` creates the initial DynamoDB control records
  transactionally. It is dry-run unless `--apply` is supplied.
- `scripts/clear_emergency_hold.py` is the operator-only, audited path for
  deleting `HOLD/ACTIVE`; it is also dry-run unless `--apply` is supplied.
- `config/runtime-defaults.json` is the single source for configurable sandbox
  runtime defaults, including Region, instance type, readiness timing,
  propagation limits, and cost thresholds.
- `scripts/validate_generation_inputs.py` rejects non-`us-west-2`, non-ARM64,
  unavailable, or non-EBS AMIs before a stack is submitted.
- `docs/safety-contract.md` records the invariants that implementations and
  infrastructure changes must preserve.
- `docs/audit-logging.md` defines audit categories, fields, retention, and known
  cross-account limitations.
- `iam/runtime-role-assumptions.md` records why public IPv4 assignment adds no
  runtime allow permission and which direct network mutations remain denied.
- `iam/scp-requirements.md` records the future account SCP requirements that
  cannot be safely rendered until the account and administrative principals
  are known.

## Safe starting values

- environment: `sandbox`
- propagation: disabled
- emergency hold: disabled
- maximum generation: `2`
- absolute live-generation ceiling: `3`
- concurrency model: `PREFLIGHT_THEN_RETIRE`
- Region: `us-west-2`
- architecture: Linux `arm64`
- approved instance type: `t4g.micro`
- networking: public subnet, ephemeral public IPv4, and zero inbound rules
- readiness: two healthy heartbeats, 30 seconds apart
- readiness polling: 15 seconds
- readiness timeout: 10 minutes after `CREATE_COMPLETE`
- monthly budget: `$20`, with `$10`, `$15`, and `$20` actual alerts, a `$20`
  forecast alert, and a `$2` anomaly threshold

## Validation

Run the dependency-free checks locally:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_repository.py
```

When `cfn-lint` is installed, also run:

```sh
cfn-lint cfn/network.yaml cfn/billing-alerts.yaml cfn/foundation.yaml cfn/generation.yaml
```

## Deployment order

1. Review the deferred values in `docs/decisions.md`.
2. Validate and deploy `cfn/network.yaml` from an administrative deployment
   principal. Record its subnet and security-group outputs.
3. Deploy `cfn/foundation.yaml` using those outputs from an administrative deployment
   principal, not from a generation instance.
4. Upload immutable generation template and agent artifacts to the versioned
   artifact bucket.
5. Initialize DynamoDB with `scripts/initialize_control.py`; inspect the dry run
   before using `--apply`.
6. Bootstrap generation `000000` or `000001` through the approved operator path while propagation remains disabled.

Do not enable propagation until the negative-security and failure-path tests in
the safety contract pass in the sandbox account.
