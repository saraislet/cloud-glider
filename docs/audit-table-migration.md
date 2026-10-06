# Audit table migration runbook

Preparation is local-only; live operations require an operator-authorized rollout.
The operator authorized the sandbox rollout on October 6, 2026. Its exact live
receipts remain under ignored `.artifacts/audit-split/aws-rollout/`.

## Prepared scope

`cfn/foundation.yaml` adds `cloud-glider-{environment}-audit` and redirects the
hold Lambda and both daemon identity policies. `cfn/permission-boundaries.json`
contains matching narrow boundary changes. `bootstrap/handler.py` and its rendered
CloudFormation source block START on unmigrated audit configuration; STOP and
cleanup remain available. New CONTROL initialization includes audit_table_name.
Both daemon handoffs, bootstrap preparation, initialization, lifecycle migration,
and daemon rename audit writes use the new audit table. AUDIT#RECOVERY is unchanged.

No generation/launch-template parameter is added: destination identity is pinned
by the new daemon source and environment, then checked against approved CONTROL.
Publish coordinated artifact/template/AMI pins before enabling the next cycle.
Historical config/releases files describe prior releases and are not rewritten.

## Future rollout order

1. Verify AWS account, Region, current stack templates and parameters, protection
   settings, deployment permissions, boundary-admin permissions, and any applicable
   SCP/RCP coverage. The foundation role needs the narrow
   `iam/foundation-audit-table-supplement.json` grant, and the operator needs
   `iam/glider-manager-audit-access.json`. Render account placeholders and review
   these separately from runtime policies. Foundation boundary statements are
   compacted to fit IAM's size limit; the profile selector covers the same seven
   operations in the pinned SDK model, with a regression check for future additions.
   Use a managed operator policy if the aggregate user inline-policy size limit is
   full. The repository's organization candidates are not proof of live policy
   coverage. Review additional on-demand storage, requests, and PITR cost.
2. Inventory all readers and writers of both audit partitions, including external
   release scripts and ignored `.artifacts/` folders across local worktrees.
   Local overlap and daemon rollout scripts write AUDIT#RELEASE; benchmark, AMI,
   and family rollout scripts also write AUDIT#PROPAGATION. Their audit targets
   and monitoring readers were updated locally; see the local producer inventory
   below. Verify operator permissions before resumption; do not silently grant
   runtime roles release access. `prepare_release_audit.py` can prepare operator release
   writes from complete wire-format items without contacting AWS.
3. Stop normally with propagation disabled, complete normal generation cleanup,
   and verify AWS stacks/instances and in-flight creation are absent. Pause bootstrap,
   controller delivery, hold reporting, release scripts, and all other audit writers.
   An idle-looking DDB snapshot alone does not prove empty AWS inventory.
4. Back up/export the state table and retain templates, policy versions, release
   pins, and full snapshots. Query each audit PK with strongly consistent reads,
   following every LastEvaluatedKey. Preserve DynamoDB wire-format values, including
   nested maps, lists, numbers, and binary data. Do not use a filtered partial export.
   Export lifecycle records, both locks, and complete generation inventory too.
5. Review future unexecuted CloudFormation change sets for the audit table, identity
   policies, independently administered boundaries, and bootstrap/controller code.
   Preserve live parameters and existing resources; reject unintended replacements.
   Apply only during the authorized offline window. No change set is created by
   this implementation. Do not invent deployment-role grants to make it succeed.
6. Generate and review copy requests locally. Each source condition and destination
   Put share a transaction. Existing destination keys are never overwritten. Submit
   reviewed requests only in the later authorized rollout; after a partial failure,
   take fresh snapshots and regenerate. Equal destination items are skipped;
   collisions fail closed.
7. Obtain fresh, complete source and destination snapshots while writers remain
   paused. Verify every key and full item value, not just counts. The planner emits
   cutover only after all source items match the supplied destination snapshot.
   Apply the reviewed cutover transaction to add CONTROL.audit_table_name and refresh
   BOOTSTRAP.control_sha256 without changing counters, switches, or other pins.
   Coordinate new daemon artifact, approved template, and baked AMI release pins
   using the reviewed release procedure; the cutover request does not repin them.
8. Validate initialization/bootstrap audit delivery, hold handling, both handoff
   transactions, duplicate retries, destination mismatch, disabled propagation,
   ambiguous readiness, and failed handoff. Use a bounded authorized cycle only
   after the coordinated release is ready. Confirm no old producer writes audit
   items into the state table, and confirm the observer still receives lifecycle
   state and generation events.
9. Pause writers again and take fresh snapshots before preparing source removal.
   Each removal transaction checks the destination copy and conditionally deletes
   only an exact requested audit key from the source. Never delete a partition
   using an unconditioned batch operation. Keep backups and the manifest afterward.

## Local request preparation

The snapshot file is a JSON object with these required fields:

- `control`, `current`, and `request`: complete CONTROL/GLOBAL, CURRENT/GLOBAL,
  and BOOTSTRAP/REQUEST items in DynamoDB wire format.
- `locks`: an empty array after both PROPAGATION and PROVISIONING locks are absent.
- `generation_items`: an empty array after the complete generation-table inventory
  and AWS resource inventory have been verified empty.
- `audit_items`: a complete array containing only the two requested audit partitions.

Attestation flags assert real operator checks; they are not permission to bypass
an active cycle. The planner reads files and prints JSON, with no AWS client or
apply mode. All commands below are local:

```sh
python3 scripts/prepare_audit_migration.py \
  --snapshot /path/to/offline-source.json \
  --writers-paused --inventory-verified-empty > /path/to/copy-plan.json

python3 scripts/prepare_audit_migration.py \
  --snapshot /path/to/fresh-offline-source.json \
  --destination-snapshot /path/to/destination-items.json \
  --writers-paused --inventory-verified-empty > /path/to/verified-cutover-plan.json

python3 scripts/prepare_audit_migration.py \
  --snapshot /path/to/fresh-offline-source.json \
  --destination-snapshot /path/to/fresh-destination-items.json --prepare-removal \
  --writers-paused --inventory-verified-empty > /path/to/verified-removal-plan.json

python3 scripts/prepare_release_audit.py --item /path/to/release-item.json \
  > /path/to/release-audit-request.json
```

Each entry in copy_requests/removal_requests is a separate TransactWriteItems
request. Groups are bounded to 100 actions and a conservative 3 MB serialized
request size. The manifest records sorted keys, count, and a SHA-256 of complete
source wire-format items. cutover_request is null until destination verification;
removal_requests is empty unless explicitly requested after verification.

Conditional expressions compare every attribute present in the snapshot, but
cannot detect arbitrary new attributes not in that snapshot. Pausing all writers,
taking fresh snapshots, and performing full readback remain mandatory. Expiring
source TTL items may disappear during the window; reconcile fresh snapshots and
preserve the exported backup rather than forcing a conditional failure through.

## Rollback

Before source deletion, keep writers paused, restore reviewed old source releases
and policies, and conditionally restore the original CONTROL and its matching
BOOTSTRAP fingerprint together from the backup. Verify the original audit records
still exist before resuming old writers. Keep the new audit table retained.

After any source deletion or new destination-only audit writes, first restore all
required audit items to the source from verified destination snapshots/backups,
with collision checks. Preserve audit writes created during the new release.
Only then restore the old configuration and policies. Do not restore a whole
state-table backup over newer lifecycle state or reset the cycle counter.

## Evidence boundary

Local unit tests, source consistency checks, and cfn-lint validate the prepared
implementation. They do not establish live policy coverage, deployed producer
inventory, delivery, costs, or successful AWS migration. These remain rollout
checks. No live audit counts or AWS snapshots are fabricated during preparation.

## Local producer inventory

The expanded search included ignored `.artifacts/` folders in local Cloud Glider
worktrees and the canonical checkout. Sixteen Python scripts were updated locally
without executing them: twelve audit writes and eleven audit reads. Audit writes
retain their original transactions with state changes; state and generation
operations retain their original destinations. Audit readers use the audit table
for these two partitions. These scripts require the coordinated audit-table
rollout before reuse.

| Local worktree | Scripts under `.artifacts/` |
| --- | --- |
| `be47` | `daemon-rollout/set_trial_cap.py`, `daemon-benchmark/set_limit.py`, `daemon-benchmark-repeat/set_limit.py`, `durable-timing/prepare_state.py`, `instance-types/prepare_state.py` |
| `5f62` | `overlap-rollout/prepare-idle-release.py`, `activate-release.py`, `set-idle-cap.py`, `monitor.py`, `collect-benchmark-evidence.py` |
| `950a` | `family-rollout/manage.py` |
| `c0fa` | `ami/browser_benchmark_prepare.py`, `browser_release_state.py`, `browser_restore_limit.py`, `monitor_benchmark.py`, `verify_final.py` |

The ignored `.artifacts/audit-split/local-producers/manifest.json` records absolute
paths, before/after hashes, and backups for this local update. Saved transaction
JSON and snapshots remain historical evidence: do not submit old requests after
cutover. Regenerate requests with the updated scripts and fresh state. Local-only
script edits are not distributed by the repository PR. Future code-change searches
must include ignored operational scripts and generated inputs; historical receipts
and snapshots should remain intact.
