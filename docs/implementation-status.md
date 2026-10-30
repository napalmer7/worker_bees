# Implementation status

Python investigation core and read-only MCP adapter, October 30, 2026.

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
- Local stdio MCP adapter with six structured investigation tools, one startup-pinned snapshot,
  explicit input/output limits, and no indexing, filesystem, or mutation tools.
- Repeatable CLI/MCP spot-check script and guides for testing a sample or an existing repository.

## Next

1. Extend the curated evaluation set and measure results against text-search investigation.
2. Validate a selected desktop MCP host; the SDK-client stdio path is already tested.
3. Add structured feature-plan submission, dependency checks, and requirement traceability.
4. Add ownership leases, approvals, and policy enforcement before any managed bee execution.

The foundation implements parts of WB-001 through WB-010 in the plan. It is not a completed
milestone-1 or v0.1 release: no general imports/non-call-reference API, broader resolver evaluation,
desktop MCP host certification, or public package publication exists. CI is configured; remote CI
results are separate from local checks. See architecture.md for coverage and authority boundaries.

## Local verification

Verified on Python 3.12.14: 62 pytest cases, Ruff lint and formatting, strict mypy checks,
and source/wheel builds pass. The tests include production CLI queries, alias/relative-import
resolution, uncertain scope handling, syntax/encoding/symlink failures, schema migration,
state/event rollback, and preservation of old source snapshots after files change or disappear.
Additional adapter tests cover real current/legacy stdio clients, parity with core queries,
read-only access, snapshot pinning, path boundaries, partial coverage, and result budgets.
GitHub Actions tests Python 3.12/3.13/3.14; consult the current PR's checks for revision-bound results.
