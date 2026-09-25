# Cloud Glider

This is a sanitized source copy. Read [public repository preparation](docs/public-repository.md) before deployment; historical recovery artifacts are examples only.

Cloud Glider is a deliberately self-propagating EC2 compute pattern. Each
generation may create its successor only through an approved, immutable
CloudFormation template and only after configuration, security, health,
coordination, concurrency, and cost-safety gates pass.

The repository implements the safety foundation and the first-pass propagation
agent. Propagation is disabled by default, and the first trial is bounded
through generation `2`.

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
- `cfn/bootstrap.yaml` defines the operator bootstrap Lambda and request-only
  stream trigger; see [the bootstrap runbook](docs/bootstrap.md).
- `cfn/generation.yaml` defines one generation EC2 instance. It deliberately
  contains no IAM or networking resources.
- `agent/` contains the dependency-free Python propagation state machine and
  its AWS CLI adapter.
- `scripts/build_agent_artifact.py` creates the deterministic tarball consumed
  by generation user data.
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
- `iam/scp-requirements.md` records the account SCP requirements and links to
  the private policy renderer and reviewed rollout procedure.

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
- readiness: two healthy heartbeats, 5 seconds apart
- readiness polling: 2 seconds
- readiness timeout: 10 minutes for the successor wait, including stack creation
- idle current-owner polling: at least 60 seconds when stopped or at the limit;
  candidates retain the configured heartbeat cadence
- monitoring: free EC2 basic metrics and one-minute status checks; the separate
  per-generation status alarm remains enabled
- state-table encryption: AWS-owned key (encrypted at rest without billed KMS usage)
- configured billing thresholds (alerts deferred to V2): `$20` monthly budget,
  with `$10`, `$15`, and `$20` actual alerts, a `$20` forecast alert, and a `$2`
  anomaly threshold

See [the reduced-cost decision](docs/decisions/0004-reduced-cost-operation.md)
for the implemented scope, release procedure, and future fan-out cost targets.

## Validation

Run the dependency-free checks locally:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate_repository.py
```

Build the agent artifact and record the printed SHA-256 digest:

```sh
python3 scripts/build_agent_artifact.py --output dist/cloud-glider-agent.tar.gz
```

Upload that exact tarball under `generation/` in the versioned artifact bucket.
Pass its bucket, key, immutable S3 VersionId, and digest to both the initial
generation stack and `scripts/initialize_control.py`.

When `cfn-lint` is installed, also run:

```sh
cfn-lint cfn/permission-boundaries.json cfn/network.yaml cfn/billing-alerts.yaml cfn/foundation.yaml cfn/generation.yaml cfn/bootstrap.yaml
```

## Permission guardrails

Runtime deployment requires independently administered role boundaries and an
exact versioned generation template URL. Follow [the guardrail rollout](iam/permission-guardrails.md)
before deployment. Organization policies are local review candidates and are not
attached automatically. See [decision 0004](docs/decisions/0003-permission-guardrails.md).

## Deployment order

Pass `--region us-west-2` explicitly for sandbox CloudFormation and resource
commands, including change-set inspection, execution, waiters, and drift checks.
Do not rely on the CLI's default Region or expect an ARN to select the endpoint.

For the sandbox's interrupted foundation deployment, follow the
[foundation recovery runbook](docs/foundation-recovery.md) before retrying.
It separates the reviewed import of surviving resources from normal deployment.

1. Review the deferred values in `docs/decisions.md`.
2. Validate and deploy `cfn/network.yaml` from an administrative deployment
   principal. Record its subnet and security-group outputs.
3. Deploy the separate boundary stack and migrate deployment permissions using
   [the guardrail runbook](iam/permission-guardrails.md), then deploy
   `cfn/foundation.yaml` using those outputs from an administrative deployment
   principal, not from a generation instance.
4. Upload immutable generation template and agent artifacts to the versioned
   artifact bucket.
5. Initialize DynamoDB with `scripts/initialize_control.py`, including both
   template and agent artifact identity tuples; inspect the dry run before
   using `--apply`.
6. Deploy the reviewed bootstrap trigger and prepare the separate bootstrap
   record, then toggle its `bootstrap_requested` Boolean to start generation `000000` using [the bootstrap runbook](docs/bootstrap.md).
   Keep propagation disabled for the initial inspection; the Lambda itself accepts
   either propagation setting.

Do not enable propagation until the negative-security and failure-path tests in
the safety contract pass in the sandbox account.

See `docs/propagation-agent.md` for the state machine, record shapes, failure
classification, and first-trial procedure.
