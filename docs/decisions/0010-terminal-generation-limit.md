# Decision 0010: terminal generation limit

Date: 2026-10-02
Status: accepted

A current owner at or above `max_generation` completes its agent process with
exit status 0 after one final heartbeat and completion log. It does not sleep,
poll control, or create another generation. This applies even if propagation
is disabled or emergency hold is active. Candidates retain their normal health
cadence until authoritative handoff establishes current ownership.

The generation service already uses `Restart=on-failure`, so successful
completion remains stopped. Raising `max_generation` after completion is not
a resume mechanism. Configure the next run's limit and start again from
generation 0 only after operator cleanup and reconciliation of retained state.
No automated cleanup, bootstrap rearming, or CURRENT reset is introduced.

All health, continuation, conditional ownership, and retirement gates remain
intact. Exiting does not delete the final generation or end its infrastructure
charges. Operators must disable propagation, verify no creation is in flight,
inspect resources, and perform CloudFormation cleanup before a new run.
