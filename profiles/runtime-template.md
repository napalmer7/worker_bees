# Runtime profile template

Copy this file and complete it for the target agent runtime and project.

## Environment
- Agent host / model interface: [name and relevant version]
- Durable shared storage: [location and access model]
- Message/dispatch facility: [capabilities, limits, delivery semantics]
- Work queue / tracker: [source of truth]
- Workspace isolation: [worktree / clone / container / serialization]
- Default branch / target: [per repository]

## Ownership mechanism
- Claim operation: [atomic operation or single-coordinator procedure]
- Read-back check: [how the claimant confirms persisted ownership]
- Release / expiry / abandoned claim recovery: [procedure]
- If two claims conflict: [procedure]

## Review and release
- Feedback channels to inspect: [reviews, comments, checks, other]
- Which checks run on this change path: [list]
- Which checks are required/blocking: [list]
- Approval freshness and re-review triggers: [policy]
- Who can block, approve, merge, deploy: [explicit authority]
- Actions requiring human approval: [list]

## Durable files and recovery
- Board owner / update model: [single writer or concurrency-safe]
- Per-agent state location: [pattern]
- How sessions resume: [procedure]
- Cleanup hazards and recovery artifacts: [procedure]

## Local exceptions and drifting facts
| Core rule changed | Local rule | Reason and evidence | Authorized by | Re-check trigger |
|---|---|---|---|---|
| [rule] | [exception] | [why] | [role/person] | [date/event] |
