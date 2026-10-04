# 0023: Automatic cleanup after a completed propagation run

Approved by the operator October 3, 2026. This explicit decision supersedes
the deferred automatic cleanup scope for normal max-generation completion.
The existing bootstrap controller observes CURRENT stream records, reads fresh
CONTROL/CURRENT/HOLD/lifecycle state, and conditionally requests the existing
supported cleanup when the terminal generation owns CURRENT and predecessor
retirement is confirmed. Holds, disabled propagation, pending operator commands,
stale cycles and active cleanup block the request. Cleanup remains idempotent
and reconciles provisioning and exact resources before resetting the cycle.
No agent, Observer, IAM, image, or launch-template capability is changed.
The Observer remains read-only. History stays locally; terminal instances no
longer remain running after a successful normal completion. Failed or ambiguous
completion preserves instances for inspection.
