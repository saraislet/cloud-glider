# 0021: Successor-produced functional agent readiness

Status: accepted by the operator on October 3, 2026; source implementation only.
Deployment and an AWS benchmark require separate authorization.

The operator approved replacing the fixed two-heartbeat observation wait after
being told this gives up evidence of health over time. For the EC2 backend this
supersedes the sustained-heartbeat gate in decision 0001 and decision 0019.
The propagation agent is still the only workload. Legacy CloudFormation cycles
retain their existing gate; no separate application or application probe exists.

A candidate must prove its own functional readiness before conditional ownership
transfer. Using its own instance-role credentials, it verifies the approved
numeric Launch Template, digest and baked image, its exact EC2 configuration and
lineage, the expected parent CURRENT, enabled transactional controls, and capacity
and continuation DryRun for its next generation. A parent's DryRun alone cannot
qualify the candidate. At max_generation the candidate instead proves the explicit
boundary and never attempts an out-of-range DryRun. Candidates do not acquire the
propagation lease, submit instances, terminate predecessors or claim CURRENT.

The proof is written in the candidate's cycle/instance-fenced GEN STATE record.
Exact generation, instance, artifact/configuration identity and handoff token are
required, together with a control fingerprint, producer instance, continuation
result and a proof timestamp. The timestamp marks the start of the checks;
verification taking longer than 15 seconds fails closed. Parent acceptance rejects
missing, foreign, stale or future evidence. Five-second proof refresh and the
15-second freshness limit are independent of heartbeat telemetry and readiness
polling. Neither defines a minimum healthy observation window. Telemetry says
agent_live, not workload_healthy. Failed functional probes invalidate prior proof.

The parent retains its lease, exact candidate validation, independent continuation
recheck, final fresh enabled controls and the conditional handoff transaction.
The transaction matches the whole candidate STATE, including the proof, and
checks CONTROL, HOLD, lifecycle, lease and old CURRENT. Successful handoff stores
the accepted proof in CURRENT as durable retirement authorization. Its freshness
was required at handoff; expiration afterward does not abandon an authorized,
identity-verified retirement. Fresh stop/HOLD, approved-control and exact instance
checks still fence retirement. An older unfinished predecessor must be confirmed
terminated before candidate ownership can advance (decision 0020).

A passing proof establishes recent functional capability, not sustained health,
future capacity or workload availability. DryRun does not reserve capacity. A
candidate can fail after publishing proof; service restart or operator recovery
remains necessary under the existing bounded model. All missing or conflicting
evidence preserves live predecessors. No new recovery automation is introduced.

The one-second ownership loop from the polling change now reads CONTROL, HOLD,
BOOTSTRAP and CURRENT in one DynamoDB transaction. Fresh prelaunch controls remain
the last AWS operation before a real RunInstances; handoff checks are retained.
The three-instance absolute ceiling and deployed max_generation=10 remain intact.
No runtime defaults, infrastructure, IAM, deployment permissions or workflows
change. A matching rebuilt, tested AMI and reviewed immutable pins are required
before activation; never mix readiness protocols within a live cycle.

See [the operational and request-cost assessment](../ec2-functional-readiness.md).
