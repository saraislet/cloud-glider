# Deployment decisions and deferred inputs

## Fixed for the sandbox baseline

- Naming: `cloud-glider-{environment}` foundation resources and
  `cloud-glider-{environment}-gen-{000001}` generation stacks.
- Environment: `sandbox`.
- Networking: the operator-deployed `cfn/network.yaml` stack creates one
  dedicated VPC, public subnet with an internet-gateway route, and no-inbound,
  HTTPS-egress-only security group. The immutable generation template assigns
  one ephemeral public IPv4 address to the primary ENI. Daemons cannot mutate
  networking directly and no SSH path is created.
- Region and compute: `us-west-2`, Linux `arm64`, and `t4g.micro` only.
- Legacy CloudFormation readiness: two consecutive healthy heartbeats at a 5-second cadence,
  2-second predecessor polling, and a 10-minute successor wait including stack
  creation. A stopped current owner polls no faster than once per 60 seconds.
  At the generation limit, the current owner exits successfully without polling;
  a new limit takes effect on a fresh run from generation 0 after cleanup.
- EC2 source readiness: [decision 0021](decisions/0021-successor-functional-readiness.md)
  replaces the fixed observation wait with fresh successor-produced functional
  daemon proof. The deployed overlap image retains its prior heartbeat gate until
  a separately approved rebuilt-image release.
- Runtime defaults: `config/runtime-defaults.json` is authoritative for values
  written into initial control state. Daemons read those values from DynamoDB.
- Control store: DynamoDB, not S3; encrypted at rest with an AWS-owned key.
- Monitoring: basic EC2 monitoring, with the separate one-minute status-check
  alarm retained. See [decision 0004](decisions/0004-reduced-cost-operation.md).
- Template identity: bucket/key, immutable S3 VersionId, SHA-256 digest,
  template version, and Git commit or build ID are all required. Daemon artifact
  identity independently requires bucket/key, immutable S3 VersionId, and
  SHA-256 digest. See `docs/decisions/0001-propagation-agent.md`.
- Configured billing thresholds: `$20` monthly sandbox budget; actual alerts at
  `$10`, `$15`, and `$20`; forecast alert at `$20`; anomaly threshold at `$2`;
  notifications only.
  Billing alerts and notification-delivery verification are deferred to V2;
  they do not gate first-pass infrastructure creation.
- Propagation: initial `max_generation=2`, ceiling `3`, and
  `PREFLIGHT_THEN_RETIRE` concurrency. [Decision 0005](decisions/0005-overlapping-handoff.md)
  permits retirement/next-create overlap after handoff; the mode still requires
  preflight before retirement, but not completed deletion before next creation.
- [Decision 0006](decisions/0006-preflight-during-successor-boot.md) permits
  unexecuted next-hop preflight during successor boot, with a fresh joined
  validation gate before handoff. Runtime implementation remains pending.
- Retention: state, audit archives, and log groups are retained by default.

## Required before the first deployment

- Initial immutable generation-template and daemon-artifact object versions and
  SHA-256 digests if the proposed template identity policy is adopted.

## Required before SCP creation or attachment

- Recovery process for an accidentally restrictive SCP.

Parameterized SCP/RCP candidates now live under `iam/organization/`. Render
private candidates only with independently verified values using
`scripts/render_guardrails.py`; source placeholders must not be attached. See
[decision 0003](decisions/0003-permission-guardrails.md) and the
[guardrail runbook](../iam/permission-guardrails.md) for required recovery tests.

## Revisit after the first trial

- Readiness poll, heartbeat freshness, and maximum wait.
- Exact service-quota headroom threshold.
- AMI and daemon release cadence.
- Whether terminal operational errors may set emergency hold automatically or
  require operator confirmation. Policy/template/ownership mismatches remain
  fail-closed regardless.
- Budget and anomaly thresholds.

## Sandbox cost estimate

At 730 hours per month, one steady generation is approximately `$10.42` before
variable log ingestion, API requests, alarms, and retained storage: about
`$6.13` for `t4g.micro`, `$3.65` for one public IPv4 address, `$0.64` for an
8-GiB gp3 root volume, and no charge for basic EC2 monitoring. Detailed
monitoring is disabled; the previous seven-metric model added about `$2.10`.
Temporary two-generation overlap is prorated by the hours of overlap. The `$20`
budget is a planning target while billing alerts are deferred; verify current
`us-west-2` prices in AWS Pricing Calculator before the first trial.

## Revisit in v2

- Automating an operator-only IAM deny as a break-glass response. Version 1
  uses the `HOLD/ACTIVE` record plus `propagation_enabled=false`; the deny
  procedure is documented for manual operator use and is not automated.

## Shared lifecycle and single-item controls

[Decision 0012](decisions/0012-shared-chain-lifecycle.md) adds explicit operator cleanup and cycle fencing.
[Decision 0013](decisions/0013-control-operator-switches.md) puts start, stop, cleanup requests and result messages in CONTROL/GLOBAL. These retain decisions 0003–0007 and the V2 billing deferral.

[Decision 0014](decisions/0014-separated-generation-state.md) separates generation details into their own table and reuses latest-event audit records.

[Decision 0017](decisions/0017-baked-image-propagation-reconciliation.md) reconciles baked-image propagation with the current lifecycle contract and records the legacy deployment.

- [Decision 0018](decisions/0018-remove-continuation-preflight.md) removes disposable continuation previews and gates handoff on the real successor.

[Decision 0019](decisions/0019-direct-ec2-propagation.md) adds cycle-fenced direct EC2 successors and integrates the backend with the baked 2 GiB image. Building and isolated-testing the candidate does not deploy or enable propagation.

- [0022: agent-to-daemon rename](decisions/0022-agent-to-daemon.md) changes names across both backends and release contracts; deploy offline using the daemon migration runbook.
