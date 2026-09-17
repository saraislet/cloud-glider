# Deployment decisions and deferred inputs

## Fixed for the sandbox baseline

- Naming: `cloud-glider-{environment}` foundation resources and
  `cloud-glider-{environment}-gen-{000001}` generation stacks.
- Environment: `sandbox`.
- Networking: the operator-deployed `cfn/network.yaml` stack creates one
  dedicated VPC, public subnet with an internet-gateway route, and no-inbound,
  HTTPS-egress-only security group. The immutable generation template assigns
  one ephemeral public IPv4 address to the primary ENI. Agents cannot mutate
  networking directly and no SSH path is created.
- Region and compute: `us-west-2`, Linux `arm64`, and `t4g.micro` only.
- Readiness: two consecutive healthy heartbeats at a 5-second cadence,
  2-second predecessor polling, and a 10-minute timeout after `CREATE_COMPLETE`.
- Runtime defaults: `config/runtime-defaults.json` is authoritative for values
  written into initial control state. Agents read those values from DynamoDB.
- Control store: DynamoDB, not S3.
- Template identity: bucket/key, immutable S3 VersionId, SHA-256 digest,
  template version, and Git commit or build ID are all required. Agent artifact
  identity independently requires bucket/key, immutable S3 VersionId, and
  SHA-256 digest. See `docs/decisions/0001-propagation-agent.md`.
- Cost controls: `$20` monthly sandbox budget; actual alerts at `$10`, `$15`, and
  `$20`; forecast alert at `$20`; anomaly threshold at `$2`; notifications only.
  Infrastructure creation remains pending until a monitored notification
  destination is supplied.
- Propagation: initial `max_generation=2`, ceiling `3`, and
  `PREFLIGHT_THEN_RETIRE` concurrency.
- Retention: state, audit archives, and log groups are retained by default.

## Required before the first deployment

- Initial immutable generation-template and agent-artifact object versions and
  SHA-256 digests if the proposed template identity policy is adopted.

## Required before SCP creation or attachment

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

## Sandbox cost estimate

At 730 hours per month, one steady generation is approximately `$12.52` before
variable log ingestion, API requests, alarms, and retained storage: about
`$6.13` for `t4g.micro`, `$3.65` for one public IPv4 address, `$0.64` for an
8-GiB gp3 root volume, and up to `$2.10` for seven detailed-monitoring metrics.
Temporary two-generation overlap is prorated by the hours of overlap. The `$20`
budget is a guardrail rather than a guarantee; verify current `us-west-2` prices
in AWS Pricing Calculator before the first trial.

## Revisit in v2

- Automating an operator-only IAM deny as a break-glass response. Version 1
  uses the `HOLD/ACTIVE` record plus `propagation_enabled=false`; the deny
  procedure is documented for manual operator use and is not automated.
