# Python foundation decisions

Status: initial implementation, October 30, 2026.

## Scope

The Python runtime implements a first investigation slice of the coordination specification.
It reads committed local Git trees, stores revision-bound source evidence, and exposes bounded
CLI queries. It does not launch agents, approve changes, merge code, or serve MCP yet.

The single operator invokes CLI commands explicitly. Repository access is selected by the local
operator at index time. Future MCP tools must query operator-approved snapshots; they must not
accept arbitrary source paths. Stored source is sensitive to the same degree as the repository.
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

Keep CLI and future MCP adapters thin and reuse Investigation services. Select and test an actual
MCP client before advertising integration. Select one execution runtime before adding a supervisor.
No provider is assumed. Transport dependencies and APIs will be pinned when that adapter is built.

The existing specification documents remain the behavioral contract. Review/release authority
is not conferred by role names or a task record. The project's license is AGPL-3.0-only;
see LICENSE and docs/licensing.md. No public package release is introduced by this implementation.
