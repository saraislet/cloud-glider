# 0003: Separately administered permission ceilings and organization guardrails

Status: implementation proposed for reviewed deployment; not applied to AWS.

## Context

The runtime role policies are scoped, but broader future identity grants could
expand their permissions. The foundation deployer can create child runtime roles
and policies. A deployer-controlled boundary would not provide independent
protection. The prior operator review also found broad CloudFormation grants;
existing stack operations can reuse a service role without a new PassRole check.

The approved template identity previously depended on agent validation alone.
The generation role's tagging statement permitted tagging existing resources,
despite retirement authorization depending on those same tags.

## Decision

Maintain five role ceilings in a separate security-administered CloudFormation
stack. Runtime templates require the matching retained managed boundary.
Foundation role attachment is an administrative migration of an existing role,
not a new role or import. The foundation ceiling permits child creation and
boundary attachment only with the correct role-specific boundary, and cannot
edit boundary policies or its own role. Its resource-management ceiling remains
coarser than the identity grants; it is still a trusted infrastructure deployer.

Require an exact versioned TemplateUrl at generation/first-bootstrap creation.
An empty approval disables creation to permit initial foundation setup. Keep
retirement and preflight deletion separate from the creation gate. Require
ec2:CreateAction=RunInstances for creation tags. Fix the existing agent's missing
CONTROL condition-check permission, malformed log-stream ARNs, and S3 bucket
metadata condition scope without granting operator-state writes.

Provide four deny-only SCP candidates and one RCP candidate, rendered locally
only after explicit account, organization and administrator inputs are supplied.
Do not guess administrative exceptions or attach anything automatically. Scope
Region denial to runtime principals so global administration and the separately
reviewed us-east-1 billing stack remain possible. Protect audit/state durability
in steady state with explicit recovery procedures for CloudFormation maintenance.

Use an RCP to deny untrusted external identities access to Glider S3, DynamoDB and
Logs resources, and enforce S3 transport and supported service-source conditions.
Defer STS federation RCPs until actual GitHub and recovery trust requirements are
verified. Supply a narrow operator deployment-policy replacement candidate;
removing existing broad grants requires a live effective-permissions inventory.

## Consequences

Initial deployment now requires the security boundary stack first. Releases
coordinate the boundary, foundation, bootstrap and CONTROL template approval.
Disagreement fails closed. Retained old generations remain inspectable and
retirable through approved lifecycle operations.

Organization guardrails require a member account, tested independent recovery,
policy validation and real sandbox integration tests. The protected-resource SCP
intentionally restricts normal foundation maintenance; it must be attached after
initial reconciliation. Offline tests are not proof of live AWS authorization.

This adds no compute, paid service, multi-account propagation, automated IAM
shutdown or recovery automation. Health, ownership, conditional writes and live
generation limits remain application invariants. See
[permission guardrails](../../iam/permission-guardrails.md) for migration,
limitations and required failure-path validation.
