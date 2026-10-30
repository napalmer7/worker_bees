# Python foundation decisions

Status: Python investigation core and local MCP adapter, October 30, 2026.

## Scope

The Python runtime implements a first investigation slice of the coordination specification.
It reads committed local Git trees, stores revision-bound source evidence, and exposes bounded
CLI queries and a read-only MCP adapter. It does not launch agents, approve changes, or merge code.

The single operator invokes CLI commands explicitly. Repository access is selected by the local
operator at index time. MCP tools query one operator-selected snapshot pinned at server startup;
they cannot select arbitrary source paths. Stored source is sensitive to the same degree as the repository.
The local database is not a multi-tenant authorization boundary.

## State and contracts

SQLite is authoritative for snapshots, tasks, and events. A task update and its event are committed
in the same transaction. Transitions compare the expected prior state under a write lock.
Task execution/completion transitions are disabled until ownership and evidence gates exist.
This is not yet an atomic claim/lease system. Frozen Pydantic records reject extra fields and
coercion. Read-only query commands open SQLite in read-only mode and do not create missing state.

Migrations 1 and 2 establish coordination records and immutable source snapshots respectively.
Index records are stored as versioned JSON payloads in SQLite for this bounded prototype. Queries
load one snapshot in memory. Normalize the graph into indexed tables when evaluation shows a need;
do not infer production-scale graph performance from this implementation.

## Source and resolution

Indexing resolves a commit and reads regular blobs by object ID. It never imports the target,
reads a tracked symlink's destination, runs Git hooks, or executes application setup commands.
Dirty and untracked files are not indexed. Each snapshot is identified independently and contains
its commit, tree, parser version, exclusions, source-size limits, coverage, and failures. All
context queries use stored source rather than a mutable local checkout.

The module-name convention supports repository-root modules and a single top-level src layout.
Duplicate module names disable imported-target resolution. Custom source roots, re-exports,
namespace-package configuration, and third-party package resolution need future adapters.

Calls resolve only through a single supported lexical definition or indexed import target.
Parameter bindings, rebinding, conditional definitions/imports, wildcard imports, decorated
definitions, dynamic receiver types, comprehensions, lambdas, generic type-parameter scopes,
and global/nonlocal declarations are treated conservatively. Some valid calls remain unresolved.
Global/nonlocal declarations invalidate related candidate bindings even when they are read-only.
No control-flow, receiver-type, runtime dispatch, or arbitrary mutation analysis is claimed.
Monkey patching or external side effects can invalidate static candidates.

Definitions and call sites are the initial graph. Non-call reads, inheritance relationships,
test coverage, Git ownership, and a general imports query are not implemented. A resolved call is
a static potential relationship, not proof that execution reaches it. Unknown calls and partial
files are preserved visibly, so an empty callers query is not an absence proof.

## Adapter and release decisions

CLI and MCP adapters reuse Investigation services. The official Python MCP SDK is pinned to 2.3.0;
the adapter serves stdio only and loads existing state in read-only mode. Tests use the actual
SDK client against a real subprocess in current and legacy modes; no desktop host is certified yet.
Tool input limits and a 64 KiB structured-result budget bound responses. There are no indexing,
filesystem, shell, or mutation tools. See docs/mcp.md for configuration and remaining boundaries.

Select one execution runtime before adding a supervisor. No model provider is assumed.

The existing specification documents remain the behavioral contract. Review/release authority
is not conferred by role names or a task record. The project's license is AGPL-3.0-only;
see LICENSE and docs/licensing.md. No public package release is introduced by this implementation.
