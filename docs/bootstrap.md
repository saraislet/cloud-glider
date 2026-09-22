# Operator bootstrap runbook

The bootstrap request and propagation permission are independent:

| Record | Meaning |
| --- | --- |
| `CONTROL/GLOBAL.propagation_enabled` | Allows the EC2 agent to create successors. |
| `BOOTSTRAP/REQUEST` | One-shot operator authorization to create generation 000000. |
| `HOLD/ACTIVE` | Stops new bootstrap and successor provisioning at their fresh checks. |

For the first trial, leave propagation disabled and inspect the first instance.
The Lambda also supports bootstrap while propagation is enabled; in that case
the first instance can immediately begin the bounded propagation cycle.

## Deploy the trigger before requesting bootstrap

1. Confirm billing and operational notification delivery, cost limits, immutable
   artifact approvals, and initialized CONTROL/CURRENT records. No request should
   exist yet. Review the added runtime role, stream reads, alarm, Lambda/log costs,
   and eventual generation compute costs.
2. Review an UPDATE change set for `cfn/foundation.yaml` using the existing
   approved administrative path. The bootstrap-related change enables the state
   table stream and adds `StateTableStreamArn` to outputs. Require no table
   replacement and preserve existing approved AMI/network parameters. Execute
   only the reviewed update and wait for UPDATE_COMPLETE.
3. Read `StateTableStreamArn` and the foundation's current `AllowedImageId`,
   `AllowedSubnetId`, `AllowedSecurityGroupId`, and `Owner` values. Validate the
   AMI with `scripts/validate_generation_inputs.py` and obtain its RootDeviceName.
4. Review a CREATE change set for `cfn/bootstrap.yaml`, stack name
   `cloud-glider-sandbox-bootstrap`, in `us-west-2`. Supply `StateStreamArn` and
   those exact foundation inputs, plus the validated RootDeviceName. This creates
   a new runtime role and needs CAPABILITY_IAM. Use the approved administrative
   deployment principal; do not broaden deployment-role permissions to bypass
   authorization failures. The existing generation service role is reused.
5. After approved deployment, verify the event source mapping is Enabled, points
   at the correct stream, and has the request-only filter and operational SNS
   failure destination. Review the function configuration, logs, and error alarm.

The Lambda source is `bootstrap/handler.py`. After editing it, run
`python3 scripts/render_bootstrap_template.py`; the generated inline code in
`cfn/bootstrap.yaml` is tested for exact agreement with the source. Deployment
does not require a new EC2 agent tarball or generation template release.

## Request the first generation

Preview the transaction (AWS reads only):

```sh
python3 scripts/request_bootstrap.py --region us-west-2
```

Inspect the pinned control digest and audit identity. Confirm the operator wants
to start compute, and inspect `propagation_enabled` separately: the request does
not set, disable, or require a particular Boolean value for that field.
For the initial inspection trial, it should remain false.

Once the compute cost and readiness prerequisites have been reviewed, submit:

```sh
python3 scripts/request_bootstrap.py --region us-west-2 --apply
```

The write requires CURRENT to be UNINITIALIZED, no HOLD, unchanged approved
control, and no previous request. Re-running cannot overwrite/rearm a request.
The Lambda must claim the request within 15 minutes; it has no TTL deletion.
There is no boolean to toggle repeatedly. An existing request requires inspection.

Inspect request status:

```sh
aws dynamodb get-item --region us-west-2 \
  --table-name cloud-glider-sandbox-state --consistent-read \
  --key '{"PK":{"S":"BOOTSTRAP"},"SK":{"S":"REQUEST"}}'
aws cloudformation describe-stacks --region us-west-2 \
  --stack-name cloud-glider-sandbox-gen-000000
```

Expect REQUESTED -> CREATING -> SUBMITTED. SUBMITTED records a stack ID and only
means CloudFormation accepted creation. Wait for CREATE_COMPLETE, then verify
CURRENT belongs to the expected instance and stack, the approved artifact
identities match, and distinct healthy heartbeats are arriving. EC2 running and
Lambda success are not readiness evidence. With propagation disabled, verify
there is no generation 000001 before considering the separately audited enable
operation. The bootstrap Lambda never claims CURRENT or marks the workload healthy.

## Stop, failures, and inspection

A normal propagation stop does not revoke a bootstrap request. Before Lambda
claims it, cancel with a conditional operator update and retain the item:

```sh
aws dynamodb update-item --region us-west-2 \
  --table-name cloud-glider-sandbox-state \
  --key '{"PK":{"S":"BOOTSTRAP"},"SK":{"S":"REQUEST"}}' \
  --update-expression 'SET #s = :cancelled' \
  --condition-expression '#s = :requested' \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":cancelled":{"S":"CANCELLED"},":requested":{"S":"REQUESTED"}}'
```

CloudTrail records this operator action. If the condition fails, inspect before
retrying; it may already be CREATING or SUBMITTED. For an incident use the approved
emergency-hold path as well as disabling propagation. Neither control reverses
an already-submitted CloudFormation create request.

Inspect `/aws/lambda/cloud-glider-sandbox-bootstrap`, the request, CURRENT,
generation records, live instances, and stack events on any error. Stream retries
are bounded to two with a 15-minute maximum record age; discarded-event metadata
is sent to the operational topic. Do not infer success from a lack of retries.

A CREATING request with no stack is ambiguous, including a crash before submission.
The handler never submits again from this state. Failed or conflicting stacks
are also left for inspection. A matching CREATE_IN_PROGRESS/CREATE_COMPLETE stack
can be reconciled on delivery retry without another CreateStack call. A failed
create uses DO_NOTHING so surviving resources are preserved; cleanup goes through
CloudFormation after verifying no request is still in flight.

Do not delete the request, reset CURRENT, or blindly reset CREATING to REQUESTED.
There is no automatic retry/rebootstrap procedure for the first pass. A recovery
requires a reviewed inventory and explicit operator decision. Retain SUBMITTED
and CANCELLED records even after cleanup to prevent replay and unintended restart.

## Validation before enabling propagation

Run repository tests, safety validation, renderer --check, and cfn-lint. After
reviewed deployment, validate the runtime IAM restrictions, stream delivery and
failure notification path, and a disabled-propagation bootstrap. Verify hold,
duplicate delivery, failed bootstrap, artifact mismatch, and ownership conflict
preserve existing resources. Local mocks do not prove IAM or live delivery.
