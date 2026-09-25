# Temporary shared concurrency for emergency hold

Status: accepted by operator request, 2026-09-22

The sandbox Lambda regional concurrency quota is 10, all unreserved. AWS rejected
the emergency-hold function's reservation of 2 because it would leave fewer than
the account's required 10 unreserved executions. The operator requested temporary
use of shared concurrency while awaiting a quota increase.

`EmergencyHoldReservedConcurrency` defaults to 2 and accepts -1, 1, or 2. The -1
sentinel omits `ReservedConcurrentExecutions` through `AWS::NoValue`. It never
sets Lambda concurrency to zero, which would disable execution. The sandbox
repair explicitly selects -1 and later updates preserve that parameter value.

Shared concurrency removes this function's dedicated allocation and individual
concurrency cap. It competes within the account's regional pool, currently 10.
This temporary tradeoff does not change the agent's control checks or authorize
propagation. No quota increase is submitted by this change.

After the quota increase is approved, verify available capacity and prepare a
reviewed CloudFormation UPDATE setting the parameter to 2. Do not rely on the
template default: CloudFormation retains the existing -1 value on later updates
unless explicitly overridden. Recheck workload behavior before the first
propagation test. Notification-delivery verification is deferred to V2.

The missing execution role is created through a small CloudFormation UPDATE;
an absent resource cannot be imported. Its trust remains Lambda-only and its
permissions stay restricted to the existing Cloud Glider records and the
emergency-hold log streams. The update preserves other imported resources.
