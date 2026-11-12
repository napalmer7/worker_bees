# Fenced task assignment leases

An approved ready task can be reserved by one bee session at a time. This slice supplies the
coordination boundary before a runtime is allowed to launch agents. A lease is an assignment
reservation, not execution, completion, merge, or release authority. Task.status stays ready and
Task.owner stays null; the separate lease's effective_owner is the authoritative reservation owner.
Use get_task_lease or check_task_policy to see effective ownership, including expiry/invalidation.

## Spot-check the complete flow

```bash
uv sync --locked
uv run python scripts/spot_check.py --coordination --keep .worker-bees/lease-demo
uv run worker-bees get-task-lease negative-price-validation T1 --db .worker-bees/lease-demo/state.db
```

Use a fresh directory. --coordination includes the investigation, planning, and approval demos,
then exercises a real MCP subprocess with claim, heartbeat, rejected duplicate claim, and release.
It leaves the sample task draft with approval revoked and its reservation released. The sample
application is not changed. Tokens stay in memory during the demonstration and are not printed
or written to retained request files. Time expiry, competing claims, fencing, and rollback are
covered by deterministic regression tests instead of sleeping in the sample.

Existing databases require explicit migration 5:

```bash
uv run worker-bees init --db .worker-bees/state.db
```

Migration preserves tasks, plans, approvals, and source snapshots. The read-only query/server path
rejects older schemas until the operator migrates them.

## MCP capability opt-in

```bash
uv run worker-bees serve --db .worker-bees/state.db --snapshot SNAPSHOT_ID --enable-planning --enable-coordination
```

| Mode | Tools | Database mutations |
|---|---|---|
| Default | 6 investigation tools | None |
| --enable-planning | 17 investigation/planning/assistance/policy tools, two prompts | Draft submission only |
| Both flags | 20 tools, two prompts | Draft submission and lease claim/renew/release |

--enable-coordination requires --enable-planning. Planning alone can inspect reservations with
get_task_lease but cannot mutate them. Operator approvals remain CLI-only. MCP clients cannot
select another database, snapshot, repository path, or execution command through lease tools.
All lease mutations are conservatively annotated non-idempotent. A repeated release of the current
matching released lease is a no-op; a claim is never blindly retried after a lost response.

## Prepare a claim

First follow [operator task approval](task-approval.md), then read check_task_policy and
get_task_lease in the plan's original snapshot. A claim includes the exact stored proposal hash,
current reviewed specification text hash, latest approval generation, and latest lease generation
(0 if absent). The request is checked again under the write lock, so a preceding allow report does
not bypass a later revocation, dependency/state change, or index update.

```json
{
  "plan_id": "negative-price-validation",
  "task_id": "T1",
  "owner": "bee-session-001",
  "submission_sha256": "REPLACE_WITH_REVIEWED_SUBMISSION_SHA256",
  "specification_sha256": "REPLACE_WITH_CURRENT_REVIEWED_TEXT_SHA256",
  "expected_approval_generation": 1,
  "expected_lease_generation": 0,
  "ttl_seconds": 120
}
```

Owner is a session label with the same identifier constraints as plan/task IDs, not authenticated
identity. Hash placeholders must be replaced with lowercase 64-character hashes. TTL is a strict
integer from 30 to 900 seconds, default 120. Unknown request fields are rejected. CLI request files
are capped at 8 KiB. Use local task IDs (T1) rather than derived database IDs (PLAN_ID:T1).

```bash
uv run worker-bees claim-task claim.json --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

Equivalent MCP: claim_task with a nested request object. The grant contains a public lease view
and a private 256-bit bearer token. Retain the token in the authorized client's private session;
CLI stdout includes it, so choose a private destination and avoid shared logs. Losing the claim
response does not allow blind retries: inspect the public lease and wait for expiry if the token
cannot be recovered. Inspection cannot recover a token.

The database stores only its SHA-256 hash. Public lease/policy reads omit both the token and its
hash. Task/approval/lease events record action and actor labels without bearer credentials. This
remains a local, trusted database model, not a multi-user credential service. A process with local
CLI/database access can administer state, so owner labels and tool annotations are not isolation.

## Heartbeat and release

A heartbeat contains plan_id, task_id, owner, lease_id, generation, token, the current reviewed
specification_sha256, and optional ttl_seconds. A release contains only the credential fields.
The grant's lease_id and generation identify the reservation, not the task or approval generation.
See [the renewal template](../examples/lease-renewal.json).

```bash
uv run worker-bees renew-task-lease heartbeat.json --db .worker-bees/state.db --snapshot SNAPSHOT_ID
uv run worker-bees release-task-lease credentials.json --db .worker-bees/state.db --snapshot SNAPSHOT_ID
uv run worker-bees get-task-lease PLAN_ID TASK_ID --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

Equivalent MCP tools are renew_task_lease and release_task_lease. Heartbeats recheck approval,
context, dependencies, and lifecycle. An expired, released, or invalidated lease cannot be renewed.
Expiry is exclusive: at expires_at_ms it is already expired. A heartbeat extends the deadline to
at least observed server time plus TTL and never shortens an existing deadline. Heartbeats should
arrive comfortably before expiry; no background scheduler sends them on a client's behalf.

Release verifies the current lease's owner, lease ID, fencing generation, and token. It remains
available after expiry or source/spec changes, and does not complete a task. Once a newer lease
exists, an old holder can neither renew nor release it. Release of an invalidated reservation is
a no-op that preserves its invalidated state. No handoff artifact or acceptance is implied.

## Effective state and fencing

| State | effective_owner | Meaning |
|---|---|---|
| absent | null | No reservation has ever been recorded |
| active | Session label | Open, unexpired reservation with valid approval/context |
| expired | null | Open reservation reached its deadline |
| released | null | Holder explicitly ended the reservation |
| invalidated | null | Operator/lifecycle invalidation, changed context, or detected clock rollback |

The historical owner remains visible separately from effective_owner. Reads compute state from
one consistent SQLite snapshot and observed_at_ms; they do not modify state or depend on a sweeper.
Policy reasons may invalidate an otherwise unexpired open reservation. Supplying a replacement
specification hash to a check/renewal evaluates that supplied context; unspecified external specs
and live/unindexed repository changes remain outside the database's knowledge.

Each successful claim increments a per-task generation that is never reused during normal database
operation, even for the same owner label. Claim, heartbeat, release, and related audit events run
under BEGIN IMMEDIATE and roll back together on failure. Old claim rows are retained; heartbeats
update their current deadline, with action events retained separately. A revoked approval invalidates
an open lease in the same transaction as the approval/task update. Blocking/cancelling through the
legacy lifecycle API also invalidates it atomically. Reapproval does not revive invalidated leases;
a new fenced claim is required.

Fencing currently protects these database operations only. No runtime or external resource consumer
exists yet. A future runtime must recheck active credentials, approval, and the latest fencing number
before every protected effect; this PR does not stop an arbitrary external process. Expiry uses one
local wall clock. A timestamp earlier than the recorded heartbeat is rejected as a detected rollback,
but arbitrary clock jumps, database backups/restores, and distributed clocks are not fully solved.
Use a stable local clock and do not treat this prototype as a distributed lock service.

## Next runtime boundary

The next slice must choose an execution adapter, bind workspaces/capabilities to leases, define
heartbeat/cancellation supervision, and enforce action-specific policy and evidence-backed lifecycle
transitions. Approval and reservation continue to have separate authority from execution and release.

A lease holder can now export a scoped task brief; the operator can prepare a detached worktree
at the original revision. This is preparation only, not a runtime. See [task workspaces](task-workspaces.md).
