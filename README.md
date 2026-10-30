# Worker Bees

Python repository investigation with revision-bound evidence, plus a platform-neutral
coordination specification for recoverable AI development teams.

## Python runtime: first implementation

Requires Python 3.12+, Git, and [uv](https://docs.astral.sh/uv/). From this checkout:

```bash
uv sync --locked
uv run worker-bees index /path/to/your/repository
uv run worker-bees status
uv run worker-bees find 'package.module.function'
uv run worker-bees references 'package.module.function'
uv run worker-bees context 'package.module.function'
uv run worker-bees impact 'package.module.function' --depth 2
uv run worker-bees unresolved
```

The index command reads **committed Python source** at HEAD. Use `--revision <commit>` to
choose another revision. Uncommitted edits and untracked files are excluded. The database
defaults to `.worker-bees/state.db` in your current directory; use `--db /path/to/state.db`
consistently across commands to choose another location. Query commands return JSON containing
the snapshot, revision, coverage, and limitations. Pass `--snapshot <id>` to revisit an older
snapshot; otherwise the latest snapshot in that database is selected.

`find` searches qualified names literally. Use a returned symbol ID if a qualified name is
ambiguous. `references` returns the supported static **potential call sites**, not all uses
or proven runtime callers. Parameter calls, dynamic receiver types, decorated/rebound functions,
and other unsupported constructs remain visible through `unresolved`. A complete index means
in-scope files parsed successfully, not that all calls were resolved. Check `status` for failures
and exclusions before interpreting an empty result. Source context comes from the saved commit,
even if the working tree later changes.

Indexing does not import or execute target code. Tracked symlinks are skipped; oversized,
undecodable, and unparsable files make coverage partial. Source is stored locally in SQLite.
The local MCP adapter serves an existing snapshot over stdio. Optional feature planning records
unapproved drafts and tracked tasks. Bee execution, approval UI, and release automation remain future work.

## Spot-check it

```bash
uv run python scripts/spot_check.py
```

This creates a temporary sample repository, checks known callers and unknowns, verifies saved
source after an uncommitted edit, and tests the real MCP subprocess. No model API key is needed.
Use `--keep .worker-bees/demo` to retain a new demo directory for manual inspection.
See [the testing guide](docs/testing.md) for expected results and commands for your own code.

## Local MCP server

```bash
uv run worker-bees serve --db .worker-bees/state.db
```

Index a repository first. The server pins the latest snapshot at startup, or the snapshot selected
by `--snapshot ID`. It serves six investigation tools and cannot edit files or select arbitrary
source paths. A quiet terminal is normal: a local MCP client speaks over stdin/stdout.
See [MCP configuration and tools](docs/mcp.md). Remote HTTP transport is not implemented.

## Feature planning

```bash
uv run python scripts/spot_check.py --planning --keep .worker-bees/planning-demo
uv run worker-bees list-tasks negative-price-validation --db .worker-bees/planning-demo/state.db
```

This tracks a sample plan with requirement links, code references, dependencies, acceptance
criteria, and validation needs. Feasibility is a human/model-supplied proposal, not an independently
verified verdict. Draft submission never approves or executes work. The default MCP server stays
read-only; `--enable-planning` adds five tools, including draft writes. See [the planning guide](docs/planning.md).
For an existing database, run `worker-bees init --db PATH` once to apply schema migration 3.

Development checks:

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy
uv run pytest -q
uv build
```

See [architecture decisions](docs/architecture.md), [implementation status](docs/implementation-status.md),
and the [evaluation fixture](tests/fixtures/python_app). GitHub Actions is configured for Python
3.12, 3.13, and 3.14; remote checks run after the branch is pushed.

## Coordination specification

A platform-neutral starter for coordinating multiple AI agent sessions with explicit ownership, durable handoffs, independent verification, and clear authority boundaries.

- `SPEC.md`: core responsibilities and coordination contract.
- `FIELD-GUIDE.md`: detailed portable rules distilled from the source specification.
- `roles/`: operational charters for coordinator, contributor, reviewer, and optional task agent.
- `skills/`: claim/handoff, verification, review, integration, and dispatch procedures.
- `profiles/`: Git worktree example and runtime configuration template.
- `templates/`: starter durable records for board, task, and decisions.

## Start small

Choose a coordinator, one contributor, and a reviewer. Select one durable source of truth for task ownership and one place for team notes. Complete a runtime profile, agree on authority and review policy, and then begin with one contributor. Add workers or mechanisms only when a concrete need justifies their coordination cost.

A runtime may not support atomic claims, separate workspaces, persistent files, or autonomous monitoring. A profile must disclose capabilities and fallback procedures. Local aliases such as WorkerBee/Queen/Forager/Inspector are optional and carry no extra authority.

## Source and adaptation

This is a generalized adaptation of the WorkerBee Swarm specification and related role/skill templates. Deployment identifiers, personal authority provisions, incident-specific measurements, and stack-specific commands were omitted or moved to profiles. The field guide retains the reusable failure mechanisms and operating rules; use a local appendix for evidence that should remain private or project-specific.

Preserve local security and platform instructions when adapting these files.

## License and contributions

Copyright (c) 2026 napalmer7 and Worker Bees contributors.

Worker Bees is licensed under the **GNU Affero General Public License, version 3 only**
(`AGPL-3.0-only`). See [LICENSE](LICENSE) for the full terms. This declaration applies to
the original code and accompanying documentation in this repository unless a file states
otherwise; third-party material retains its own license.

Commercial use is allowed. When conveying covered software, comply with the AGPL source
and licensing requirements. If you modify Worker Bees and let users interact with that
modified version remotely over a network, section 13 requires a prominent offer of the
Corresponding Source to those users. The complete license controls these obligations.

We welcome improvements through pull requests to
[napalmer7/worker_bees](https://github.com/napalmer7/worker_bees). The license does not require
submission to this particular repository. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
upstream contribution process and [the licensing rationale](docs/licensing.md) for context.
