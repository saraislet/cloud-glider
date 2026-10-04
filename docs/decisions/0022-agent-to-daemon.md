# 0022: Rename the Glider runtime from agent to daemon

The operator approved a full terminology rename, including the IAM role,
instance profile, managed permission-boundary policy and operations log group.
The runtime is a systemd daemon; propagation behavior remains unchanged.

Both CloudFormation and EC2 implementations use daemon module/class names.
Configuration, CONTROL artifact fields, readiness `daemon_live`, image manifests,
AMI tags, CloudFormation parameters/logical IDs/outputs and operator CLI flags
use daemon terminology. The executable `cloud-glider`, systemd unit
`cloud-glider.service`, Python package `cloud_glider`, table names and propagation
backend remain unchanged. Exact configuration parsing and readiness validation
continue to reject mismatched releases; no fallback accepts legacy proof fields.

This is an offline release migration. Do not update a live cycle or mix old images,
new user data, old state pins and new controllers. Build new immutable artifacts,
a new baked AMI and a reviewed numeric launch-template version/digest. Preserve
cycle counters, ownership fencing, emergency hold and audit history. Follow
[the migration runbook](../daemon-migration.md) before deploying.

Historical ADRs, release receipts, performance evidence and recovery/import
snapshots retain original names and identities. They describe previous releases;
they are not deployment inputs for the renamed release. `AGENTS.md` names coding
agent instructions, and `amazon-ssm-agent` is an AWS package/service. Neither is
renamed. Existing retained agent resources may survive replacement and require
separate verified retirement after the new release is proven.
