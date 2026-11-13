# Operator-configured local worker supervision

Worker Bees can run one operator-selected local process for a prepared, actively leased task.
This is a provider-neutral bridge: a Python adapter can read a JSON task brief, call a chosen AI
provider, and work in the prepared checkout. No model SDK, provider account, default AI command,
or automatic deployment is included. The bundled demonstration is deterministic Python, not an AI
implementation or proof that the sample feature is satisfied.

## Try the full workflow

```bash
uv sync --locked
uv run python scripts/spot_check.py --workers --keep .worker-bees/worker-demo
```

Use a fresh directory. --workers includes investigation, planning, approval, lease, and workspace
checks, then runs a fixed demo Python command that reads its brief and prints the task ID. It checks
that process exit zero does not complete or accept the task. The demo retains a profile, input JSON,
and bounded stdout/stderr logs under task-workspace/runs/RUN_ID. Bearer request files are private
and temporary. At the end, the sample lease is released and approval revoked.

Existing databases need explicit migration 6:

```bash
uv run worker-bees init --db .worker-bees/state.db
```

Migration 6 adds durable worker_runs and a unique active-run-per-task constraint. Existing tasks,
plans, source snapshots, approvals, and leases remain intact. No new package dependencies are added.

## Execution authority and profiles

Assignment approval and a prepared workspace alone do not authorize process execution. The operator
explicitly invokes run-worker with a local profile and live lease context. That invocation authorizes
only the configured command for this run and records operator_local_worker_invocation authority,
profile identity/hash, workspace, original revision, owner, lease identity/generation, timestamps,
process IDs, and outcomes. The profile is read once and its actual arguments are not templated from
the specification, proposed validation procedures, or worker output.

A profile is a JSON object authored and selected by the operator, outside the workspace bundle:

```json
{
  "id": "my-python-adapter",
  "argv": ["/absolute/path/to/python", "/absolute/path/to/adapter.py"],
  "inherit_environment": [],
  "timeout_seconds": 300,
  "heartbeat_seconds": 10,
  "lease_ttl_seconds": 120,
  "terminate_grace_seconds": 2,
  "max_output_bytes": 65536
}
```

Replace executable/script paths with your trusted adapter. argv is a fixed vector of 1–32 nonempty
arguments, up to 4,096 characters each; the executable must be absolute. The supervisor uses
shell=False with no shell expansion. An operator can deliberately select an interpreter or shell;
this is not a language/command sandbox. The profile file is capped at 16 KiB and unknown fields are
rejected. The full exact profile-file hash is retained, but argv and environment values are not
copied into public run records.

The child starts in checkout/ with a fresh environment containing PATH=os.defpath, LANG=C.UTF-8,
PYTHONUNBUFFERED=1, and WORKER_BEES_RUN_ID. Only explicitly named inherit_environment variables are
forwarded. GIT_* and WORKER_BEES_* overrides are reserved. If your adapter needs a provider secret,
select its environment variable explicitly; do not put secrets in argv. Worker output can still
expose secrets, so treat input/log files as private task artifacts. No bearer lease token is passed
in stdin, argv, or the supervisor-generated environment.

Timeout is 1–3,600 seconds. Heartbeats are 1–30 seconds and at most one third of the selected
30–900-second lease TTL. Termination grace is 1–10 seconds. Each output stream has a separate
1,024–1,048,576-byte capture cap, default 65,536 bytes. These are supervision/capture bounds, not CPU,
memory, network, or filesystem isolation.

## Run a prepared task

Use the same private LeaseContext JSON as [workspace preparation](task-workspaces.md), without a
TTL field. The workspace must still match that lease and original snapshot. Before launching, the
supervisor checks its manifest, Git HEAD/common metadata, exact tracked file bytes, and absence of
added/missing files or symlinks. A changed checkout requires a fresh prepared workspace. It does
not invoke git status or checkout/clean filters for this verification.

```bash
uv run worker-bees run-worker context.json /path/to/task-workspace operator-profile.json --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

The foreground command prints the run ID to stderr as soon as authorization is recorded. The final
JSON on stdout reports process outcomes. CLI exit 0 means status exited with process exit code 0;
other run outcomes exit 2, while invalid requests/preflight failures exit 1. This is never a task
acceptance gate. Task.status remains ready and task_status_changed/acceptance_verified remain false.
Use the existing independent evidence/acceptance-review workflow after inspecting actual changes.

A new session/process group is started after a second authorization check. Approval/lease writes
are serialized during launch, but a database transaction and OS process creation are not atomic.
A child may start before a launch audit/commit failure; the supervisor then attempts to stop it and
records the failure. No automatic retry, commit, branch creation, merge, release, or lease release
occurs. The lease is renewed while supervision is active and remains subject to its ordinary TTL
when the run ends.

## Worker input and outputs

The worker reads one JSON object to EOF on stdin:

```json
{
  "protocol_version": 1,
  "run_id": "...",
  "profile_id": "my-python-adapter",
  "profile_sha256": "...",
  "authority": "operator_local_worker_invocation",
  "brief": {"task": {"id": "T1"}, "...": "complete current task brief"}
}
```

The actual brief contains the original spec/revision, scope/constraints, requirements, acceptance
criteria, proposed validation procedures, and current reservation context. The brief's own
execution_available remains false: execution consent is the separate operator-selected invocation,
not authority conferred by document content or an old exported artifact.

stdout/stderr are opaque captured data. The supervisor never interprets worker text as commands,
approval, validation success, or task lifecycle changes. A Python adapter can use the brief to form
provider instructions and report evidence through a later authorized workflow. Exit zero only means
the selected process exited successfully.

## Inspect and cancel

```bash
uv run worker-bees get-worker-run RUN_ID --db .worker-bees/state.db --snapshot SNAPSHOT_ID
uv run worker-bees cancel-worker-run RUN_ID 'Operator cancellation reason' --db .worker-bees/state.db --snapshot SNAPSHOT_ID
```

Planning-enabled MCP exposes only read-only get_worker_run. It has no launch, profile selection,
log-file access, or cancellation tool. Planning now exposes 18 tools/two prompts, coordination 21,
and default investigation remains six tools. Queries are pinned to the run's original snapshot.

Cancellation records intent and returns cancel_requested; it does not acknowledge that a process
has stopped. A live foreground supervisor observes that request, approval revocation, lease/context
loss, timeout, or output overflow and signals only the process group it created. It sends SIGTERM,
then SIGKILL if needed, and reaps its direct child. It observes exit without reaping until
group cleanup finishes, keeping the owned child PID reserved during signalling. Ctrl-C and normal CLI SIGTERM follow the
interruption cleanup path. Stored PIDs are never adopted or signalled by another CLI process.

Run states include starting, running, exited, failed, cancelled, timed_out, lease_lost, output_limit,
interrupted, supervision_failed, and stop_unconfirmed. active remains true if direct-child cleanup
cannot be confirmed, preventing another managed run for that task. Concurrent duplicate runs are
also rejected. Run inspection includes last-observed age and needs_reconciliation for stale active
records; it always says process_liveness_verified=false. PID/heartbeat history is not an independent
OS liveness proof. Cancellation does not refresh the supervisor heartbeat or clear a stale record.

## Local prototype boundaries

This runtime currently requires POSIX process groups and waitid/WNOWAIT, and is tested on Linux. It is a foreground
supervisor, not a persistent daemon or isolation boundary. The configured process runs with the
operator's normal host privileges. It can access files/network permitted to that account, spawn
processes, or escape its process group. Group signalling is best effort and leader_reaped describes
only the direct child. It does not prove every descendant or external effect stopped.

SQLite/OS/filesystem actions are not one atomic transaction. If the supervisor is killed abruptly,
the host crashes, or final state cannot be saved, a worker may remain and an active row may be stale.
The system fails closed for additional managed launches; it does not automatically restart, adopt,
kill stored PIDs, or clear that row. The operator must reconcile actual processes/artifacts before
manual state repair. Safe automated reconciliation/recovery is a separate next slice.

Lease observation/renewal happens on the supervision loop, not before every action the child takes.
Clock assumptions and possible database-lock delays still apply. A local worker can therefore make
an effect between revocation/expiry and detection. Stronger fencing of external effects requires a
cooperative capability adapter or isolated execution service. Per-tool action policy, container
isolation, provider integration, recovery/restart, and evidence-backed completion remain future work.
