# Decision 0013: single-item operator switches and visible results

Status: accepted for implementation by the operator on October 2, 2026;
deployment remains a separately reviewed action.

## Operator interface

Use CONTROL/GLOBAL as the only routine operator-facing item. Three Boolean
request switches replace manual combinations of bootstrap/propagation permissions:

- `start_requested`: bootstrap and enable propagation, or enable an existing chain.
- `stop_requested`: disable propagation without deleting running instances.
- `cleanup_requested`: close launch gates, clean up, and prepare the next cycle.

The operator sets one switch true, saves, and refreshes `operation_status` and
`last_result` in that same item. Lambda processing is asynchronous; there is no
console popup. Status/schema prerequisites are controller checks, not operator
steps. More than one switch true is rejected with an explanatory message and
cleared. A hold blocks start. Start during cleanup is rejected, never queued.
Repeated cleanup continues the existing operation and preserves inventory.
A cleanup with no generation resources completes successfully.

The initial start can prepare BOOTSTRAP/REQUEST automatically from initialized
CONTROL/CURRENT. `cycle_initialized` prevents silently reconstructing a missing
previous lifecycle record. The preparation script remains an optional alternative.
Separate low-level bootstrap/enable/disable/resume helpers remain for recovery.
Normal CLI start/stop/cleanup commands request the same CONTROL actions.

## Internal state and concurrency

Decision 0012's BOOTSTRAP/REQUEST remains the controller-managed cycle record,
including incremental request ID, actual launch permissions, and latest cleanup.
This decision supersedes its operator-facing switch placement, not its cleanup,
health, ownership, or reset safeguards. CONTROL request Booleans are commands;
internal lifecycle Booleans are permission gates, not additional operator steps.

A conditional transaction consumes a command, increments `command_sequence`,
resets request Booleans, changes internal lifecycle permission, and records a
message. START checks HOLD atomically. Only accepted commands establish an
`active_command` and target cycle. DynamoDB delivers commands. Active cleanup
uses cycle-scoped one-time retries; there is no idle or bootstrap polling timer.
See decision 0016.

Duplicate/stale stream events cannot consume later commands. Exact conditional
CONTROL writes protect newer console edits; contention leaves a command pending
for a bounded delivery retry or an operator retry. Feedback is conditional on active action and target
cycle. Progress does not overwrite the message from a newer rejection/stop.
Pending start requests entered while cleanup finishes are rejected before the
cleanup busy gate is cleared. START failures pause internal launch permissions
and report NEEDS_ATTENTION while preserving the resources/submission marker.

Pending CONTROL stop/cleanup requests close successor provisioning gates before
Lambda acknowledgement; pending cleanup also blocks bootstrap. The submission
marker and final fresh control checks still handle calls already in flight.

Operator request/status fields are excluded from the approved-control fingerprint.
Changing them cannot invalidate preparation or masquerade as an artifact change.
Approved artifacts, limits, environment, and health checks remain validated.
Controller source restricts CONTROL writes to interface fields; it cannot edit
approved configuration through its command-update helper. Agent permissions stay
read/condition-check only for CONTROL and BOOTSTRAP.

## Rollout, cost, and history

The bootstrap template adds CONTROL UpdateItem permission restricted to this
Cloud Glider table/partition and three stream filters. It retains the existing
function, reserved concurrency one, notification path, and narrow generation
stack permissions. No new deployment-role grants, IAM administration, workflows,
secrets, or direct EC2 mutation.

Initializer and offline migration populate the interface. Existing deployments
need the reviewed coordinated release; do not hot-migrate a live chain or replace
its internal cycle record. Review the bounded reads, Lambda invocations, and
one-time schedules used only during active cleanup before deployment. Existing CloudTrail/operational audit retention remains unchanged;
no separate cleanup history is introduced.

## Verification

Tests cover automatic first preparation, start with propagation, existing-chain
start, stop, empty cleanup, conflicting/malformed switches, holds, stale delivery,
rejection during cleanup, message preservation, startup failure, missing prior
lifecycle state, and command fields excluded from fingerprints. Cleanup, health,
identity, submission ambiguity, and failure-path checks from decision 0012 remain.
Live IAM, stream/timer delivery, and actual AWS cleanup must be tested after
reviewed deployment; local mocks do not establish those properties.

Migration to cloud-glider retains the persistent boto3 transport, independent permission boundaries, sanitized settings, overlapping handoff and joined health/ownership/capacity gates. Boundary ceiling updates remain independently administered. Billing and notification verification stay deferred to V2.
