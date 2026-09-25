# Public repository preparation

This is a sanitized source copy. The private deployment repository remains the
source of original recovery evidence. No AWS changes or GitHub publication were
performed while preparing this copy.

## History and privacy scope

All selected historical commits replace the deployment account ID with the AWS
example account `123456789012` and machine-specific checkout paths with
`/path/to/glider`. Branch relationships, empty commits, author identities, dates,
and merges are preserved; affected commit hashes change and original signatures
cannot be preserved. The main branch starts at the latest fetched upstream main;
other local branches retain their separate development history. A preparation
commit adds the portable configuration on top of rewritten history.

Names, emails, resource IDs, Region, network configuration, IAM design, and
operational narratives are intentionally retained. This is not a claim of full
anonymization. No GitHub discussions, Actions logs, or artifacts were copied.

## Deployment account configuration

CloudFormation templates already use AWS::AccountId for dynamic resource ARNs.
Account-specific standalone IAM JSON uses the example account. Set AWS_ACCOUNT_ID
in your local environment to the independently chosen deployment account. Do not
commit its value. An ignored .env or local configuration can hold it; scripts do
not automatically source .env files. Credentials continue to come from your
normal short-lived AWS authentication, never from this configuration.

Render a policy before reviewing or applying it:

```sh
python3 scripts/render_account_policy.py foundation-bootstrap-supplement.json
```

Rendered policies go to ignored `.artifacts/iam/`. The renderer performs no AWS
operations. Compare the authenticated account with the independently configured
expected account before deployment. Do not replace exact resource permissions
with wildcard accounts. Commands and ARNs containing the example account are
illustrations, not ready-to-run deployment inputs.

For GitHub Actions, configure the sandbox environment secret AWS_ACCOUNT_ID.
The workflow rejects missing/example IDs, restricts authentication to that
account, masks it, and checks the caller identity. Also configure sandbox environment secrets AWS_ROLE_ARN and
CFN_SERVICE_ROLE_ARN, since both ARNs contain the account ID. Other deployment
values remain environment variables. Review all new workflow logs before
publication; masking is not a guarantee against every form of disclosure.

## Historical recovery artifacts

The import/repair snapshots, manifests, checksums, stack identifiers, and result
records under cfn/ describe historical operations. Redaction changes their
contents and invalidates original checksum relationships. They must not be
replayed, even after substituting an account ID. The import verifier rejects the
example-account snapshot before contacting AWS. Prepare fresh private recovery
artifacts from current state, or use the untouched private repository's evidence
for investigation. Do not recompute old hashes merely to bypass these checks.

## Publishing later

No remote is configured. Review branches before choosing which to publish.
Scan the complete selected history again before adding a remote. Keep local
configuration, rendered policies, Git metadata, and private recovery evidence
out of uploaded artifacts. The filter-repo commit map in local Git metadata is
for local audit only and is not part of published Git history.
