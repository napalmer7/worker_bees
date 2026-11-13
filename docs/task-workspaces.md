# Lease-bound task briefs and prepared workspaces

A reserved task can now supply a complete task brief and an operator-prepared Git worktree at its
original base revision. This is preparation for a future execution adapter. No agent/model is
launched, target code is not imported, validation procedures are not run, and task state stays ready.
Every brief and manifest retains execution_available: false.

## Try it

```bash
uv sync --locked
uv run python scripts/spot_check.py --workspaces --keep .worker-bees/workspace-demo
```

Use a new --keep directory. --workspaces includes the investigation, planning, approval, and lease
demos, then prepares task-workspace/checkout, task-workspace/task-brief.json, and
task-workspace/manifest.json. It uses a real MCP subprocess for the brief and the operator CLI for
filesystem preparation. Bearer credentials stay in memory or a private, automatically removed
temporary request file; they are not printed or retained in the demo. The fixture application stays
unchanged. At the end, the sample lease is released and approval revoked. Its prepared manifest is
a historical preparation record, not current ownership or permission to use the checkout.

This feature adds no database migration beyond lease schema 5. Existing older databases still need
worker-bees init --db PATH before use.

## Export a task brief

Create a request with the current lease credentials and current reviewed specification text hash:

```json
{
  "plan_id": "negative-price-validation",
  "task_id": "T1",
  "owner": "bee-session-001",
  "lease_id": "REPLACE_WITH_LEASE_ID",
  "generation": 1,
  "token": "REPLACE_WITH_PRIVATE_LEASE_TOKEN",
  "specification_sha256": "REPLACE_WITH_CURRENT_REVIEWED_TEXT_SHA256"
}
```

Use actual lease values, lowercase 64-character token/hash values, and the local task ID. Keep the
request private. The request has no ttl_seconds: exporting a brief does not renew the lease.
The CLI reuses the 8 KiB request-file budget; unknown fields are rejected.

```bash
uv run worker-bees get-task-brief context.json --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

Equivalent MCP: get_task_brief with the nested request object, available with --enable-planning.
It is read-only and requires a live lease even when the server has no coordination mutation tools.
Planning now exposes 17 tools and two prompts; adding --enable-coordination exposes 20 tools.
The default six investigation tools and their permissions are unchanged. There is no MCP
prepare_workspace tool and no document/source-path argument on the brief tool.

The brief contains:

- Original snapshot, revision, plan/specification hashes, and exact specification text.
- Plan constraints and proposed feasibility claims, including assumptions, unknowns, and risks.
- This task's requirement links, related decision questions, dependencies, affected symbols,
  exclusions, shared contracts, acceptance criteria, and complete validation procedures.
- The current reservation's identity, fencing/approval generations, effective owner, and deadline.
- Explicit scope, evidence, untrusted-content, and runtime authority instructions.

Tokens and token hashes are never included in the brief or manifest. Briefs remain sensitive to the
same degree as the source/specification. The existing 64 KiB MCP result budget applies; oversized
metadata produces an error rather than silent truncation, and the CLI can export larger results.

## Operator workspace preparation

Choose a new destination outside the source repository and its shared Git metadata. Its parent must
already exist; existing destinations, including symlinks, are refused. The destination is created
with private owner permissions on POSIX systems.

```bash
uv run worker-bees prepare-workspace context.json /path/to/new-task-workspace --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

The source repository comes from the indexed snapshot, not the request. Preparation verifies that
the local Git root and commit/tree match that snapshot, then registers a detached worktree without
checkout. It fills the worktree's private index with read-tree and writes regular committed blob
bytes itself. Hooks, checkout/smudge filters, filesystem monitor commands, automatic maintenance,
inherited Git override environment variables, and replacement objects are disabled or bypassed by
these preparation operations. No dependencies, submodules, LFS objects, or remote content are fetched.
LFS pointers remain committed pointer bytes; checkout transformations/line-ending conversions are
not applied. Partial/promisor clones are rejected before registration; complete local objects
are required. Executable regular-file modes are preserved without executing files.

The preparation path supports only regular committed files. Symlinks, gitlinks/submodules, unsafe
paths, non-UTF-8 names, and case-colliding paths are rejected before worktree creation. Limits are
10,000 files, 8 MB per file, 128 MB total committed file bytes, a 16 MB captured Git response budget,
and a 30-second timeout per Git operation. These are prototype preparation limits, not OS resource
isolation. Git response limits are checked after capture; they do not bound subprocess peak memory.
Python 3.12–3.14 CI exercises this path on Linux; Windows/macOS behavior is not certified.

Source working-tree edits and branch refs are preserved. Worktree registration changes the source's
shared Git administrative metadata, and the new checkout shares the object database and repository
configuration. A worktree is not a process, network, filesystem, or credential sandbox. Later manual
Git commands may use that shared configuration; the preparation helper's protections do not apply
to arbitrary commands an external process chooses to run.

## Lease checks and failure records

The operator command takes the SQLite coordination write lock while it verifies credentials,
approval, dependencies, task state, and indexed context and prepares the worktree. It checks expiry
during copying and validates the lease again before finishing. It does not renew the lease. Other
coordination writes wait for this preparation transaction and may hit their normal SQLite timeout;
keep preparations small and leave ample lease time. Runtime jobs must not hold this write lock.

A fresh destination receives a manifest with preparing status. A completed attempt records the
worktree/brief paths, exact revision/tree, reservation identity and generations, file counts/bytes,
and prepared status, plus a workspace_prepared task audit event. A known failure marks the attempt
failed and reports its retained path. A process crash can leave preparing or another last-written
status; filesystem changes and SQLite commits are not one atomic transaction. A prepared manifest
alone is not evidence of a committed audit event or current authority. Inspect both state and files
when recovering an interrupted operation.

Failed or interrupted worktrees are retained for operator inspection. The command never overwrites
an existing attempt, removes user files, prunes other worktrees, creates branches, merges changes,
or automatically deletes a workspace after lease release/expiry. The operator manages retention and
Git worktree removal after inspecting any work. Failed preparations do not report successful
execution, extend approval, or change task lifecycle.

## Next execution adapter

An execution adapter must select a runtime, bind its workspace and capabilities to the lease,
provide independent heartbeat/cancellation supervision, and enforce current fencing and action
policy before protected effects. It must not infer execution permission from a ready task, exported
brief, prepared manifest, or prior allow report. Validation, acceptance, integration, and release
still require their own gates.
