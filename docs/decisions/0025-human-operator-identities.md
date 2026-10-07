# 0025: Split human operator access by task

Approved by Anna Sarai on October 6, 2026 after design review. Preserve the
reviewed privilege model and accepted semantic-enforcement risks; use the names
GliderStateRecovery and GliderExceptionalCleanup.

Create separate short-lived identities for observation, operation, state
recovery, exceptional cleanup, image building, release and security
administration. One existing Identity Center human identity receives separate
assumption-only operational and security permission sets. Named CLI profiles
select task roles. The existing IAM user supports migration validation without
creating new long-lived credentials.

Provision these identities alongside GliderManager. Validate actual replacement
assumption and relevant operational paths before removing any existing broad
grant. Retain existing access whenever a required replacement remains unverified.
Provisioning itself removes no grants and changes no runtime roles, workload
configuration, release pins or live cycles.

The roles are trusted operator capabilities. IAM does not enforce cycle evidence,
conditional expressions, safe settlement values or absence of in-flight launches.
Release retains indirect authority through executable artifacts and its
CloudFormation service role. Security retains scoped Glider IAM administration.
No broker or stronger semantic enforcement is introduced by this decision.

The state-recovery role is distinct from the administrative RecoveryRoleArn
exception in organization-policy candidates. Workload service-role trusts remain
separate from human task-role trusts. See [the operator identity runbook](../operator-identities.md).
