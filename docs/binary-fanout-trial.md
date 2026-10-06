# Binary fan-out initial test

This historical model is separate from the actual inherited FamilyAgent now
implemented in source. See [the family runbook](independent-ec2-families.md) for
the live-agent source contract and outstanding deployment work. Its leaves are
retained for inspection, unlike this model's simulated automatic leaf retirement.

The operator-approved experiment is two children per interior node, three
zero-based generations (0–2), and seven nodes total. See
[decision 0022](decisions/0022-bounded-binary-fanout-trial.md).

Run the offline trial from the repository root:

```sh
python3 scripts/binary_fanout_trial.py
python3 -m unittest discover -s tests -p 'test_binary_fanout.py' -v
```

The script has no AWS SDK imports, credentials, deployment switches, or adjustable
generation limit. It prints a JSON receipt including node identity, simulated
lifecycle events, occupied slots, completion, and remaining occupancy. It models
functional readiness using a separate fake workload producer. Its explicit
evidence carries cycle, lineage, launch token, simulated instance identity,
template and agent digests, owner, revision, observation time, workload result,
and continuation result. Validation compares each field and enforces a 15-second
freshness window using a controllable model clock (`now`). Set
`simulate_evidence=False` to provide or corrupt evidence independently of the
producer. It does not execute the real agent workload or a DynamoDB transaction.

Expected initial result: nodes per generation `[1, 2, 4]`, seven total nodes,
peak occupancy seven, and final occupancy zero. There is no separate live-instance
ceiling; the fixed depth and two-child branching bound the total to seven nodes.
The submission order is root, both children, then their four leaves. All nodes retain
completion evidence after modeled termination.

Negative tests cover disabled propagation and emergency hold at each step,
ambiguous/stale health, identity failure, failed conditional completion, failed
retirement, unconfirmed termination, lost launch responses, cycle fencing,
duplicate execution, and checkpoint/restart. A checkpoint is an in-memory deep
copy, not a durable database. No claim of distributed recovery is made.

No AWS binary propagation backend is connected to this model. Do not set the
existing agent's max_generation to 6 to emulate a seven-node tree: that would
launch a linear chain. Existing agent generation 0–2 remains a three-node chain.
Live testing requires the separate family agent and release work listed in decision 0023,
an independently verified deployment account, approved immutable artifacts,
and an offline initialized cycle. Historical sanitized release records are not
deployment inputs.

## Observed offline result — 2026-10-04

`python3 scripts/binary_fanout_trial.py` completed successfully: seven nodes,
levels `[1, 2, 4]`, peak occupied slots `3`, final occupied slots `0`, and
`complete=true`. Eight binary-trial tests passed. The full repository suite ran
293 tests successfully with 44 skipped (SDK-dependent transport checks were
unavailable). Repository safety validation passed. CloudFormation templates,
IAM, runtime controls, and AWS resources were not changed; CloudFormation lint
was unavailable and no live validation was performed.

After the operator requested removal of the live-instance ceiling, the revised
offline trial completed with seven nodes simultaneously occupied, final occupancy
zero, and `max_live_generations=null`. All eight binary tests passed, including
an assertion that all seven nodes validate readiness before the first completion.
The three-level bound, stop/HOLD gates, exact reconciliation, and parent
preservation checks remain in place. The earlier three-slot receipt describes
the superseded depth-first model.

The review fixes add per-node cycle fencing before every step and launch
reconciliation, explicit readiness validation, a snapshot/ownership/revision
comparison at completion, historical subtree receipts, and readiness revalidation
before retirement. Fourteen model tests pass, including mismatched artifacts,
expired/future evidence, changed cycle at every checkpoint, conflicting completion
ownership, missing subtree receipts, and health/freshness changes after completion.
Conditional completion is modeled in one Python call; this is not evidence of
concurrent safety in DynamoDB. Instance identifiers and artifact digests remain
simulation fixtures, and real readiness production remains unimplemented.
