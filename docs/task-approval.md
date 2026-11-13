# Operator task approval and assignment preparation

A submitted plan remains an immutable draft proposal. An operator can separately approve a
specific task for preparation for assignment. This promotes its tracked state from draft to ready,
without assigning an owner, starting a bee, accepting evidence, or approving integration/release.
The plan's own status remains draft: task consent does not approve the whole plan.

The local operator chooses this gate explicitly by invoking the CLI. The MCP server has only the
read-only check_task_policy tool, available with --enable-planning. It has no approval/revocation
mutation tool. Local CLI/database access is the authority boundary; actor labels are audit labels,
not authenticated human identity. A client with equivalent filesystem/shell access is outside that
boundary. This is a single-operator prototype, not multi-user access control.

## Try the sample

```bash
uv sync --locked
uv run python scripts/spot_check.py --planning --keep .worker-bees/approval-demo
uv run worker-bees check-task-policy negative-price-validation T1 --db .worker-bees/approval-demo/state.db
```

The script simulates an approval and revocation only in its disposable sample database. It leaves
T1 unowned and draft, generation 2. Its approval-request.json is a fresh request template for
another decision, with generation 2 and the sample's exact hashes. Review the plan, current
specification, proposed scope, constraints, acceptance criteria, and validation procedures before
editing its reason and applying it:

```bash
uv run worker-bees get-plan negative-price-validation --db .worker-bees/approval-demo/state.db
uv run worker-bees get-task negative-price-validation T1 --db .worker-bees/approval-demo/state.db
uv run worker-bees decide-task .worker-bees/approval-demo/approval-request.json --db .worker-bees/approval-demo/state.db
uv run worker-bees check-task-policy negative-price-validation T1 --db .worker-bees/approval-demo/state.db
```

## Review and record a real decision

For an existing database, explicitly apply migrations through version 6 first:

```bash
uv run worker-bees init --db .worker-bees/state.db
uv run worker-bees check-task-policy PLAN_ID TASK_LOCAL_ID --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

Use the response's submission_sha256, specification_sha256, and approval_generation in a request
based on [the example](../examples/task-approval.json). The submission hash is the stored immutable
proposal's hash; use the returned value rather than recomputing it after schema defaults change.
Confirm that the current specification matches the reviewed proposal. prepare-spec reports a
text_sha256 that can be compared with specification_sha256 when the proposal uses that exact text.
For Word, compare extracted text hashes, not the Word package byte hash. A policy query accepts
--specification-sha256 HASH to report a replacement-spec mismatch; the MCP argument has the same name.
Omitting it does not check an external specification.

```json
{
  "plan_id": "negative-price-validation",
  "task_id": "T1",
  "submission_sha256": "REPLACE_WITH_REVIEWED_SUBMISSION_SHA256",
  "specification_sha256": "REPLACE_WITH_CURRENT_REVIEWED_TEXT_SHA256",
  "expected_generation": 0,
  "action": "approve",
  "reason": "Reviewed task scope, source, constraints, acceptance criteria, and current specification."
}
```

Replace both hash placeholders with lowercase 64-character SHA-256 values. Use the local task ID
(T1), not the derived database ID (PLAN_ID:T1). Request files are capped at 8 KiB. Unknown fields,
including caller-supplied actor/authority, are rejected.

```bash
uv run worker-bees decide-task approval.json --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

To revoke, read the latest policy, use its generation, set action to revoke, and record a reason.
Revocation returns ready tasks to draft; it preserves blocked/cancelled/other states. It remains
available after the repository index changes. Revocation does not cancel an external process.
Decisions are deliberately not blind idempotent retries. A stale generation fails: reread the
latest policy before deciding again. Two concurrent requests for the same generation cannot both
succeed, and replaying an old approval cannot override a revocation.

## Policy behavior

| Condition | Approval change | Assignment preparation policy |
|---|---|---|
| Unapproved, otherwise eligible draft | Operator may approve | require_approval |
| Approved ready task with all gates satisfied | Operator may revoke | allow |
| Incomplete or missing dependency | Consent may be recorded in advance | deny |
| Changed indexed source/context or supplied specification hash | Reassessment needed; revocation allowed | deny |
| Blocking decision linked to the task's requirements | Revise and resubmit the immutable proposal | deny |
| Linked requirement is deferred or needs_decision | Revise and resubmit | deny |
| Proposed feasibility is unknown/needs_investigation | Only investigation tasks may be approved | deny for implementation/validation |
| Proposed feasibility is blocked | Approval rejected | deny |
| Task is blocked, cancelled, terminal, or already owned | Approval rejected; revocation preserves state/owner | deny |

Feasibility is still a human/model claim. Approval records that the operator reviewed the proposal;
it does not independently prove feasibility or acceptance quality. Unrelated blocking decisions
are scoped by requirement links. Nonblocking questions remain visible in the plan but do not deny
this preparation gate. Resolve decisions by submitting a reviewed replacement plan with a new ID;
this slice does not mutate proposal decisions in place.

Freshness compares the plan with the latest indexed metadata for the same repository, excluding
snapshot ID and timestamp. Reindexing identical metadata does not invalidate consent. Dirty files,
unindexed commits, and an unspecified replacement specification cannot be detected by this check.
Use --snapshot consistently with the plan's original snapshot for reads and decisions; a server
pinned to a different snapshot cannot retrieve it. New/replacement plans do not inherit consent.

Both CLI and MCP policy reports are read-only and read one consistent SQLite transaction. A CLI
exit code of 0 means the report was computed; inspect policy.decision and blockers. Even allow is
limited to prepare_task_assignment, and every report has execution_available: false. It is a
point-in-time result, not an ownership claim. The opt-in [lease claim operation](task-leases.md) rechecks policy and reserves assignment
in the same write transaction. Active reservations add a task_reserved blocker to policy reports.

## Persistence and next gates

Migration 4 adds task_approvals with a per-task generation key and foreign-key link. Each decision
stores the actor label, reason, time, plan/spec hashes, snapshot, revision, and scope. The approval
record, task state update, and corresponding event commit or roll back together. All prior decision
payloads remain in task_approvals ordered by generation; policy reports expose the latest one.
No task/snapshot/plan payload rewrite is needed for the migration.

The legacy transition API still cannot promote planned tasks or enter execution. Fenced reservation leases and client-driven heartbeats are available separately. Runtime selection,
cancellation supervision, evidence-backed completion, action-specific
execution policies, and integration/release approval remain future work. This gate supplies a
reviewable consent boundary before those capabilities are added.

The operator-only local worker command now grants one fixed profile invocation separate execution
authority. Assignment approval still authorizes preparation only. See [local workers](local-workers.md).
