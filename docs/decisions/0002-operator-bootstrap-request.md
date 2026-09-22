# Decision 0002: separate operator bootstrap request

Status: accepted for implementation; infrastructure deployment and the first
bootstrap request require separate operational and cost review.

## Decision

Keep `CONTROL/GLOBAL.propagation_enabled` as the permission for EC2 agents to
create successors. Add a separate, operator-created `BOOTSTRAP/REQUEST` item
with a `bootstrap_requested` Boolean that authorizes the first generation, `000000`, through a bootstrap Lambda.
The Lambda accepts either Boolean value of `propagation_enabled` and never
writes it. For the initial sandbox inspection, the operator keeps it false.
A future run can bootstrap with it true and allow the EC2 agent to continue.

This is the approved operator bootstrap path, an explicit exception to the
successor-provisioning enabled gate. It is not a recovery controller and cannot
create successors, delete stacks, change IAM, take CURRENT ownership, or clear
an emergency hold. `HOLD/ACTIVE` blocks both bootstrap and propagation.

Enable NEW_AND_OLD_IMAGES on the foundation state table. A separate reviewed
`cfn/bootstrap.yaml` stack defines the Lambda, its narrow execution role,
retained logs, an error alarm, and an event source mapping. Only a MODIFY transition of
`BOOTSTRAP/REQUEST.bootstrap_requested` from false to true with status READY
invokes the handler; changing
`propagation_enabled`, heartbeats, and Lambda status updates do not trigger it.

## Request and safety gates

`scripts/request_bootstrap.py` is dry-run by default. Applying it atomically
checks the approved control values, UNINITIALIZED CURRENT, and absence of HOLD,
then inserts a READY record with `bootstrap_requested=false` and a preparation
audit event without overwriting either record. An operator later toggles the
Boolean in DynamoDB; applying the script does not launch compute.
The request contains an operator identity, request ID, and a digest of approved control values. The digest excludes only
`propagation_enabled`, `updated_at`, and `updated_by`, so toggling propagation
is independent of requesting bootstrap. Bootstrap parameters other than the
artifact identities come from the reviewed bootstrap stack, matching the
foundation's approved AMI/network inputs.

The handler verifies the artifact bucket, version IDs, and both SHA-256 hashes;
validates the ARM64/EBS AMI; rejects existing tagged instances and an existing
first stack; and conditionally changes READY to CREATING only while the Boolean is true. Immediately before
CreateStack it transactionally rereads CONTROL, CURRENT, HOLD, and the request.
The approved control fingerprint must still match, CURRENT must still be
UNINITIALIZED, the Boolean must still be true, and HOLD must be absent.

The only create target is the deterministic generation-000000 stack, using the
versioned template URL, explicit parameters, the designated CloudFormation
service role, and a deterministic request token. No IAM capabilities are passed.
The EC2 agent retains its existing conditional claim of CURRENT and readiness
checks. SUBMITTED means the create request was accepted, **not** that bootstrap
or workload health succeeded.

## Failure and retry behavior

A durable CREATING claim prevents concurrent deliveries from submitting again.
If the response is lost, retries may only inspect the deterministic stack and
verify its parameters, role, and request tag. If absent, failed, or conflicting,
the Lambda fails closed and requires operator inspection. A crash after claim
but before submission deliberately sacrifices automatic recovery to avoid a
second launch after an uncertain outcome. CREATING is not an expiring lease.

Successful submission changes the request to SUBMITTED. Keep the request as a
permanent one-shot marker: there is no DynamoDB TTL and no automatic rearming
after stack deletion. Setting the Boolean back to false withdraws authorization at the final read.
After claim, inspect before any retry; a false Boolean cannot undo an in-flight
AWS call. Status is never automatically reset or rearmed.
As in the existing propagation path, the final read and CloudFormation call
are not a cross-service transaction. A hold arriving after that read may race
with submission; it does not cancel a submitted stack operation.

Preparation does not expire. Stream events have a 15-minute maximum age.
Stream retries are bounded and discarded-event metadata goes to the existing
operational SNS topic. Lambda failures also drive an error alarm. CloudTrail,
the operator audit item, request status, and structured submission log provide
attribution. Bootstrap CREATE failures retain resources for inspection through
`OnFailure=DO_NOTHING`; cleanup is an explicit operator CloudFormation action.

## Review boundary

This adds stream consumption, Lambda/log usage, and an alarm. Applying a request
can incur EC2, EBS, public IPv4, and monitoring charges. Confirm notifications
and budget controls before the first request. No deployment-role permissions,
GitHub workflows, existing agent artifacts, or live control values are changed
by the implementation. Live IAM enforcement and end-to-end behavior must be
verified after the reviewed deployment.
