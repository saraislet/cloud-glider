# Trial cleanup and AMI retention

Apply [decision 0026](decisions/0026-trial-cleanup-and-ami-retention.md) after
every live propagation and isolated AMI smoke trial. Cleanup remains an explicitly authorized operator workflow. The read-only
`scripts/trial_receipt.py` automates inventory, evidence capture and completion
verification; it never starts trials, requests STOP/CLEANUP, deletes resources,
or prunes AMIs/snapshots.

## Complete each trial

1. Verify the account, Region, task identity, trial/cycle identifiers and other
   operators' active work. Inventory only resources belonging to the trial.
2. Collect timing, health, identity, failure and cost-relevant instance evidence
   before removing resources. For smoke tests, save console output proving
   explicit PASS and absence of FAIL. If evidence is missing, record the trial
   as unvalidated; do not label stack creation or a stopped instance a PASS.
3. For propagation trials, use the supported STOP path, verify propagation is
   disabled, and reconcile in-flight launches before CLEANUP. Preserve instances
   when ownership or cleanup scope is ambiguous. STOP retains instances.
4. Use supported lifecycle CLEANUP for generation resources. For isolated AMI
   smoke tests, delete the exact smoke CloudFormation stack. Do not directly
   delete resources owned by a live stack to shortcut stack cleanup.
5. Verify stack deletion, actual instance termination and disappearance of
   trial-owned volumes and any temporary billable network resources. Verify
   no unresolved launches, generation records or locks; for propagation trials,
   confirm disabled propagation, reconciled CURRENT, lifecycle READY and cleanup
   COMPLETE. Retained control-plane infrastructure is not trial residue.
6. Save an owner-only cleanup receipt listing targets, identity, timestamps,
   deletion results, fresh readback and any explicitly retained exceptions.
   Report remaining billable resources or incomplete evidence. Do not mark
   cleanup complete merely because a deletion API returned successfully.

## Retain only useful images

Keep the currently deployed AMI and its root snapshot. Keep at most one
compatible, deliberately selected rollback image and at most one next-release
candidate, each with its backing snapshot. If no safe rollback or candidate
exists, do not retain an arbitrary old image just to fill that slot.

After each build/release/trial, classify images against live CONTROL pins,
bootstrap/foundation/boundary parameters, the exact numeric launch-template
version, live instances/stacks, active or planned work in other chats, and the
chosen rollback release. Check the rollback's artifact, configuration and schema
compatibility; age alone does not establish a usable rollback.

For superseded images, preserve build/test receipts, record retired historical
launch-template references, deregister the exact owned AMIs, and delete only
their owned snapshots after checking that no retained AMI shares them. Verify
the image registrations and snapshots are gone and retained image/snapshot
pairs remain available. Do not delete the pinned upstream source AMI or treat
it as one of the account-owned retention slots.

When an image is needed beyond this set, record the operator-approved reason,
owner and review point. Do not silently accumulate build candidates or apply
an age-based deletion rule to an active dependency.

## Implemented private evidence commands

Run from an isolated checkout with Python and boto3, AWS CLI credentials from
an existing scoped task profile, and existing read permissions. No extra IAM
permissions are installed. Read denial is an evidence gap, never absence.
Supply an operator-reviewed JSON scope under ignored `.artifacts/`. For example
(values below are placeholders; never check the filled file into Git):

```json
{
  "account": "ACCOUNT_ID",
  "region": "us-west-2",
  "role_arn": "arn:aws:iam::ACCOUNT_ID:role/EXISTING_TASK_ROLE",
  "task": "Anna Sarai / approved trial task",
  "trial": "NUMERIC_CYCLE_ID",
  "kind": "propagation",
  "started_at": "2026-10-07T23:00:00+00:00",
  "ownership_reviewed_at": "2026-10-07T23:00:00+00:00",
  "no_other_operator_work": true,
  "stacks": [],
  "tags": {"project": "cloud-glider", "environment": "sandbox",
           "bootstrap-request-id": "NUMERIC_CYCLE_ID"},
  "table": "cloud-glider-sandbox-state",
  "generation_table": "cloud-glider-sandbox-generations",
  "launch_template_id": "lt-EXACT_ID",
  "launch_template_version": "NUMERIC_VERSION",
  "source_commit": "EXACT_COMMIT",
  "daemon_sha256": "EXACT_DIGEST",
  "retained_control_plane": ["shared foundation, tables, Lambda and networking"],
  "retained_exceptions": [],
  "coverage": "Exact cycle tags, both tables, seed stack and attached resources"
}
```

Review the account, role, Region, task, exact cycle, resource ownership and other
operators' active/planned work yourself. The tool verifies STS account and task
role before resource reads; `task` and `no_other_operator_work` are operator
attestations, not automatic chat discovery. Refresh `ownership_reviewed_at`
within 15 minutes of each invocation. Future dates are rejected. A snapshot
cannot prevent another operator from starting work afterward; coordinate first.

For an isolated smoke test, set `kind` to `smoke`, supply `approved_image_id` and the baked `daemon_sha256`,
and omit propagation tables/template pins. Use a unique `trial` tag on the
approved smoke stack when creating it, or inventory its exact stack ARN after
creation. Shared subnet/security-group parameters belong in retained control
plane; the tool does not infer that a shared input is trial-owned. Once the
stack exists, record its full ARN, not its reusable name, in `stacks`.

```sh
python3 scripts/trial_receipt.py preflight --scope .artifacts/trial/scope.json --profile EXISTING_TASK_PROFILE --output .artifacts/trial/preflight-001.json
```

Propagation preflight requires disabled propagation, READY lifecycle, the exact
next cycle, UNINITIALIZED CURRENT, no generation records/locks/HOLD, and no pending
commands. `VERIFIED` preflight is a read-only observation, not permission to run.
Run the trial only under its separate authorization. After creation, fill in
exact stack ARNs. Capture while resources and evidence are still available,
then capture again immediately before cleanup; the latter is the `--before`
input to verification. Add `--before` to subsequent captures to carry forward
all observed exact resource IDs, including untagged attached volumes/ENIs/EIPs.
Do not change scope between incremental captures except the review timestamp;
preserve separate captures if the approved scope changes.

```sh
python3 scripts/trial_receipt.py capture --scope .artifacts/trial/scope.json --profile EXISTING_TASK_PROFILE --output .artifacts/trial/before-001.json
```

Capture collects console output, EC2 identity/status, stack resource lists,
consistent paginated reads of both state tables, cycle-derived status alarms,
explicit optional scope `alarms`, and five-minute CPU utilization,
credit usage/balance, surplus balance and charged-credit metrics since
`started_at`. Missing metric samples remain unknown, never zero. This tool does
not estimate dollar charges. Choose a bounded trial window; long CloudWatch
requests may exceed service limits and will be recorded as gaps. Cycle-tagged
volumes, ENIs, EIPs, snapshots, NAT gateways and VPC endpoints are also inventoried.
Existing snapshots are inspected only, never pruned.

For propagation, pass `--evidence .artifacts/trial/evidence.json`. The bundle
contains `timing_records`, `expected` identities in the existing
`verify_timing_collection.py` format, and `observations` with `health`, `identity`
and `failure` arrays. Each array must cover every inventoried instance; each
observation supplies `instance_id`, `request_id`, `observed_at`, `source`, and
`reviewed_by` equal to scope `task`, and nonempty `details`. Observation timestamps
must fall within the trial window. Include actual raw observations in the bundle,
including failed/ambiguous gates and explicit no-failure observations where
supported. `source` identifies their provenance. Operator observations are
preserved review evidence; their presence does not prove workload health.
Expected timing identities must match inventoried instances, exact cycle and
artifact pins. The existing validator checks producer digests, sequence coverage,
completion markers, boot phases, dropped records and upload errors. Optionally
add `--timing-log-group EXACT_GROUP` to retrieve all expected boot streams before
validation. Failed retrieval and invalid/missing timing remain explicit gaps.

Use the supported STOP and CLEANUP commands in the bootstrap/family runbooks
under existing authorization. For smoke tests, delete only the exact approved
CloudFormation smoke stack through the existing operator path. No deletion is
performed by this tool. Refresh the ownership-review timestamp, then:

```sh
python3 scripts/trial_receipt.py verify --scope .artifacts/trial/scope.json --profile EXISTING_TASK_PROFILE --before .artifacts/trial/before-001.json --output .artifacts/trial/completion-001.json
```

The receipt contains identity, scope, capture timestamps, fresh readback,
pre-cleanup inventory digest, gaps, declared retained infrastructure and named
exceptions. All output files are created exclusively with mode 0600 inside
checkout `.artifacts/`, with enclosing directories mode 0700. Retries use new
filenames; existing observations are never overwritten. Partial reads are saved
with errors. Wrong-account/role, malformed scope or expired ownership review
fails before resource reads. Keep captures and receipts local and ignored.

Verification returns exit code 2 and `INCOMPLETE` for missing evidence, scope
changes, inventories over 24 hours old, denied reads, unsupported stack resource
types, missing exact IDs, named retained exceptions or unfinished cleanup. It
requires exact stack `DELETE_COMPLETE` without retained resources, absent
inventoried alarms, observed instance termination, exact
volume/ENI/EIP removal, and no tagged storage/network residue. An expired stack
record or EC2 termination record is a gap; absence alone cannot substitute for
actual termination. Propagation additionally requires COMPLETE cleanup for the
exact cycle, READY next cycle, disabled control/lifecycle propagation, reconciled
CURRENT, no generation records/locks, and no pending launch commands. Both tables are read again at the end; changes during verification remain
an explicit gap. Timing is
revalidated from the stored bundle at verification time.

`VERIFIED` means cleanup within the declared captured scope. Propagation trial
outcome remains `REVIEW_REQUIRED`; lifecycle cleanup and timing coverage do not
prove a successful healthy handoff. Smoke PASS additionally requires the approved
AMI identity and structured console `CLOUD_GLIDER_AMI_SMOKE_PASS` matching that daemon digest,
with no FAIL.
No resources are exempted merely by calling them retained: exceptions keep full
cleanup incomplete. Unsupported billable resource classes, missing historical
instances, untagged unattached resources, external operators and deletion-history
expiry require separate review. The tool is deliberately conservative and cannot
claim account-wide absence of residue. Keep retained control-plane infrastructure
separate from trial-owned resources in both inventory and human review.
