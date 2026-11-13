# Python foundation decisions

Status: Python investigation, local MCP, planning, and agent assistance, November 12, 2026.

## Scope

The Python runtime implements a first investigation slice of the coordination specification.
It reads committed local Git trees, stores revision-bound source evidence, and exposes bounded
CLI queries and a read-only MCP adapter. It does not launch agents, approve changes, or merge code.
An opt-in planning layer records unapproved plans and draft tasks without changing repository source.

The single operator invokes CLI commands explicitly. Repository access is selected by the local
operator at index time. MCP tools query one operator-selected snapshot pinned at server startup;
they cannot select arbitrary source paths. Stored source is sensitive to the same degree as the repository.
The local database is not a multi-tenant authorization boundary.

## State and contracts

SQLite is authoritative for snapshots, tasks, and events. A task update and its event are committed
in the same transaction. Transitions compare the expected prior state under a write lock.
Task execution/completion transitions are disabled until ownership and evidence gates exist.
Operator approval is a separate transactional gate that can promote reviewed planned tasks to ready.
An opt-in lease system now reserves assignment under the same write lock as approval checks.
Tasks remain ready/unowned execution records; the lease effective_owner describes reservations. Frozen Pydantic records reject extra fields and
coercion. Read-only query commands open SQLite in read-only mode and do not create missing state.

Migrations 1 and 2 establish coordination records and immutable source snapshots respectively.
Migration 5 adds fenced assignment reservations; public reads derive effective expiry/invalidation.
Migration 4 adds append-only task approval decisions with per-task generations.
Migration 3 adds snapshot-scoped plans with foreign-key references to the persisted snapshots.
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
the default adapter serves stdio only and loads existing state in read-only mode. Tests use the actual
SDK client against a real subprocess in current and legacy modes; no desktop host is certified yet.
Tool input limits and a 64 KiB structured-result budget bound responses. The default tool set has
no indexing, filesystem, shell, or mutation tools. See docs/mcp.md for configuration and boundaries.

## Draft planning

Planning validates a client/human proposal's structure, declared requirement coverage, code
references, and dependency graph against the selected snapshot. A valid report is not a semantic
feasibility verdict or approval. Plans include assumptions, unknowns, risks, deferred outcomes,
human decision questions, acceptance criteria, and validation procedures. No model provider is called.

The CLI submit-plan command, or an MCP server started with --enable-planning, may persist drafts.
The default server still has only six read-only investigation tools. Opt-in adds five planning tools,
three read-only assistance tools, three read-only policy/reservation/brief checks, and two MCP prompts;
submit_plan is the only write. It writes a plan, derived unowned tasks, and events in one transaction.
Stable plan IDs and a canonical submission hash make identical retries no-ops and reject conflicting
content. Exclusions, shared contracts, kinds, and complete criteria remain in the immutable proposal.

Plans are tied to a snapshot and exact specification hash. Reads are scoped to the operator-selected
snapshot. Freshness compares the latest indexed metadata for that repository and an optionally supplied
current specification, not live/unindexed work. Stale plans are flagged without overwriting their history.
Planned tasks can enter ready only through the operator approval gate; the legacy state-transition
API cannot bypass it or enter execution. The gate checks reviewed plan/spec hashes, indexed freshness,
feasibility, and linked decisions. Dependencies may await completion after consent but block assignment
preparation. Approval/revocation and task/event writes share a transaction; stale generations fail.
MCP can only read policy. Local CLI/database access is the operator boundary, not authenticated identity.
See task-approval.md for scope and the remaining lease/execution gates.
New Task fields have backward-compatible defaults.
See docs/planning.md for limits, migration, and the testable example.

## Agent assistance

Versioned guidance is exposed through both structured tools and standard MCP prompts so clients
can load a spec-to-plan workflow or an evidence/acceptance-review rubric. The client supplies model
reasoning. No provider inference, document instructions, or reported reviewer identity grants authority.

prepare_specification accepts client-provided text and returns exact text, a hash, and block IDs/
character offsets. The operator's prepare-spec CLI can additionally extract bounded DOCX body/table
text. It reads one byte snapshot, never extracts package entries or follows external relationships,
rejects unsafe/unsupported packages and tracked changes, and reports content omissions. It is not a
Word renderer or an OCR engine. File paths are not exposed in MCP document-intake arguments.

Optional requirement source_blocks are checked structurally against the exact plan specification.
They improve provenance without proving interpretation or complete requirement extraction. Legacy
plans without citations remain unassessed for source mapping. Normalized equality preserves identical
legacy retries after additive defaults, including near-budget records; hashes/progress are not rewritten.

assess_task checks client-reported evidence revisions, validation methods/steps, contradictions,
semantic review references, and plan/task blockers. It returns missing/rejected support and an advisory
recommendation; matching reports are labeled reported_support, not verified. It never opens artifacts,
fetches candidate commits, executes validations, persists assessments, or changes task status. The
client must obtain authorized source/artifacts and perform semantic/independent review. See
docs/agent-guidance.md for capabilities, limitations, and examples.

Select one execution runtime before adding a supervisor. No model provider is assumed.

The existing specification documents remain the behavioral contract. Review/release authority
is not conferred by role names or a task record. The project's license is AGPL-3.0-only;
see LICENSE and docs/licensing.md. No public package release is introduced by this implementation.

## Assignment reservations

Leases use one append-per-claim generation and a hashed bearer token; claims, renewals, releases,
and audit events are transactional. Approval revocation and task block/cancel invalidate open
reservations in the same transaction. Stale credentials cannot update a later generation. Effective
expiry is computed on reads. Planning-only MCP exposes inspection; a second explicit coordination
flag enables the three mutation tools. All grants retain execution_available false. Local clock
assumptions, database restore limits, and future external fencing enforcement are documented in
task-leases.md. Runtime execution remains unimplemented.

## Workspace preparation

A live lease can export a complete revision-bound task brief. The operator-only preparation CLI
registers a detached no-checkout worktree, fills its private index, and writes bounded regular
committed blobs without invoking checkout hooks/filters. Source edits and branch refs are preserved.
Coordination is checked under a write lock before/after preparation; expiry does not renew.
Filesystem attempts have explicit preparing/prepared/failed manifests and are retained on failure.
Files and SQLite are not one atomic transaction; a manifest is never runtime authority. Shared
Git metadata/configuration and prototype limits are documented in task-workspaces.md. No migration
beyond schema 5, execution adapter, or agent provider is added.
