# Decision 0024: separate propagation and release audit records

Status: operator requested implementation and local AWS preparation; not deployed.

Move AUDIT#PROPAGATION and AUDIT#RELEASE into
`cloud-glider-{environment}-audit`, retaining every existing PK, SK, and item
attribute. This supersedes decision 0014's placement of latest propagation audit
results in the control table. CONTROL, CURRENT, BOOTSTRAP, HOLD, locks, and
AUDIT#RECOVERY remain in the state table. Generation records remain in the
generation table. Existing LATEST_* writes still reuse their keys; this change
does not turn those records into a complete event history.

The foundation owns the retained, deletion-protected, on-demand audit table,
with AWS-owned encryption and 35-day PITR. No indexes, stream, or TTL are added.
Existing expires_at attributes are copied as data, but do not expire in the new
table. Any automatic history expiration requires a separate retention decision.
Additional storage and PITR costs require operator review before rollout.

Both daemon releases derive the audit name from their environment. Approved
CONTROL must explicitly contain that same audit_table_name; missing or mismatched
configuration fails closed. Existing configuration JSON and launch-template
parameter schemas remain unchanged. Both backends keep the audit Put inside the
existing conditional ownership transaction. The terminal-error Lambda likewise
keeps hold creation, generation error state, and its audit write atomic across
all involved tables. Missing audit permissions prevent the entire transaction.

Daemon and hold identity policies and independent boundaries permit only PutItem
under AUDIT#PROPAGATION in the audit table. They lose propagation audit mutation
permissions in the state table. Neither receives release writes or audit deletion.
The CloudFormation daemon boundary combines identical unconditional resource
grants to stay within the 6,144-character expanded managed-policy ceiling;
resource, action, and condition coverage are unchanged by that consolidation.
Deployment grants and organization policies are not modified by this change.
Their independently administered coverage needs review before AWS rollout.

Use an offline rollout with all audit and provisioning writers paused, verified
empty AWS generation inventory, and lifecycle gates disabled. Prepare bounded,
conditional copy requests from complete snapshots. Exact destination comparison
must succeed before preparing control cutover or source removal. Preserve cycle
counters and refresh the approved-control fingerprint atomically. Publish a
coordinated source release and repin its artifact/template/AMI inputs separately
before resuming. Never switch audit destinations underneath a running chain.

AUDIT#RELEASE writers were identified in ignored local overlap and daemon rollout
scripts. Additional local benchmark, AMI, and family release scripts write
AUDIT#PROPAGATION. These producers and audit readers were updated locally to the
new table; verify their operator permissions and regenerate requests before
resumption. See the runbook inventory. A new
local-only release request preparer targets the audit table; it does not grant
permissions or submit requests. The observer derives its lifecycle display from
state and generation records and needs no audit subscription.

See [the rollout runbook](../audit-table-migration.md) for preparation, verification,
rollback, and the evidence required before any AWS action.
