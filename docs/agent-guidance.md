# Agent guidance, document intake, and acceptance review

Worker Bees supplies instructions and checks to help an AI client interpret a specification and
review task evidence. The client still performs language understanding and semantic reasoning;
the server does not call a model or independently execute or verify test artifacts.

## Discover the workflows

Start the server with --enable-planning. It exposes 15 tools and two standard MCP prompts:

| Interface | Name | Purpose |
|---|---|---|
| Prompt | plan_feature | Interpret a document, investigate code, and prepare a grounded draft |
| Prompt | review_task_satisfaction | Examine acceptance evidence and make a semantic review |
| Tool | get_workflow_guidance | Get the same versioned workflow as structured instructions |
| Tool | prepare_specification | Prepare client-extracted text with a hash and source-block IDs |
| Tool | assess_task | Evaluate the completeness/consistency of reported evidence and reviews |
| Tool | check_task_policy | Read operator consent and assignment preparation blockers |

The new tools are read-only. submit_plan remains the only database write tool. The default
server, without --enable-planning, retains the six investigation tools and has no planning prompts.
MCP hosts may present prompts as user-selectable templates; they do not automatically run them.
For clients without a prompt UI, call get_workflow_guidance with spec_to_plan or assess_task.
Server instructions also direct the client to load the relevant workflow before proceeding.

```bash
uv run worker-bees workflow-guidance spec_to_plan --db .worker-bees/project.db
uv run worker-bees workflow-guidance assess_task --db .worker-bees/project.db
```

The guidance covers normative and non-functional requirements, negation/exceptions, ambiguity,
source citations, real code entry paths, task scope/dependencies, acceptance conditions,
instrument quality, revision-bound evidence, independent review, and authority boundaries.

## Word, text, and Markdown intake

The operator can import a local file through the CLI:

```bash
uv run worker-bees prepare-spec feature.docx
uv run worker-bees prepare-spec feature.md
```

The JSON result contains normalized text, its text_sha256, original source_sha256, format, and
source blocks with IDs, previews, and character offsets. Copy the returned text exactly into
PlanProposal.specification. A client that already read an uploaded Word file can instead send
its extracted text to prepare_specification. No MCP tool accepts a local document path or opens
an arbitrary file. The server does not receive an attachment automatically just because it runs.

DOCX intake supports main-body paragraphs, Heading1–Heading9 style IDs, and basic table rows.
It does not render Word layouts or preserve all document semantics. Images/OCR, headers/footers,
comments, footnotes, text boxes, nested tables, content controls, and embedded objects are omitted.
Formatting/field evaluation is not preserved, and hidden run text may be included. Resolve tracked
changes before importing; the parser rejects them instead of choosing a version. Compare the
extraction with the original before treating it as a complete specification.

The parser reads the exact bytes whose hash it reports. It never extracts ZIP entries to disk,
executes macros/fields, or follows links/relationships. It rejects macro-containing packages,
DTD/entity declarations, duplicate ZIP names, unsupported XML encodings, excessive entries,
oversized XML/packages, and excessive XML node counts. Legacy .doc and PDF are not supported.
Limits: 2 MB compressed input, 8 MB uncompressed package metadata, 1 MB body XML, 256 package entries,
20,000 XML nodes, 32,000 text characters, and 256 source blocks. Existing MCP output budgets apply.

## Requirement citations

A requirement may cite the exact prepared specification's blocks:

```json
{
  "id": "R1",
  "outcome": "Negative prices raise ValueError.",
  "source_blocks": ["B0002"]
}
```

Block IDs are bound to the exact text hash. Regenerate and review them after editing the text.
Offsets are Python Unicode character indexes, not UTF-8 byte or JavaScript UTF-16 offsets.
validate_plan rejects unknown or duplicate block references. It returns source_block_coverage,
unmapped_source_blocks, and a source_mapping label. Links establish provenance, not interpretation
or proof that all requirements were extracted. An unmapped paragraph may be useful context.

The field is optional for existing proposals. With no citations, source mapping is explicitly
not_supplied and unassessed. Existing immutable plan retries remain idempotent after the additive
schema field; task progress and historical hashes are preserved. This source-mapping field does
not require a migration; the separate [task approval](task-approval.md) feature requires schema 4.

## Task satisfaction assistance

Read get_plan and get_task, then use the review workflow. The agent should inspect the actual
candidate diff/source and validation artifacts through its authorized capabilities. It then
submits an AssessmentRequest to assess_task, or uses the CLI:

```bash
uv run worker-bees assess-task assessment-request.json --db .worker-bees/project.db --snapshot ORIGINAL_SNAPSHOT_ID
```

[examples/task-assessment.json](../examples/task-assessment.json) is an empty report template;
replace its revision placeholder with the full candidate commit SHA. The retained spot-check demo
generates a concrete empty request that intentionally returns needs_evidence.

Each evidence record names an id, criterion_id, zero-based validation_index, full revision,
method, outcome (pass/fail/inconclusive/not_run), artifact locator, and summary. Each semantic
review names a criterion, a supported/not_met/uncertain judgment, rationale, and evidence IDs.
These are reported claims. Matching validation_index and method does not prove the procedure ran.

For each acceptance criterion, the helper checks required validation steps, revision/method
consistency, contradictory outcomes, and evidence references supporting the client's semantic
review. A wrong revision is rejected as support. A fail cannot be hidden by another pass;
inconclusive/unrun records remain unresolved. Passing reports without a semantic review are
insufficient. The output records accepted/rejected evidence IDs, missing steps, review rationale,
reassessment reasons, and one recommendation:

| Recommendation | Meaning |
|---|---|
| blocked | Reported failure, negative review, blocked task, changed specification, or relevant blocking decision |
| needs_evidence | Required validation steps lack matching reported passing evidence |
| needs_review | Semantic support is missing/uncertain, reports are unresolved, or source context changed |
| ready_for_independent_review | Declared checks and client-supplied review are complete enough to review independently |

None means satisfied, approved, or completed. The basis is always reported_evidence_and_client_review,
and task_status_changed is always false. The helper does not open artifact paths, fetch candidate
commits/URLs, run tests, authenticate a reviewer, or verify that a claimed pass occurred. Its job is
to expose gaps and guide the agent's judgment, not certify it. Retain the input/output JSON for an audit
trail; assessments are not persisted in the task database. CLI exit code 0 means a report was computed,
not that a task passed; inspect recommendation. Invalid data/operational errors exit nonzero.

## Spot-check

```bash
uv run python scripts/spot_check.py --planning --keep .worker-bees/guidance-demo
uv run worker-bees prepare-spec .worker-bees/guidance-demo/feature-spec.md
uv run worker-bees workflow-guidance spec_to_plan --db .worker-bees/guidance-demo/state.db
uv run worker-bees assess-task .worker-bees/guidance-demo/assessment-request.json --db .worker-bees/guidance-demo/state.db
```

Use a fresh --keep directory. The demo checks prompt discovery, guidance, source blocks, and an
evidence-free assessment that requests evidence while leaving tasks unchanged. It does not modify
the sample application or create fictional proof that the feature was implemented.
