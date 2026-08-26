# AGENTS.md

## Cloud Glider Agent Rules

### Architecture invariants

- Infrastructure is managed through **CloudFormation**. Prefer declarative changes over direct AWS mutations.
- Propagation/control state is stored in **DynamoDB**.
- Generation `N` must **not be deprovisioned until generation `N+2` is successfully provisioned and validated**.
- A disabled propagation state must prevent creation of additional generations without disrupting already-running instances.
- Lifecycle operations must be **idempotent** and safe to retry.
- Never assume an instance is healthy solely because EC2 reports it as running; use explicit validation criteria.

### AWS safety

- Do not use or create long-lived AWS access keys.
- Prefer **GitHub Actions → OIDC → scoped IAM role → CloudFormation** for AWS changes.
- Apply least privilege to every IAM role and policy.
- Do not broaden IAM permissions to solve deployment failures without documenting why.
- Never modify unrelated AWS resources.
- Tag project resources consistently as belonging to Cloud Glider.
- Treat billing controls, budgets, anomaly detection, and cost alerts as required infrastructure.
- Changes capable of increasing recurring cost require explicit review.

### Git and GitHub

- Work only within the `glider` repository.
- Use a dedicated branch/worktree for each independent task.
- Do not push directly to protected branches.
- Keep changes narrowly scoped to the assigned task.
- Do not modify `.github/workflows/`, deployment permissions, secrets, or repository security settings unless explicitly assigned.
- Never commit credentials, tokens, private keys, account IDs that should remain private, or other secrets.

### Change discipline

Before completing a change:

1. Validate the implementation against these invariants.
2. Run applicable tests, linting, and CloudFormation validation.
3. Review the diff for unintended infrastructure, IAM, lifecycle, or cost changes.
4. Document important architectural decisions or operational changes.
5. Clearly identify anything that could not be tested.

### Documentation

- Keep implementation consistent with the Cloud Glider design document.
- Record significant architectural decisions as ADRs under `docs/decisions/`.
- Update operational/runbook documentation when behavior or recovery procedures change.
- Do not silently change an architectural invariant; propose the change explicitly.

### Agent behavior

- Do not make destructive changes unless required by the assigned task.
- Prefer small, reviewable changes over broad refactoring.
- Preserve existing security boundaries.
- If requirements conflict with an invariant in this file, stop and surface the conflict rather than silently overriding the invariant.
- Never bypass a safety check merely to make a test or deployment succeed.
