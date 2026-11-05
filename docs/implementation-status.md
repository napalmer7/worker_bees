# Implementation status

Python investigation, planning, and agent assistance, November 5, 2026.

## Implemented

- Package scaffold, lockfile, CLI entry point, and GitHub Actions configuration.
- Versioned snapshot, symbol, call, task, evidence, event, and policy-decision records.
- Transactional SQLite persistence, schema migrations, read-only query access, and guarded
  non-execution task transitions. Writes and corresponding events roll back together.
- Committed-tree discovery, source decoding, exclusions, byte limits, and partial-index reporting.
- Python definitions, signatures, docstrings, conservative lexical/import call candidates,
  and unresolved-call records.
- CLI status, symbol search, source context, potential callers, unresolved calls, and reverse
  call impact queries against a chosen snapshot.
- Curated Python fixture and regression tests covering the production CLI and persistence paths.
- Default local stdio MCP adapter with six investigation tools, one startup-pinned snapshot,
  explicit input/output limits, and no indexing, filesystem, or mutation tools.
- Repeatable CLI/MCP spot-check script and guides for testing a sample or an existing repository.
- Structured plan proposals with feasibility claims, code references, declared requirement coverage,
  dependency checks, acceptance criteria, validation needs, and unresolved decisions.
- Transactional draft-plan/task/event creation, idempotent retries, and indexed/spec freshness flags.
- Five opt-in MCP planning tools and equivalent CLI commands; the default six-tool server is read-only.
- Two planning/review MCP prompts and three read-only assistance tools for workflow guidance,
  source-block preparation, and advisory assessment of reported acceptance evidence.
- Bounded operator-only DOCX/text/Markdown intake, optional requirement source citations, and
  normalized legacy-plan retry compatibility without rewriting stored plan payloads.
- Operator-only task approval/revocation bound to reviewed plan/spec hashes, append-only decision
  generations, atomic task/event writes, and migration 4 preserving existing records.
- Read-only CLI/MCP assignment preparation checks with dependency, freshness, feasibility, decision,
  and approval blockers. Planning now exposes 15 tools; default discovery remains six tools.

## Next

1. Extend the curated evaluation set and measure results against text-search investigation.
2. Validate a selected desktop MCP host; the SDK-client stdio path is already tested.
3. Evaluate model/human-authored plan quality against representative feature requests.
4. Add atomic ownership leases and runtime/action policy enforcement before managed bee execution.
   Operator approval for assignment preparation is implemented; execution/release approval is not.

The foundation implements parts of WB-001 through WB-010 in the plan. It is not a completed
milestone-1 or v0.1 release: no general imports/non-call-reference API, broader resolver evaluation,
desktop MCP host certification, or public package publication exists. CI is configured; remote CI
results are separate from local checks. See architecture.md for coverage and authority boundaries.

## Local verification

The local suite includes investigation, CLI/MCP, and planning regression cases. Ruff lint/formatting,
strict mypy checks, and source/wheel builds pass. The tests include production CLI queries, alias/relative-import
resolution, uncertain scope handling, syntax/encoding/symlink failures, schema migration,
state/event rollback, and preservation of old source snapshots after files change or disappear.
Additional adapter tests cover real current/legacy stdio clients, parity with core queries,
read-only access, snapshot pinning, path boundaries, partial coverage, and result budgets.
Planning checks cover coverage, cycles, code references, concurrent retries/conflicts, transaction
rollback, database upgrades, freshness, and rejection of ownership/approval fields. Planning procedures
are stored as data, not executed. Feasibility and acceptance quality are not independently established.
Assistance tests cover document omissions/limits, malformed packages, source offsets/hashes,
prompt discovery, evidence revisions/methods, missing validation, conflicting results, and
advisory-only recommendations without task-state changes.
GitHub Actions tests Python 3.12/3.13/3.14; consult the current PR's checks for revision-bound results.

Approval tests cover concurrent decisions, stale replay/revocation, hash mismatches, source freshness,
operator CLI, real current/legacy MCP access, lifecycle bypass, migration, and atomic rollback.
