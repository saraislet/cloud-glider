# Decision 0014: separate generation state and reusable latest audits

Status: accepted by the operator; implementation on the cleanup branch, not deployed.

Keep the existing `cloud-glider-{environment}-state` table for CONTROL/GLOBAL,
CURRENT/GLOBAL, BOOTSTRAP/REQUEST, HOLD, coordination locks, and latest audit
results. Add `cloud-glider-{environment}-generations` for generation state and
exact stack inventory. Preserve the existing control stream and operator switches.

Use separate items per generation in the generation table, rather than a shared
map. This keeps heartbeat writes independent and avoids the 400 KB item limit
when a future fan-out has hundreds or thousands of nodes. A future fan-out must
use unique node IDs within each depth. This change implements the existing
bounded chain only; it does not increase instance limits or enable branching.

Generation keys may be reused after verified cleanup. Request IDs fence old
agents, and cleanup deletes generation state only after stack/resource deletion
is verified. Handoff remains a single transaction across both tables: current
ownership, candidate state, and the latest handoff record change together.

The approved control includes the designated generation table name. Templates
and baked configuration include GenerationTableName. Mismatches fail closed.
Agents can write generation records but not operator records; the hold Lambda
marks errors in the generation table, and cleanup can delete only generation
state and approved stacks. Independent boundary ceilings use the same separation.
No deployment-role grants or organization security-policy changes are included.

Use fixed DynamoDB audit keys: LATEST_HANDOFF, LATEST_HOLD,
LATEST_INITIALIZATION, LATEST_BOOTSTRAP, and LATEST_MIGRATION under
AUDIT#PROPAGATION; LATEST under AUDIT#RECOVERY. Each retains only its latest
result. Ownership/lifecycle conditions fence delayed handoffs and preparation;
recovery writes reject an older timestamp. Logs retain their existing coverage
and retention. These records do not provide a complete event history.
Existing historical audit items are preserved until explicit offline compaction.

The generation table is retained, deletion protected, on demand, and encrypted
with the AWS-owned key. Point-in-time recovery follows the control table's
35-day setting. Review backup and request costs before rollout. Billing alerts
and notification verification remain deferred to V2.

Existing installations require an offline rollout: stop provisioning, remove and
verify legacy generations, create the empty new table, update independently
administered boundaries, migrate legacy control/state, and publish coordinated
agent/template/controller versions. Do not copy or migrate a live chain. Existing
organization policies protecting only the state-table ARN need independent
review for the new table; this change does not alter those policy candidates.

Validation covers cross-table handoff and cleanup, destination mismatch, stale
workers/retries, latest audit reuse, permissions and policy-size ceilings, and
CloudFormation/source consistency. Live permissions and delivery remain untested.
