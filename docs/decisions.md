# Deployment decisions and deferred inputs

## Fixed for the sandbox baseline

- Naming: `cloud-glider-{environment}` foundation resources and
  `cloud-glider-{environment}-gen-{000001}` generation stacks.
- Environment: `sandbox`.
- Networking: existing private subnet and existing security group; no inbound
  rule is created by Cloud Glider.
- Region and compute: `us-west-2`, Linux `arm64`, and `t4g.micro` only.
- Readiness: two consecutive healthy heartbeats at a 5-second cadence,
  2-second predecessor polling, and a 10-minute timeout after `CREATE_COMPLETE`.
- Runtime defaults: `config/runtime-defaults.json` is authoritative for values
  written into initial control state. Agents read those values from DynamoDB.
- Control store: DynamoDB, not S3.
- Template identity: decision pending; the implementation currently supports
  S3 bucket/key, VersionId, SHA-256 digest, and Git commit or build ID, but this
  tuple is not yet adopted as the final policy.
- Cost controls: `$10` monthly sandbox budget; actual alerts at `$5`, `$8`, and
  `$10`; forecast alert at `$10`; anomaly threshold at `$2`; notifications only.
  Infrastructure creation remains pending until a monitored notification
  destination is supplied.
- Propagation: initial `max_generation=2`, ceiling `3`, and
  `PREFLIGHT_THEN_RETIRE` concurrency.
- Retention: state, audit archives, and log groups are retained by default.

## Required before the first deployment

- Existing VPC, private subnet, and no-inbound security-group IDs.
- Approved Linux ARM64 AMI ID for `us-west-2`.
- Owner tag value and monitored alert destination.
- Initial immutable generation-template and agent-artifact object versions and
  SHA-256 digests if the proposed template identity policy is adopted.
- Administrative deployment principal and break-glass principal ARNs.

## Required before SCP creation or attachment

- AWS account ID and AWS Organizations management/delegated administrator
  context.
- Principal ARNs exempted for deployment and break glass.
- Whether Cloud Glider is allowed in only the sandbox Region or in a short
  Region allowlist.
- Existing organization-wide CloudTrail, AWS Config, GuardDuty, Security Hub,
  permission-boundary, and tag-policy controls.
- Recovery process for an accidentally restrictive SCP.

The SCP is intentionally not rendered yet. A placeholder SCP containing guessed
account or administrator ARNs would be a lockout risk.

## Revisit after the first trial

- Readiness poll, heartbeat freshness, and maximum wait.
- Exact service-quota headroom threshold.
- AMI and agent release cadence.
- Whether terminal operational errors may set emergency hold automatically or
  require operator confirmation. Policy/template/ownership mismatches remain
  fail-closed regardless.
- Budget and anomaly thresholds.

## Revisit in v2

- Automating an operator-only IAM deny as a break-glass response. Version 1
  uses the `HOLD/ACTIVE` record plus `propagation_enabled=false`; the deny
  procedure is documented for manual operator use and is not automated.
