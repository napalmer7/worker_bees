# Implementation status

Initial Python investigation slice, October 30, 2026.

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

## Next

1. Extend the curated evaluation set and measure results against text-search investigation.
2. Select the target MCP client, pin the official SDK, and add the read-only MCP adapter.
3. Add structured feature-plan submission, dependency checks, and requirement traceability.
4. Add ownership leases, approvals, and policy enforcement before any managed bee execution.

The foundation implements parts of WB-001 through WB-009 in the plan. It is not a completed
milestone-1 or v0.1 release: no general imports/non-call-reference API, broader resolver evaluation,
MCP host test, release license, or public package publication exists. CI is configured; remote CI
results are separate from local checks. See architecture.md for coverage and authority boundaries.

## Local verification

Verified on Python 3.12.14: 44 pytest cases, Ruff lint and formatting, strict mypy checks,
and source/wheel builds pass. The tests include production CLI queries, alias/relative-import
resolution, uncertain scope handling, syntax/encoding/symlink failures, schema migration,
state/event rollback, and preservation of old source snapshots after files change or disappear.
Python 3.13/3.14 and remote GitHub Actions have not been run in this workspace.
