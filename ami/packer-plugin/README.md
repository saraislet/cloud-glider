# Local Packer Amazon registration fix

Upstream: hashicorp/packer-plugin-amazon v1.8.2, commit
`3896533621ce21e5d8277b7e86e9bf02b577045a` (MPL-2.0).
`register-image-tags.patch` modifies `builder/ebssurrogate/step_register_ami.go`.
The accompanying Go test is applied to the upstream package during setup.

The stock surrogate snapshot registration path does not set TagSpecifications.
The patch renders the configured final AMI tags with the plugin's normal tag
interpolation helper and sends image-only tags in RegisterImage. Tag rendering
errors stop before registration. Snapshot tags remain handled by the existing
snapshot step. Later tag reconciliation remains unchanged. No IAM relaxation or
post-registration workaround is required. Tags depending on a newly created AMI
ID cannot be used at registration time; the Cloud Glider recipe uses static/HCL
resolved values and does not enable intermediary AMI copies.

Run from the repository with Python 3.9+, Go 1.25.11 or compatible, Packer,
and the patch utility available:

```sh
python3 scripts/install_packer_amazon.py
packer init ami/ubuntu-minimal.pkr.hcl
packer validate -var-file=.artifacts/ami/build.pkrvars.json ami/ubuntu-minimal.pkr.hcl
```

`--go /path/to/go` selects an alternate toolchain. Save the printed provenance
with release evidence. The source archive is pinned by commit and SHA-256;
Go verifies dependencies against the upstream go.sum. The logical plugin address
`github.com/cloud-glider/amazon` is local only, not a published fork. Do not use
`packer init -upgrade` to obtain it from a registry. The official HashiCorp
installation remains available to other projects. To update this patch, review
the source/test changes and deliberately reinstall; do not silently substitute
an upstream version without tag-at-registration coverage.

Validation requires the captured RegisterImageInput to contain project,
purpose, approval, and an interpolated tag, and proves a rendering error makes
no registration call. Tests use a fake EC2 client and no AWS credentials.
A paid build and CloudTrail inspection are still needed to confirm live IAM
acceptance. Require all final image tags and the existing smoke/release gates.

## SSM cleanup context

`ssm-cleanup-context.patch` gives TerminateSession a fresh 30-second deadline,
independent of build cancellation while preserving context values. The regression
test cancels the build context, verifies the SDK sends the correct session ID,
and checks the request has a live, bounded deadline. The installer runs both
SSM and surrogate tests before replacing the local plugin. This fixes canceled
context cleanup; IAM denials and network failures still surface as errors.
