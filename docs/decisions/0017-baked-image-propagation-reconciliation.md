# Decision 0017: reconcile baked image propagation onto current main

Status: implemented in cloud-glider; current-contract image release outstanding.

Add explicit baked delivery mode to the current generation and bootstrap
parameter contract. Baked startup verifies the installed archive identity and
file hashes and uses the image's SDK environment/service. Preserve request IDs,
separate generation state, current operator switches and cleanup fencing.
Root sizes below 8 GiB require baked mode. Successors and continuation preflight
inherit the same image/runtime parameters.

The earlier deployment was made from the obsolete glider repository. Preserve
its release receipt as legacy history, not as a deployable current-main release.
Its image's archive predates current lifecycle fields. A matching rebuilt image,
smoke validation and coordinated lifecycle migration are required before using
this branch in AWS. Do not silently downgrade the current contract to match it.
See [the reconciliation runbook](../minimal-ami.md).
