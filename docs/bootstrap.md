# Bootstrap and cleanup runbook

Use one DynamoDB item: `CONTROL/GLOBAL` in `cloud-glider-sandbox-state`, Region
`us-west-2`. Generation details live separately in `cloud-glider-sandbox-generations`. Pick a small generation limit. Billing alerts and notification verification remain deferred to V2.

## AWS Lambda

The existing `cloud-glider-sandbox-bootstrap` Lambda launches the first generation.
This release adds the three switches below, automatic request preparation,
cleanup, and result messages. Follow deployment/migration below before first use.

## Start, stop, or clean up through DynamoDB

1. Open DynamoDB → Tables → `cloud-glider-sandbox-state` → Explore table items.
2. Edit the item with `PK=CONTROL`, `SK=GLOBAL`.
3. Set **one Boolean** to true and save:

   | Switch | Action |
   | --- | --- |
   | `start_requested` | Bootstrap if needed and enable propagation; otherwise enable the existing chain |
   | `stop_requested` | Stop propagation and keep running instances |
   | `cleanup_requested` | Stop launches, delete the generation chain, and reset for another start |

4. Refresh the item. Read `operation_status` and `last_result`.

The Lambda checks prerequisites automatically and resets request switches to false.
You do not need to check READY, schema versions, or cleanup status before requesting
an action. Leave other attributes unchanged. No manual Lambda invocation is needed.

Examples of `last_result`:

- “Bootstrap submitted. Propagation follows its current setting.”
- “Start rejected: cleanup is still running or needs attention. Start again after it finishes.”
- “Request rejected: choose just one switch: start, stop, or cleanup.”
- “Cleanup complete. Ready for another start.”

Processing is asynchronous; DynamoDB does not show a Lambda popup. Refresh to see
the result. DynamoDB triggers each request. While cleanup remains active, the
Lambda schedules one retry about a minute later. Each completed schedule deletes
itself; completion or a problem needing attention stops further scheduling.
There is no idle timer or scheduled bootstrap retry. SUBMITTED means AWS accepted creation, not workload
health. An emergency hold blocks start.

Cleanup waits for in-flight creation, deletes verified stacks through CloudFormation,
and checks residual resources before resetting. Only the latest cleanup is kept.
Holds, shared infrastructure, artifacts, and audit/log storage remain; some costs continue.

## If an operation fails

1. Read `last_result`. For bootstrap delivery failures, inspect the submitted stack and provisioning marker before retrying delivery; no permanent timer retries bootstrap. Check `/aws/lambda/cloud-glider-sandbox-bootstrap` logs and stack events.
2. Resolve the reported submission, ownership, deletion, or residual-resource problem.
3. Submitted bootstrap retries verify the exact stack and restore inventory before clearing a matching marker. If `LOCK/PROVISIONING` remains, confirm the submission outcome and that its process has finished before conditionally clearing it. Age or one empty listing is insufficient.
4. Request cleanup again to resume it, or use the recovery helper below. Start during cleanup is rejected, not queued.

Do not replace the lifecycle item, reuse request numbers, reset CURRENT manually,
or bypass checks with direct EC2 termination or force deletion. For an incident,
use the approved emergency-hold path; cleanup never clears a hold.

## Python alternatives and recovery helpers

Run from the repository root. Preview first, then repeat with `--apply`.
These are alternatives to console edits and **still rely on the Lambda**.

```sh
python3 scripts/lifecycle.py start
python3 scripts/lifecycle.py stop
python3 scripts/lifecycle.py cleanup
```

Read the result in CONTROL/GLOBAL. If the Lambda itself fails, restore it or its
trigger first; these scripts do not bypass it.

### Optional initial bootstrap preparation

Initialize approved settings with `scripts/initialize_control.py`. The Lambda
prepares the first request automatically on start. To prepare it explicitly:

```sh
python3 scripts/request_bootstrap.py
```

The advanced `lifecycle.py bootstrap`, `enable`, and `disable` commands retain
separate bootstrap/propagation control for an alternative approach.

### Resume an inspected failed cleanup

```sh
python3 scripts/lifecycle.py resume-cleanup
```

Resume preserves inventory and restarts the 30-minute cleanup deadline. The
controller allows three minutes for an outstanding submission marker to settle.

## Deploy or migrate this release

1. Deploy reviewed boundaries, the cleanup schedule group, and its delivery role through the independently administered boundary stack. Review runtime permissions; require no state-table replacement.
2. Create the retained `cloud-glider-sandbox-generations` table through the reviewed foundation change set while operator provisioning is paused; do not replace the control table. Publish approved, immutable generation-template and agent-artifact versions with their SHA-256 digests. The template requires `RequestId` and `GenerationTableName`.
3. For an existing installation, complete the offline migration below. For a new one, initialize approved CONTROL/CURRENT settings.
4. Apply coordinated reviewed templates and artifact settings.
5. Verify CONTROL stream filters, Lambda concurrency one, cleanup-only schedules, and IAM. Remove the old recurring rule.

### Existing installation: offline migration

1. Disable legacy propagation and withdraw bootstrap authorization.
2. Pause the old event source and operator provisioning. Wait for Lambda/CloudFormation operations to settle; inspect ambiguous CREATING requests.
3. Delete legacy generation stacks through CloudFormation. Verify instance/root-volume deletion and inspect residual assets.
4. Reconcile and release old locks; preserve holds.
5. Confirm the separate generation table exists and is empty. Preview, inspect, then apply:

   ```sh
   python3 scripts/migrate_lifecycle.py --operator-path-paused
   ```

6. Complete the coordinated rollout, then resume the event source.

Never migrate a live chain. Migration keeps an audit snapshot and initializes the
cycle once. It rejects an already migrated lifecycle; do not reset its counter.

## Reference and validation

CONTROL holds the operator switches and messages alongside approved settings. The control table also holds CURRENT, BOOTSTRAP, holds/locks, and reusable latest-event audit records. Heartbeats and stack inventory use the separate generation table. Earlier audit history remains until an explicit offline compaction; normal cleanup does not erase it.
BOOTSTRAP/REQUEST holds controller-managed launch permission, cycle identity,
and cleanup state. No routine edits to that item are needed. HOLD stays independent.

For Lambda source edits, run `python3 scripts/render_bootstrap_template.py`.
Run unit tests, repository safety checks, renderer `--check`, and cfn-lint.
Before live use, test holds, conflicting/stale requests, submission races,
failed deletion, residual resources, and reset conflicts in the sandbox.
Local tests do not verify live IAM or delivery.

See [decision 0013](decisions/0013-control-operator-switches.md) for this interface
and [decision 0012](decisions/0012-shared-chain-lifecycle.md) for cleanup mechanics.

## Minimal baked AMI

Use `AgentDeliveryMode=baked`, `/dev/sda1` and a 2 GiB root only with an image
containing the current lifecycle agent. The previously deployed minimal image
predates the current request/table contract. Follow the [reconciliation and
release runbook](minimal-ami.md) before a coordinated image/controller release.
