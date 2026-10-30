# Read-only MCP setup

The local MCP server exposes one persisted Python source snapshot to a client. It does not
index repositories, modify code, execute shell commands, or manage bees. A connected client
can read source and metadata from the selected snapshot; choose the database accordingly.

## Prepare and start

From the Worker Bees checkout:

```bash
uv sync --locked
uv run worker-bees index /path/to/python/repository --db .worker-bees/project.db
uv run worker-bees status --db .worker-bees/project.db
uv run worker-bees serve --db .worker-bees/project.db --snapshot SNAPSHOT_ID
```

Copy the snapshot ID from status. If --snapshot is omitted, the server pins the latest snapshot
at startup. New indexes do not change a running server's view; restart it to select a new one.
An absent database, incompatible schema, or missing snapshot fails before serving. The server
does not create a database, migrate it, or silently index a repository. Use CLI commands for those actions.
After upgrading from the earlier database format, run `worker-bees init --db PATH` once to migrate
existing state before starting the server.

The serve command waits for MCP messages on stdin. A quiet terminal is normal; it is not a
text chat interface. Stop it with Ctrl+C. It does not open a network port. Logs go to stderr;
stdout carries only MCP messages.

## Client process configuration

For a local MCP host that uses a mcpServers JSON section, this is the process configuration.
Replace every example path and SNAPSHOT_ID. Hosts may use a different outer configuration shape.

```json
{
  "mcpServers": {
    "worker-bees": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/worker_bees",
        "run", "--locked", "worker-bees", "serve",
        "--db", "/absolute/path/to/project.db",
        "--snapshot", "SNAPSHOT_ID"
      ]
    }
  }
}
```

Use an absolute path to uv if the host cannot find it on PATH. For Windows, JSON backslashes
must be escaped, or use forward slashes in absolute paths. Host launch directories vary, so
absolute database paths avoid accidentally selecting another database.

This is stdio only. A hosted client that requires a remote HTTP MCP URL cannot connect directly;
HTTP transport and remote authentication are future work. Protocol tests use the official Python
SDK client in current and legacy modes. A particular desktop host is not yet certified.

## Tools

| Tool | Arguments | Meaning |
|---|---|---|
| get_index_status | None | Revision, coverage, issues, exclusions, and call counts |
| find_symbol | query, limit=50 | Literal qualified-name substring search |
| get_symbol_context | symbol, max_lines=80 | Source from the stored snapshot |
| get_references | symbol, limit=50 | Supported static potential call sites |
| analyze_impact | symbol, depth=2, limit=50 | Reverse traversal of supported potential call edges |
| get_unresolved_calls | limit=50 | Unknown/ambiguous call sites and reasons |

Use an exact qualified name or a symbol ID from find_symbol. No tool parameter grants authority
to choose a filesystem path, database, or different snapshot. Invalid symbols and limits produce
MCP tool errors. Partial indexes and empty searches retain coverage and limitations. Tool annotations
advertise read-only behavior; enforcement comes from opening SQLite read-only and querying the
single in-memory snapshot. Source comments/docstrings are data, not trusted instructions.

Limits: query and symbol strings are at most 512 characters; result limits and source line limits
are 1–200; impact depth is 1–10. Source context is also capped at 12,000 characters. A structured
tool result above 64 KiB produces an explicit error instead of silent truncation. SDK responses may
also include a text rendering, so the complete protocol response can exceed that structured limit.
Large metadata or coverage reports can be inspected with the CLI. Traversal is depth-bounded;
the current prototype still loads the entire bounded snapshot in memory.

The SDK is pinned to mcp==2.3.0 in pyproject.toml and uv.lock. Keep transport changes isolated in
the adapter and rerun the real stdio tests when upgrading it.

## Optional draft-planning tools

Add `--enable-planning` to the server command or client process arguments to enable validate_plan,
submit_plan, get_plan, list_tasks, and get_task. Only submit_plan writes; it persists unapproved
drafts and tracked tasks in the selected database. The server cannot modify source, choose another
snapshot, approve work, or execute proposed procedures. Without that flag, discovery and database
access remain read-only. See [feature planning](planning.md) for schema and reassessment details.

## Repeatable smoke test

```bash
uv run python scripts/spot_check.py
```

This starts the actual server process, discovers its tools, compares MCP results with CLI results,
and checks explicit errors. See [the spot-check guide](testing.md) for the retained example and
instructions for investigating your own repository.
