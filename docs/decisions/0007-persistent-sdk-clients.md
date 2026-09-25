# Decision 0007: persistent boto3 clients

Status: implemented; compatible AMI and reviewed release required before deployment.

## Decision

Replace the propagation gateway's per-request AWS CLI subprocess with native,
typed boto3 calls. Create one low-level client per service per agent process
before any workers are started; reuse its HTTP connection pool. Do not create
clients per request or share a Session across future worker initialization.
The entry point closes clients on orderly exit. No credentials are embedded or
frozen; the normal SDK provider chain handles instance-role credential refresh.

Preserve strongly consistent DynamoDB reads, conditional writes, transactional
handoff, versioned S3 object verification, CloudFormation template URLs, roles,
parameters, and deterministic request tokens. Handle conditional failures using
structured AWS error codes, not substring matches against error messages.
Network/credential failures remain transient and are never interpreted as
missing stacks or successful mutation. Read all EC2 capacity pages, retaining
the CLI's automatic pagination behavior. Stream and close S3 bodies; drain and
close Lambda responses, preserving FunctionError handling.

Use 2-second connection and 5-second read timeouts, ten pooled connections,
and standard retry mode with total_max_attempts=1. Automatic mutation replay
could bypass a fresh control gate or obscure uncertain outcomes; retries return
to the existing state-machine reconciliation path. Reads also use one attempt
for a simple bounded policy. These socket timeouts are not a hard end-to-end
lifecycle deadline. No promise of faster AWS service execution is made.

## Dependency and release contract

The deterministic artifact contains agent/requirements.txt as requirements.txt.
It does not vendor SDK packages or install them on boot. Install the pinned
manifest during the separately managed AMI build, into the service interpreter.
Python 3.9 remains supported with its compatible urllib3 pin; Python 3.10+ uses
the modern urllib3 pin. Review dependency updates as release changes. AWS CLI
remains necessary for existing bootstrap downloads and operator commands.

The AMI work owns image/service changes and must consume this manifest and
approved agent artifact. No IAM, CloudFormation, image IDs, or boot scripts
change here. Release only after imports and instance-profile credential/IMDSv2
behavior are verified on ARM64 and the immutable artifact identity is approved.
No live test or deployment is authorized by this implementation commit.

## Verification and limits

Use botocore Stubber and service-model validation to exercise native request
shapes, connection-client reuse, conditional conflicts, transaction conditions,
control hold/incomplete reads, error mapping, pagination, stream cleanup and
artifact integrity without AWS access. Run the existing lifecycle failure tests
and deterministic artifact tests. Without installed dependencies, SDK tests are
explicitly skipped; a release must run them with the pinned manifest installed.

This replaces transport only. It does not implement decisions 0005/0006's
concurrency, durable retirement recovery, or fresh health after preflight.
Existing lifecycle limitations remain; benchmark boot, request counts and
handoff p50/p95 with the compatible AMI before quantifying performance gains.

## References

- [Boto3 configuration](https://docs.aws.amazon.com/boto3/latest/guide/configuration.html)
- [SDK retry configuration](https://docs.aws.amazon.com/boto3/latest/guide/retries.html)
- [DynamoDB Python connection pooling](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/programming-with-python.html)
