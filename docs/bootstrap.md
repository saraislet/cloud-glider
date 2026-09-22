# Operator bootstrap runbook

The bootstrap request and propagation permission are independent:

| Record | Meaning |
| --- | --- |
| `CONTROL/GLOBAL.propagation_enabled` | Allows the EC2 agent to create successors. |
| `BOOTSTRAP/REQUEST.bootstrap_requested` | Boolean operator authorization to create generation 000000. |
| `HOLD/ACTIVE` | Stops new bootstrap and successor provisioning at their fresh checks. |

For the first trial, leave propagation disabled and inspect the first instance.
The Lambda also supports bootstrap while propagation is enabled; in that case
the first instance can immediately begin the bounded propagation cycle.

## Deploy the trigger before requesting bootstrap

1. Confirm billing and operational notification delivery, cost limits, immutable
   artifact approvals, and initialized CONTROL/CURRENT records. A prepared READY
   record may exist, but its `bootstrap_requested` Boolean must remain false. Review the added runtime role, stream reads, alarm, Lambda/log costs,
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
   a new runtime role and needs CAPABILITY_IAM. GliderManager can submit it using
   `--role-arn arn:aws:iam::123456789012:role/cloud-glider-sandbox-foundation-cfn`
   after the [service-role supplement](../iam/glider-manager-policy-review.md)
   is reviewed and applied; do not broaden deployment-role permissions to bypass
   authorization failures. The existing generation service role is reused.
5. After approved deployment, verify the event source mapping is Enabled, points
   at the correct stream, and has the request-only filter and operational SNS
   failure destination. Review the function configuration, logs, and error alarm.

The Lambda source is `bootstrap/handler.py`. After editing it, run
`python3 scripts/render_bootstrap_template.py`; the generated inline code in
`cfn/bootstrap.yaml` is tested for exact agreement with the source. Deployment
does not require a new EC2 agent tarball or generation template release.

## Prepare the Boolean, then request the first generation

Prepare `BOOTSTRAP/REQUEST` with `bootstrap_requested=false` and status READY.
This does not launch compute, even if the Lambda is already deployed. Preview:

```sh
python3 scripts/request_bootstrap.py --region us-west-2
```

Apply the preparation after inspecting its approved control digest and identity:

```sh
python3 scripts/request_bootstrap.py --region us-west-2 --apply
```

An unchanged READY record with `bootstrap_requested=false` reports
`ALREADY_PREPARED` on repeat runs, without writes. This confirms preparation only,
not deployment readiness. Active, completed, legacy, or stale records require
inspection. The script never overwrites a request or changes propagation.
The prepared record has no TTL or preparation expiry. If approved artifact/control
values change, the fingerprint check fails closed; inspect before preparing a
replacement. Existing legacy REQUESTED/CREATING/SUBMITTED records must not be
blindly replaced or reset. Deploy the Boolean-aware Lambda/filter before toggling.

When ready to start compute, edit the **Boolean** `bootstrap_requested` from
`false` to `true` on `PK=BOOTSTRAP, SK=REQUEST` in DynamoDB. Leave the other
attributes unchanged. This is the only switch needed; no script is required
for the toggle. The equivalent conditional CLI command is:

```sh
aws dynamodb update-item --region us-west-2 \
  --table-name cloud-glider-sandbox-state \
  --key '{"PK":{"S":"BOOTSTRAP"},"SK":{"S":"REQUEST"}}' \
  --update-expression 'SET bootstrap_requested = :yes' \
  --condition-expression '#s = :ready AND bootstrap_requested = :no' \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":ready":{"S":"READY"},":yes":{"BOOL":true},":no":{"BOOL":false}}'
```

Confirm compute costs and notification prerequisites before toggling. Keep
`CONTROL/GLOBAL.propagation_enabled=false` for the initial inspection trial;
bootstrap also supports it being true. CloudTrail records the operator toggle.
Only MODIFY events whose old Boolean is false and new Boolean is true with READY
status pass the filter. Insertion, repeated true writes, and status changes do
not trigger bootstrap. The stream event has a 15-minute maximum delivery age.

Inspect request status:

```sh
aws dynamodb get-item --region us-west-2 \
  --table-name cloud-glider-sandbox-state --consistent-read \
  --key '{"PK":{"S":"BOOTSTRAP"},"SK":{"S":"REQUEST"}}'
aws cloudformation describe-stacks --region us-west-2 \
  --stack-name cloud-glider-sandbox-gen-000000
```

Expect READY -> CREATING -> SUBMITTED after the Boolean becomes true. SUBMITTED records a stack ID and only
means CloudFormation accepted creation. Wait for CREATE_COMPLETE, then verify
CURRENT belongs to the expected instance and stack, the approved artifact
identities match, and distinct healthy heartbeats are arriving. EC2 running and
Lambda success are not readiness evidence. With propagation disabled, verify
there is no generation 000001 before considering the separately audited enable
operation. The bootstrap Lambda never claims CURRENT or marks the workload healthy.

## Stop, failures, and inspection

A normal propagation stop does not revoke bootstrap. Set `bootstrap_requested`
back to false to withdraw authorization before the final provisioning read.
The Lambda checks it both when claiming the request and immediately before
CreateStack. If status remains READY, a later false-to-true transition can retry.
If status is CREATING or SUBMITTED, inspect instead of resetting status: a create
may already be in flight. The Boolean remains true after submission as a record
of authorization; status prevents later toggles from launching a second chain.
For an incident use the approved emergency-hold path as well as disabling
propagation. Neither switch nor HOLD reverses a submitted CloudFormation request.

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

Do not delete the request, reset CURRENT, or blindly reset CREATING to READY.
There is no automatic retry/rebootstrap procedure for the first pass. A recovery
requires a reviewed inventory and explicit operator decision. Retain SUBMITTED
and any legacy CANCELLED records even after cleanup to prevent replay and unintended restart.

## Validation before enabling propagation

Run repository tests, safety validation, renderer --check, and cfn-lint. After
reviewed deployment, validate the runtime IAM restrictions, stream delivery and
failure notification path, and a disabled-propagation bootstrap. Verify hold,
duplicate delivery, failed bootstrap, artifact mismatch, and ownership conflict
preserve existing resources. Local mocks do not prove IAM or live delivery.
