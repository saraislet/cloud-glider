# AGENTS.md

## Cloud Glider Agent Rules

### Design authority and first-pass scope

- Keep implementation consistent with the **Cloud Glider design doc - simplified first pass**.
- Optimize for a system that is understandable, bounded, observable, and easy to stop.
- Start in one AWS account and one Region, with a small EC2 instance type and a low `max_generation`.
- Defer multi-account support, automated IAM shutdown, advanced recovery automation, stronger lease coordination, and automated quarantine or cleanup until the basic propagation cycle works reliably.
- Do not add deferred complexity without an explicit design decision.

### Architecture and lifecycle invariants

- Manage generation creation and deletion through **CloudFormation**. Prefer declarative changes over direct AWS resource mutations.
- A generation template creates one EC2 generation. A small Python agent on that generation coordinates propagation.
- Store propagation control and basic generation state in **DynamoDB**.
- Generation `N` may create `N+1` only from the approved, versioned generation template and only after confirming that propagation is enabled.
- Read DynamoDB again immediately before provisioning. If `propagation_enabled` is false or `emergency_hold` is true, do not create another generation.
- Disabling propagation must stop new generations without disrupting already-running instances.
- Wait for `N+1` to pass explicit health validation before considering retirement of `N`.
- Do not retire `N` until `N+1` is healthy and the continuation check for `N+2` passes.
- For the first pass, the continuation check is an unexecuted CloudFormation **CREATE change set** for `N+2`, a fresh DynamoDB control check, and basic quota and capacity checks.
- Provision a live `N+2` during the continuation check only when stronger proof is explicitly required.
- Prefer two or fewer live generations. Three may exist temporarily to prove the next hop; four are never allowed.
- Lifecycle operations must be idempotent, deterministic, and safe to retry after partial failure.

### Health and readiness

- Never treat EC2 `running` state as proof that a generation is healthy.
- Authoritative readiness must verify the expected generation and approved template, current control state, and valid handoff ownership in addition to workload health.
- Treat EC2 Application Status Checks as corroborating telemetry and an alert signal, not as the authoritative readiness or retirement gate.
- Preserve the predecessor whenever health, identity, ownership, control state, or continuation results are missing or ambiguous.

### IAM and AWS safety

- `CloudGliderAgentRole` may read and update only Cloud Glider DynamoDB records and operate only approved CloudFormation generation stacks.
- The agent may pass only the designated CloudFormation service role. Restrict `iam:PassRole` to that role and the CloudFormation service.
- The CloudFormation service role may create and delete only resources required by the approved generation template.
- Generation instances must never create or modify IAM roles, policies, permissions, or trust relationships.
- Apply least privilege to every role and policy. Do not broaden permissions merely to make a deployment pass.
- Require IMDSv2 and use an approved, versioned generation template.
- Do not use or create long-lived AWS access keys. Prefer GitHub Actions OIDC and narrowly scoped deployment roles for repository-driven AWS changes.
- Never modify unrelated AWS resources.

### DynamoDB state

- Keep the first schema small:
  - `CONTROL`: `propagation_enabled`, `emergency_hold`, `max_generation`, and `max_live_generations` (absolute maximum `3`).
  - `CURRENT`: current generation number, current stack and instance identifiers, and status.
- Use conditional writes when changing `CURRENT`; an unexpected generation must not silently take ownership.
- Treat unauthorized or conflicting control-state changes as a safety incident. Stop new propagation and preserve live generations for inspection.

### Operations and cost

- Before the first propagation test, confirm billing and operational notifications work, set a small `max_generation`, and keep propagation disabled.
- Bootstrap the first generation only through the approved operator path. Enable propagation only when ready to observe the complete handoff.
- To stop normally, set `propagation_enabled = false`. For an incident, also set `emergency_hold = true`.
- Before cleanup, verify that no new generation is being provisioned.
- Use a small sandbox instance type and avoid unnecessary quota increases.
- Maintain AWS Budgets thresholds, a forecast alert, a CloudWatch `EstimatedCharges` alarm, and Cost Anomaly Detection notifications.
- Tag Cloud Glider resources consistently for cost attribution.
- Billing alerts are warnings, not the circuit breaker. When an alert fires, disable propagation first, then inspect live stacks, instances, and current spend.
- Changes capable of increasing recurring cost require explicit review.

### Git, review, and documentation

- Work only within the `glider` repository and use a dedicated branch or worktree for each independent task.
- Do not push directly to protected branches. Keep changes narrowly scoped and reviewable.
- Do not modify `.github/workflows/`, deployment permissions, secrets, or repository security settings unless explicitly assigned.
- Never commit credentials, tokens, private keys, or other secrets.
- Record significant architectural decisions under `docs/decisions/` and update runbooks when behavior or recovery procedures change.
- Do not silently change an invariant. Surface the conflict and propose the design change explicitly.

### Completion checks

Before completing a change:

1. Validate it against these invariants and the simplified design document.
2. Run applicable tests, linting, and CloudFormation validation.
3. Review the diff for unintended infrastructure, IAM, lifecycle, state, security, or cost changes.
4. Exercise relevant failure paths, including disabled propagation, emergency hold, duplicate execution, ambiguous health, and failed handoff.
5. Document important operational or architectural changes and identify anything that could not be tested.

Never bypass a safety check merely to make a test or deployment succeed.
