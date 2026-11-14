# OpenAI and Gemini proposal workers

The same supervised Python worker now supports OpenAI Responses and Google Gemini Developer API
generateContent. Both adapters produce an unverified change proposal, a reviewable unified diff,
and provider-reported usage. They do not apply changes, execute proposed validation, make commits,
approve work, or complete tasks. Provider/model choice comes only from the operator configuration.

## Spot-check both providers without credentials

```bash
uv sync --locked
uv run python scripts/spot_check.py --providers --keep .worker-bees/provider-demo
```

Use a new directory. This runs the existing investigation/planning/approval/workspace workflow,
then executes both provider adapters through the real local worker supervisor with httpx.MockTransport.
There are no live model requests, real API keys, or usage charges. The reports explicitly identify
network_mode: injected_transport, and their usage values and proposals are synthetic fixture data.
The pricing implementation stays unchanged. Reports, proposal.json, and proposal.diff are retained
under task-workspace/runs/RUN_ID/provider/ for inspection. The sample lease is released and approval
revoked after the demonstration; artifacts are historical records, not current authority.

Mocked tests establish request shape, parsing, and local guards. They do not establish live model
availability, output quality, account permissions, vendor compatibility, or semantic correctness.

## Configure a live provider

Create a provider config outside the prepared workspace bundle, using one of:

- [OpenAI config](../examples/openai-provider.json) and [worker profile](../examples/openai-worker-profile.json).
- [Gemini config](../examples/gemini-provider.json) and [worker profile](../examples/gemini-worker-profile.json).

Replace all executable/config paths and model placeholders. Select a text model supporting the
specified structured-output API. Model IDs are required; there is no implicit model, fallback,
provider switching, or automatic retry. This initial slice supports simple model IDs containing
letters, digits, dot, underscore, and hyphen. Fine-tuned/qualified IDs and custom endpoints are
not supported. Google authentication targets the Gemini Developer API, not Vertex AI.

```json
{
  "provider": "openai",
  "model": "REPLACE_WITH_SUPPORTED_MODEL_ID",
  "context_paths": ["shop/pricing.py"],
  "allowed_new_paths": ["tests/test_pricing.py"],
  "max_output_tokens": 4096,
  "timeout_seconds": 60,
  "max_context_bytes": 32768,
  "max_request_bytes": 131072
}
```

Set provider to gemini for Gemini. By default the worker reads OPENAI_API_KEY or GEMINI_API_KEY
respectively. Set api_key_env to select a different environment variable, and explicitly include
that name in the operator WorkerProfile.inherit_environment list. Do not put the key value in JSON,
argv, documents, or source. The adapter never writes authentication headers or key values into its
reports. Selected source/specification contents may themselves be sensitive; review them before
choosing to send them to a provider.

Example worker profile:

```json
{
  "id": "openai-proposal",
  "argv": ["/absolute/path/to/python", "-m", "worker_bees", "ai-worker", "/absolute/path/to/openai-provider.json"],
  "inherit_environment": ["OPENAI_API_KEY"],
  "timeout_seconds": 180,
  "max_output_bytes": 65536
}
```

For Gemini use its config path and GEMINI_API_KEY. A custom key environment variable must be selected
in both configurations. The profile command is fixed by the operator; the model cannot choose the
executable, endpoints, credential variable, context paths, or artifact directory.

After approving/reserving/preparing a real task, use the existing private lease-context file:

```bash
uv run worker-bees run-worker context.json /path/to/task-workspace openai-worker-profile.json --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

This is an explicit live invocation and can incur provider charges. The adapter makes at most one
provider HTTP request per worker run and never automatically retries rate limits, timeouts, schema
failures, refusals, or truncated output. An interrupted/failed call may still be processed/billed by
the provider. The supervisor stopping the local process does not cancel a remote provider job.
Usage that is missing is unknown, not zero; request/token bounds are not a dollar spending cap.

## Dry-run preflight

Append --dry-run to the ai-worker argv in a separate operator profile to validate selected input
and compute request/context hashes without looking up API credentials or sending a request:

```json
["/absolute/path/to/python", "-m", "worker_bees", "ai-worker", "/absolute/path/to/openai-provider.json", "--dry-run"]
```

Launch that profile through run-worker in the same way. The report says dry_run with zero attempted
requests and unknown usage. It does not test credentials, model availability, or API compatibility.
A later live attempt uses a new operator-authorized worker run. Existing provider artifact directories
are never overwritten or resumed, preventing a blind repeat of an uncertain request.

## Source context and bounds

The operator explicitly selects 1–8 context files and up to 8 permitted new file paths. Source is
read as committed Git blobs at the brief's original commit/tree, checked against the current
checkout, and hashed with SHA-256 over exact UTF-8 bytes. Selected source must be complete UTF-8
text without NUL; binary/non-UTF-8 files, symlinks, dirty selected files, existing addition paths,
and noncommitted context are rejected before any provider request. No silent truncation occurs.
After receiving a response, selected context is checked again before proposal artifacts are saved.
Unselected files, live unindexed changes elsewhere, and replacement external specifications are
not independently investigated by this adapter.

Paths are relative ASCII names using letters, digits, underscore, dot, hyphen, and slash. Absolute,
traversal, noncanonical, Git metadata, duplicate/case-duplicate config paths, and unselected model
change paths are rejected. Only modify and add proposals are supported; no rename/delete operation
or model-driven file discovery is exposed. Review semantic exclusions/shared contracts separately.

Provider config files are capped at 16 KiB, worker input at 256 KiB, context at a configurable
1–64 KiB, and the serialized provider request body at 4–192 KiB. Defaults are 32 KiB context and
128 KiB request body. max_output_tokens is 256–16,384. HTTP phase timeout is 5–120 seconds; use the
supervisor timeout for total local run control. Response bodies are capped at 256 KiB, extracted
model text at 96 KiB, each replacement string at 32,768 characters, and all replacement UTF-8 bytes
at 64 KiB. The latter constraints are enforced locally even when provider schemas omit them.
These are request/capture bounds, not OS/network isolation or exact input token counts.

Requests use fixed HTTPS origins, TLS verification, no redirects, no environment proxies, and no
ambient client credentials. There are no model tools, shell execution, browsing, or provider-hosted
code execution in the request. The first OpenAI adapter uses store:false for response retrieval
storage; that setting is not a promise about every provider retention policy.

## Proposal contract and review

Both providers receive the same small JSON Schema, with required closed objects. Local strict
Pydantic/business checks remain authoritative. A propose_changes result must include:

- A summary and 1–8 complete replacement/addition file contents with rationale.
- An exact original SHA-256 for modifications, or an empty base hash for explicitly allowed additions.
- Known requirement links covering this task's declared requirements.
- Proposed validation procedures linked to every acceptance criterion, including expected outcomes.
- Explicit assumptions and uncertainties.

Unknown/duplicate links, invalid base hashes, unselected paths, no-op replacements, extra fields,
missing declared coverage, and malformed/oversized output are rejected. If information is missing,
the model can return needs_context with no changes and explicit uncertainties. A matching schema
and declared coverage are not proof of a correct, feasible, secure, or complete implementation.

The JSON proposal preserves complete file text. proposal.diff is for review and can be checked
against the original revision; it is never applied by the adapter. Validation procedures remain
untrusted data. The next integration slice must authorize patch application and validation commands,
bind actual results to a candidate revision, and perform independent acceptance review.

## Artifacts and status

report.json begins with preparing and is updated using atomic file replacement. It binds provider,
requested/resolved model, run/task/plan/snapshot/revision, configuration/specification/submission
hashes, selected context hashes, serialized request hash, latency, request attempts, response identity,
and token usage. It never saves raw HTTP response/error bodies, headers, or private thinking text.
If a process is interrupted, a preparing report with attempted request count may remain; it does not
establish whether a remote call completed or was billed.

Completed report states are dry_run, proposed, needs_context, or failed. Proposals are labelled
unverified_model_proposal and retain acceptance_verified:false/task_status_changed:false.
OpenAI incomplete/error/refusal/tool output and Gemini prompt blocks, non-STOP finishes, unexpected
parts, or multiple/missing candidates are not accepted as successful proposals. Sanitized error
codes/http status are retained, including usage metadata when the provider supplies it.

Usage is provider-reported, with unknown fields left null. OpenAI input/output/total, cached input,
and reasoning counters are mapped separately. Gemini prompt/candidate/total, cached content, and
thought counters are mapped separately; their accounting semantics differ and should not be assumed
to be identical billing units. No prices, cost estimates, or account budget enforcement are inferred.
The adapter CLI exits 2 for failed reports and 0 for other completed reports; process success alone
still does not accept or complete the task.

## References and compatibility

Request formats were checked against official documentation on November 14, 2026:

- [OpenAI Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create)
  and [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
- [Gemini generateContent](https://aistudio.google.com/api-reference/generate-content/generate-content),
  [generation/output types](https://aistudio.google.com/api-reference/generate-content), and
  [structured output guidance](https://aistudio.google.com/docs/structured-output).

The Gemini request uses generationConfig.responseFormat.text with APPLICATION_JSON and schema,
as specified in the current generation API reference. No live model call was made while implementing
this slice. Operator-selected live smoke tests are separate from the default offline test suite.
No database migration beyond runtime schema 6 and no new MCP execution tool are introduced.
