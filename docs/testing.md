# Spot-checking Worker Bees

Use a development computer with Python 3.12+, Git, and uv. Run these commands from a checkout
of Worker Bees. No model API key is needed for indexing or these checks.

## One-command demonstration

```bash
uv sync --locked
uv run python scripts/spot_check.py
```

The script creates a temporary Git repository containing the small shop example, runs the
installed CLI in separate processes, and launches the MCP server as a real stdio subprocess.
It does not edit your own repositories or change your global Git settings. The temporary
repository and database are removed afterward. A failure returns a nonzero exit code.

Expected output contains these PASS checks:

| Check | Expected behavior |
|---|---|
| Committed source | Three Python files indexed without importing the sample application |
| Potential callers | Three call sites for shop.pricing.total, all in shop/service.py |
| Unknowns | handler and self.quote remain unresolved |
| Source context | An uncommitted edit does not change the saved source |
| Impact | Four potential callers of normalize within two levels |
| MCP discovery | Six tools, served over stdio |
| MCP/CLI parity | The same snapshot and three callers |
| Failure handling | A missing symbol is an error, not a successful empty result |

To retain the example for manual inspection, choose a directory that does not already exist:

```bash
uv run python scripts/spot_check.py --keep .worker-bees/demo
uv run worker-bees references shop.pricing.total --db .worker-bees/demo/state.db
uv run worker-bees context shop.pricing.total --db .worker-bees/demo/state.db
uv run worker-bees unresolved --db .worker-bees/demo/state.db
```

The script refuses to overwrite an existing demo directory. Pick a new path for another retained
run. The .worker-bees directory is ignored by Git.

## Investigate the Worker Bees repository itself

```bash
uv run worker-bees index . --db .worker-bees/self.db
uv run worker-bees status --db .worker-bees/self.db
uv run worker-bees find build_index --db .worker-bees/self.db
uv run worker-bees references worker_bees.analysis.build_index --db .worker-bees/self.db
uv run worker-bees context worker_bees.analysis.build_index --db .worker-bees/self.db
uv run worker-bees impact worker_bees.analysis.build_index --db .worker-bees/self.db
```

For a different Python project, replace the index command's dot with its Git repository path.
Then use qualified names or IDs returned by find. Keep using the same explicit --db path.
The repository's tracked Python content must be committed before indexing can see it.

Compare a reported call's path, line, and expression with source at the reported commit:

```bash
git -C /path/to/indexed/repository show COMMIT:path/to/file.py
```

The current checkout's line numbers may differ. Unknown calls are expected for external
dependencies, dynamic dispatch, unsupported scopes, or ambiguous bindings. They are not proof
the application is broken. A complete index means in-scope files were parsed, not that every
relationship was resolved. Search is a literal qualified-name substring match, not semantic search.

## Try MCP through a local client

See [MCP setup](mcp.md) for process configuration and example tool calls. The spot-check script
already tests the real MCP transport without needing a desktop host. An example model prompt is:

> Use Worker Bees to find shop.pricing.total, list its potential callers, and show its source.
> Name the indexed commit and explain unresolved-call limitations.

Use the retained sample database for that prompt. A desktop host has not been operated as part
of the automated tests; supported wire paths are tested with the official SDK client.

## Automated regression checks

```bash
uv run pytest -q
uv run pytest tests/test_mcp.py -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy
uv build
```

Tests use temporary repositories and databases. MCP tests include real stdio subprocesses with
current auto-negotiation and legacy initialization, tool discovery, core-query parity, read-only
database access, snapshot pinning, cross-repository boundaries, input limits, output budgets,
and explicit failure/partial-coverage reporting. They do not establish a performance benchmark
or unrestricted compatibility with every MCP host.
