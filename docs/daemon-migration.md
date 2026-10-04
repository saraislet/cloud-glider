# Daemon rename migration runbook

This release changes names, not propagation semantics. Repository changes alone
do not rename deployed resources. Deploy only between cycles through the approved
operator path. The existing GitHub deployment workflow is unchanged and is not a
migration orchestrator.

## Release contracts

| Old name | New name |
| --- | --- |
| `agent/`, `agent.py`, `ec2_agent.py` | `daemon/`, `daemon.py`, `ec2_daemon.py` |
| `build_agent_artifact.py` | `build_daemon_artifact.py` |
| `Agent*`, `Ec2Agent*` | `Daemon*`, `Ec2Daemon*` |
| `agent_artifact_*`, `agent_delivery_mode`, `agent_operations_log_group` | corresponding `daemon_*` fields |
| functional proof `agent_live` | `daemon_live` |
| image manifest `agent_sha256`, AMI tag `agent-sha256` | `daemon_sha256`, `daemon-sha256` |
| AMI purpose `agent-image`, diagnostics purpose `agent-diagnostics` | `daemon-image`, `daemon-diagnostics` |
| `Agent*` CloudFormation parameters, logical IDs and outputs | corresponding `Daemon*` names |
| `--agent-*` operator flags and `AGENT_SHA256` build variable | `--daemon-*`, `DAEMON_SHA256` |
| `/opt/cloud-glider/agent.tar.gz` | `/opt/cloud-glider/daemon.tar.gz` |
| `cloud-glider-sandbox-agent` role/profile | `cloud-glider-sandbox-daemon` |
| `cloud-glider-sandbox-agent-boundary` policy | `cloud-glider-sandbox-daemon-boundary` |
| `/cloud-glider/sandbox/operations/agent` | `/cloud-glider/sandbox/operations/daemon` |

The profile retains its `/cloud-glider/` IAM path. Other environments use their
existing environment value. S3 artifact keys are new immutable objects; never
rewrite old object versions or release receipts. The service and executable
retain their existing neutral names.

## Quiesce and capture evidence

1. Disable propagation and pause operator starts, bootstrap event delivery and
   cleanup retry delivery. Allow accepted operations to reconcile. Use the old
   release's supported cleanup to remove the old cycle before replacing its roles.
2. Confirm cleanup is complete, propagation/bootstrap are disabled, no start/stop/
   cleanup command is pending, no provisioning or propagation lock exists, and no
   generation instance, generation stack or unresolved submission remains. Check
   AWS inventory as well as state; an empty state table is insufficient evidence.
   Preserve an existing emergency hold; do not clear it for the migration.
3. Export consistent CONTROL, CURRENT, BOOTSTRAP/REQUEST and HOLD snapshots,
   generation inventory, stack templates/parameters, IAM policies/boundaries and
   launch-template pins. Record the request and command counters for rollback.

## Prepare and apply the coordinated release

1. Run the repository checks and SDK-backed tests. Build the deterministic daemon
   archive, bake a new image and run image metadata, integrity and isolated
   cold-boot smoke checks. Record new hashes, image ID and object version IDs in
   a new release receipt. Prior agent-image receipts cannot approve this image.
2. Prepare change sets for the security boundary, foundation, bootstrap and
   persistent launch template. Review resource additions/removals/replacements:
   renamed logical IDs represent new resources, not adoption of existing ones.
   Preserve retained tables, audit resources, network and unrelated resources.
   The generation table keeps its deployed NEW_AND_OLD_IMAGES stream.
   Keep old logs and boundary evidence; do not interpret retention as completion
   of the rename. Have security administrators review rendered organization
   policies and narrowly scoped operator/deployment grants for the new ARNs.
   Update allowlists without adding broader actions, wildcards or runtime IAM rights.
   Include the image-build operator's snapshot/image tag conditions: new candidates
   use `purpose=daemon-image` and `daemon-sha256`. Keep legacy `agent-image`
   selectors only where needed to inspect or clean up historical candidates.
   Temporary deployment grants for both runtime names must fit IAM policy size
   limits and be reduced to the new runtime name after the migration is verified.
   Set `ApprovedGenerationTemplateUrl` to the same exact versioned daemon seed
   URL in the boundaries, foundation and bootstrap stacks. A renamed boundary
   deployed with its previous parameter still rejects the new seed; successful
   resource creation alone does not establish that these pins agree.
   Rotate `AllowedImageId` to the verified daemon AMI in those same three
   stacks. Also verify the boundary's backend, launch-template ID, subnet and
   security-group pins against the approved release; retaining a previous AMI
   parameter prevents both seed and direct successor launches.
3. With all provisioning paths still paused, deploy the renamed boundary before
   the foundation role/profile. Deploy the matched bootstrap controller and
   templates with `Daemon*` parameters and `BootstrapTriggerEnabled=false`. Review actual change sets for ordering
   and dependency handling before execution. Do not use historical import or
   recovery templates to deploy this release.
4. Publish the new seed and generation templates. Update the persistent launch
   template with the new baked image, profile, daemon configuration and artifact
   pins; capture the exact numeric version and canonical LaunchTemplateData
   digest. Keep the selected backend unchanged.
5. Prepare a conditional CONTROL update from the captured current item: rename
   the four `agent_artifact_*` attributes to `daemon_artifact_*`, set their new
   immutable artifact pins, and update the approved template/bootstrap/launch
   template pins together. Remove the old CONTROL artifact fields. Condition the
   write on the observed command sequence and old pins, unchanged paused request
   flags, exact BOOTSTRAP request identity and disabled launch gates, plus absent
   propagation/provisioning locks. Preserve every unrelated attribute, cycle
   counter, HOLD item, audit item and CURRENT record. Abort on conflicts and
   re-inspect rather than resetting state. `initialize_control.py` only creates
   missing records; it is not a migration tool and must not overwrite existing state.
6. Inspect the actual installed policies, profile role membership, log group,
   controller configuration, image metadata and state/template pin agreement.
   Compare the three stacks' `ApprovedGenerationTemplateUrl` parameters with
   the seed URL reconstructed from CONTROL before enabling delivery or starting.
   Compare their `AllowedImageId` values with the image in the exact numeric
   launch-template version pinned in CONTROL.
   Set `BootstrapTriggerEnabled=true` through a reviewed bootstrap change set
   only after the complete release agrees. Keep propagation
   disabled until the operator is ready for the bounded trial.

The dry-run-only helper prepares the conditional state transaction and refreshes
BOOTSTRAP's approved-control fingerprint without changing its cycle counter:

```sh
python3 scripts/prepare_daemon_migration.py \
  --table-name cloud-glider-sandbox-state \
  --snapshot /absolute/path/idle-snapshot.json \
  --approved-control /absolute/path/reviewed-daemon-control.json \
  --operator-id OPERATOR_ID \
  --operator-path-paused --inventory-verified-empty \
  > /absolute/path/reviewed-daemon-transaction.json
```

The snapshot has `control`, `current` and `request` keys containing consistent
DynamoDB wire-format items. The approved file contains the complete new CONTROL
item. Only artifact naming and reviewed release pins may change; all unrelated
fields must match. The attestations require actual inspection of paused delivery
and AWS inventory. The helper does not query inventory or apply the transaction.
Review and apply the output through the approved operator path while paused.
Existing HOLD is neither read as permission nor modified by this transaction.

## Validate, retire and recover

Start with a low max_generation. Observe new-generation daemon readiness, fresh
controls, conditional handoff and confirmed predecessor termination. Verify
operations logs reach the daemon log group. Exercise disabled propagation and
hold behavior through the approved tests/observation path; do not manufacture a
failed live handoff merely for testing. Clean up through the new controller.

After confirming no old instance/profile/template/controller still uses agent
resources, review explicit retirement of retained old boundaries and unused roles
or profiles. Preserve historical logs according to the approved retention policy.

If deployment fails before a new cycle starts, keep every provisioning path
paused and restore the matched old controller/templates/image/profile and saved
CONTROL pins with conditional writes. Preserve counters and HOLD. Old resources
may need recovery if CloudFormation removed them. If a new cycle has started,
stop propagation and inspect using that release's supported cleanup; never switch
backend, schema or image underneath a live cycle.

## Names intentionally left alone

Historical decisions, release receipts, recovery/import snapshots, the foundation
completion repair supplement, IAM runtime deployment evidence and recorded
performance measurements keep their agent names. `AGENTS.md` and references to
Amazon SSM Agent remain unchanged. No workflow contains a rename dependency;
workflow permissions and secrets are unchanged. Existing S3 object keys, AMI IDs
and old logs retain their identities as historical evidence.
