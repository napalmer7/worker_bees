# Feature planning and tracked drafts

Worker Bees accepts a structured feature plan authored by a human or by a model using the
investigation tools. It validates and persists that proposal against a selected source snapshot.
It does not call a model provider, independently establish feasibility, implement the feature,
run the proposed validation commands, or grant approval to execute work.

Validation reports say unverified_proposal, stored plans and new tasks are drafts, and tasks
have no owner. The existing transition API cannot move planned tasks into execution/readiness
without the future approval and execution gates.

## Try the complete example

```bash
uv sync --locked
uv run python scripts/spot_check.py --planning --keep .worker-bees/planning-demo
uv run worker-bees get-plan negative-price-validation --db .worker-bees/planning-demo/state.db
uv run worker-bees list-tasks negative-price-validation --db .worker-bees/planning-demo/state.db
uv run worker-bees get-task negative-price-validation T2 --db .worker-bees/planning-demo/state.db
```

Choose a new --keep directory; existing directories are never overwritten. Omit --keep for a
temporary run that cleans itself up. The script runs the original investigation checks, then
checks plan validation, submission retries, two dependent draft tasks, specification freshness,
the opt-in MCP tools, and rejection of a cyclic plan.

The retained directory contains feature-plan.json and updated-spec.txt: a proposal and a changed
specification for comparison. The sample specification requests ValueError for negative prices
while preserving non-negative behavior. The sample application still clamps negative values;
this planning demonstration does not implement the feature or change its code.

## Proposal structure

[examples/feature-plan.json](../examples/feature-plan.json) is the complete sample. Replace its
snapshot placeholder and code references when preparing another proposal. Obtain the schema with:

```bash
uv run worker-bees plan-schema
```

| Field | Purpose |
|---|---|
| id | Stable, database-wide plan ID for retry protection |
| title, specification, constraints | Requested outcomes, exact source specification, and boundaries |
| snapshot_id | Indexed snapshot used to prepare the proposal |
| feasibility | Proposed verdict, summary, code references, assumptions, unknowns, and risks |
| requirements | IDs, outcomes, and planned/deferred/needs_decision dispositions |
| tasks | Local IDs, requirement links, dependencies, affected symbols, scope, and criteria |
| acceptance_criteria | Criterion IDs and observable outcomes, each with validation steps |
| validation | Automated/manual/inspection method, procedure, and expected result |
| decisions | Unresolved questions, affected requirements, and whether they block work |

Feasibility verdicts are feasible, needs_investigation, blocked, or unknown. A proposed feasible
assessment needs an indexed code reference and cannot include unresolved unknowns or blocking
decisions. These checks detect inconsistent structure; they do not establish whether the
reasoning or acceptance criteria are correct. A model or reviewer must assess that.

Every planned requirement needs a linked task. A deferred requirement needs a rationale and no
task claiming to implement it. A needs_decision requirement needs a rationale and a recorded
decision question. Tasks cannot reference missing requirements or dependencies. IDs must be
unique within their respective lists, and dependencies must be acyclic. Each criterion needs a
validation procedure and expected result. Code references must resolve within the selected
snapshot; intended new symbols belong in task outcomes rather than fabricated evidence.

## Use your repository

```bash
uv run worker-bees index /path/to/repository --db .worker-bees/project.db
uv run worker-bees status --db .worker-bees/project.db
uv run worker-bees validate-plan feature-plan.json --db .worker-bees/project.db --snapshot SNAPSHOT_ID
uv run worker-bees submit-plan feature-plan.json --db .worker-bees/project.db --snapshot SNAPSHOT_ID
uv run worker-bees get-plan PLAN_ID --db .worker-bees/project.db --snapshot SNAPSHOT_ID
uv run worker-bees list-tasks PLAN_ID --db .worker-bees/project.db --snapshot SNAPSHOT_ID
uv run worker-bees get-task PLAN_ID LOCAL_TASK_ID --db .worker-bees/project.db --snapshot SNAPSHOT_ID
```

Prepare feature-plan.json using the current schema and repository evidence first. validate-plan
does not write. A well-formed but invalid proposal returns a JSON report and exit code 2.
Malformed input or an operational failure exits with code 1. Successful validation/submission
exits with code 0. submit-plan refuses invalid proposals.

The receipt names the plan, snapshot, base revision, specification hash, and derived task IDs.
Persisting the plan, tasks, and events is one SQLite transaction. An identical retry with the
same plan ID returns created=false and preserves existing tasks and progress. A changed proposal
with the same ID is rejected. Submit revisions as new IDs; existing plans are immutable.
Tracked task IDs are PLAN_ID:LOCAL_TASK_ID; get-task takes the local ID as its second argument.
Full exclusions, shared contracts, task kinds, and decision questions remain in get-plan.

## Reassessment

get-plan reports needs_reassessment when the latest indexed snapshot for the same repository
has changed in source revision, parser, coverage, or indexing configuration. Reindexing the same
content/configuration does not make it stale solely because the snapshot ID changed. Another
repository's latest snapshot does not affect its freshness.

To compare an updated specification explicitly:

```bash
uv run worker-bees get-plan PLAN_ID --spec-file current-feature.txt --db .worker-bees/project.db --snapshot ORIGINAL_SNAPSHOT_ID
```

The specification hash uses exact UTF-8 text, including whitespace. MCP get_plan accepts the
current specification text instead of a file path. Freshness does not inspect live Git state,
detect unindexed edits, or discover a replacement specification unless supplied. Stale plans
are flagged, never silently rewritten.

Access is scoped to the selected snapshot. After reindexing, use the original --snapshot ID to
retrieve an older plan and its freshness flags. Inspect the new code and submit a newly assessed
proposal with a new plan ID and snapshot; changing an ID alone is not reassessment.

## Opt-in MCP planning

The default server still exposes only the six read-only investigation tools. To allow drafts:

```bash
uv run worker-bees serve --db .worker-bees/project.db --snapshot SNAPSHOT_ID --enable-planning
```

| Additional tool | Behavior |
|---|---|
| validate_plan(proposal) | Read-only structure, coverage, code-reference, and dependency checks |
| submit_plan(proposal) | Write an unapproved draft and tasks with retry protection |
| get_plan(plan_id, specification?) | Read a scoped plan and reassessment flags |
| list_tasks(plan_id, limit=20, offset=0) | Read paginated tracked task state and requirements |
| get_task(plan_id, task_id) | Read one task and its reassessment context |

submit_plan is annotated as a write operation. The flag enables database draft writes; it does
not permit repository edits, workers, approval, or execution of procedure text. Plan content
cannot set an owner, approval state, event actor, database path, or another source snapshot.
Event actors identify the interface, not a verified human approver. Ownership and human approval
remain later milestones.

## Upgrade and limits

Migration 3 adds plans while retaining existing snapshots, tasks, and events. MCP startup and
planning reads do not migrate automatically. Upgrade an existing database once:

```bash
uv run worker-bees init --db .worker-bees/project.db
```

Indexing also upgrades the database as an explicit write operation. Plan input is capped at
48 KiB of serialized JSON after defaults, with at most 100 requirements/tasks and 50 decisions.
The specification is at most 32,000 characters. Existing MCP result limits apply; submission
rejects records too large to read back. For large task lists, reduce limit and use offset.
The default investigation tool set, source authorization boundaries, and stdio transport remain.
