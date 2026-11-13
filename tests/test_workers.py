import asyncio
import json
import os
import signal
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mcp import Client
from typer.testing import CliRunner

from worker_bees.approvals import Approvals
from worker_bees.cli import app
from worker_bees.mcp_server import create_server
from worker_bees.storage import Store
from worker_bees.workers import Workers


@pytest.fixture
def runtime(assignment, tmp_path):
    root, database, index, workspaces, request, approval, _ = assignment()
    workspace = tmp_path / "workspace"
    workspaces.prepare(request, workspace)
    workers = Workers(Store(database, writable=True), index)

    def profile(script, **options):
        path = tmp_path / "operator-profile.json"
        value = {
            "id": "test-worker",
            "argv": [sys.executable, "-c", script],
            "timeout_seconds": 5,
            "heartbeat_seconds": 1,
            "lease_ttl_seconds": 180,
            "terminate_grace_seconds": 1,
            "max_output_bytes": 1024,
        } | options
        path.write_text(json.dumps(value))
        return path

    return root, database, index, workers, workspace, request, approval, profile


def wait_started(workers, ids):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if ids:
            record = workers.get(ids[0])
            if record.child_pid:
                return record
        time.sleep(0.01)
    raise AssertionError("worker did not start")


def test_operator_worker_receives_brief_edits_checkout_and_never_completes_task(
    runtime, monkeypatch
):
    root, database, index, workers, workspace, request, _, profile = runtime
    monkeypatch.setenv("PRIVATE_UNSELECTED_SECRET", "do-not-inherit")
    path = profile(
        "import json,os,sys,pathlib,time; x=json.load(sys.stdin); "
        "assert 'token' not in x; assert 'PRIVATE_UNSELECTED_SECRET' not in os.environ; "
        "pathlib.Path('worker-note.txt').write_text(x['brief']['task']['id']); "
        "print(x['run_id']); print('diagnostic',file=sys.stderr); time.sleep(1.2)"
    )
    record = workers.run(request, workspace, path)
    assert record.status == "exited" and record.exit_code == 0 and record.leader_reaped
    assert not record.active and not record.acceptance_verified and not record.task_status_changed
    assert (workspace / "checkout" / "worker-note.txt").read_text() == "T1"
    assert not (root / "worker-note.txt").exists()
    assert Store(database).get_task(f"{request.plan_id}:{request.task_id}").status == "ready"
    directory = Path(record.directory)
    assert request.token not in (directory / "input.json").read_text()
    assert (directory / "stdout.log").read_text().strip() == record.id
    assert (directory / "stderr.log").read_text().strip() == "diagnostic"
    assert "worker_heartbeat" in [event.kind for event in Store(database).events()]
    assert workers.inspect(record.id).process_liveness_verified is False
    assert workers.inspect(record.id).needs_reconciliation is False
    with pytest.raises(ValueError, match="added/missing files"):
        workers.run(request, workspace, path)


@pytest.mark.parametrize(
    "script,status,exit_code",
    [
        ("raise SystemExit(7)", "failed", 7),
        ("import time;time.sleep(20)", "timed_out", None),
        ("print('x'*10000)", "output_limit", None),
    ],
)
def test_failures_timeout_and_output_bounds(runtime, script, status, exit_code):
    _, _, _, workers, workspace, request, _, profile = runtime
    record = workers.run(request, workspace, profile(script, timeout_seconds=1))
    assert record.status == status and not record.active and record.leader_reaped
    if exit_code is not None:
        assert record.exit_code == exit_code
    assert (Path(record.directory) / "stdout.log").stat().st_size <= 1024
    assert (Path(record.directory) / "stderr.log").stat().st_size <= 1024
    assert not record.acceptance_verified


@pytest.mark.parametrize("action,expected", [("cancel", "cancelled"), ("revoke", "lease_lost")])
def test_live_cancellation_and_approval_revocation_stop_owned_child(runtime, action, expected):
    _, database, index, workers, workspace, request, approval, profile = runtime
    ids = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            workers.run,
            request,
            workspace,
            profile("import time;time.sleep(20)"),
            lambda run: ids.append(run.id),
        )
        started = wait_started(workers, ids)
        if action == "cancel":
            pending = Workers(Store(database, writable=True), index).cancel(
                started.id, "Operator stop"
            )
            assert pending.cancel_requested
        else:
            Approvals(Store(database, writable=True), index).decide(
                approval.model_copy(
                    update={
                        "action": "revoke",
                        "expected_generation": 1,
                    }
                )
            )
        record = future.result(timeout=8)
    assert record.status == expected and not record.active and record.leader_reaped
    with pytest.raises(ProcessLookupError):
        os.kill(started.child_pid, 0)


def test_duplicate_run_is_rejected_even_with_valid_same_lease(runtime):
    _, _, _, workers, workspace, request, _, profile = runtime
    path = profile("import time;time.sleep(20)")
    ids = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(workers.run, request, workspace, path, lambda run: ids.append(run.id))
        started = wait_started(workers, ids)
        with pytest.raises(ValueError, match="active worker run"):
            workers.run(request, workspace, path)
        workers.cancel(started.id, "End duplicate-run test")
        assert future.result(timeout=8).status == "cancelled"


def test_cancel_before_launch_and_interrupt_are_audited(runtime, monkeypatch):
    _, _, _, workers, workspace, request, _, profile = runtime
    result = workers.run(
        request,
        workspace,
        profile("raise RuntimeError('must not start')"),
        lambda record: workers.cancel(record.id, "Cancel before launch"),
    )
    assert result.status == "cancelled" and result.child_pid is None

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(workers, "_monitor", interrupt)
    result = workers.run(request, workspace, profile("import time;time.sleep(20)"))
    assert result.status == "interrupted" and result.leader_reaped and not result.active


def test_sigterm_ignoring_child_is_killed_after_grace(runtime):
    _, _, _, workers, workspace, request, _, profile = runtime
    result = workers.run(
        request,
        workspace,
        profile(
            "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)",
            timeout_seconds=1,
        ),
    )
    assert result.status == "timed_out" and result.exit_code == -signal.SIGKILL
    assert result.leader_reaped


def test_invalid_profiles_and_dirty_or_wrong_workspaces_never_launch(runtime, tmp_path):
    _, database, _, workers, workspace, request, _, profile = runtime
    for changes in [
        {"argv": ["python"]},
        {"inherit_environment": ["GIT_DIR"]},
        {"heartbeat_seconds": 30, "lease_ttl_seconds": 30},
    ]:
        with pytest.raises(ValueError):
            workers.run(request, workspace, profile("print('no')", **changes))
    path = profile("print('no')")
    (workspace / "checkout" / "shop" / "pricing.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="content changed"):
        workers.run(request, workspace, path)
    with Store(database).connect() as connection:
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0


def test_readonly_scope_and_stale_status_do_not_signal_stored_pids(runtime):
    _, database, index, workers, workspace, request, _, profile = runtime
    record = workers.run(request, workspace, profile("print('ok')"))
    with pytest.raises(ValueError, match="write access"):
        Workers(Store(database), index).run(request, workspace, profile("print('no')"))
    with pytest.raises(ValueError, match="write access"):
        Workers(Store(database), index).cancel(record.id, "no")
    other = index.model_copy(update={"snapshot": index.snapshot.model_copy(update={"id": "other"})})
    with pytest.raises(ValueError, match="selected snapshot"):
        Workers(Store(database), other).get(record.id)
    # Simulate a stale active row: inspection reports uncertainty, cancellation only records intent.
    workers._update(record.id, {"active": True, "status": "running"})
    with workers.store.connect() as connection:
        old = workers.get(record.id).model_copy(update={"observed_at_ms": 0})
        connection.execute(
            "UPDATE worker_runs SET payload = ? WHERE id = ?", (old.model_dump_json(), record.id)
        )
    assert workers.inspect(record.id).needs_reconciliation
    pending = workers.cancel(record.id, "Reconciliation requested")
    assert pending.active and pending.status == "running"
    assert workers.inspect(record.id).needs_reconciliation


def test_execution_audit_failure_prevents_launch(runtime):
    _, _, _, workers, workspace, request, _, profile = runtime
    with workers.store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'event failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        workers.run(request, workspace, profile("print('must not start')"))
    assert not (workspace / "runs").exists()
    with workers.store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0


def test_v5_explicit_migration_preserves_state_and_cli_runs_worker(runtime):
    _, database, index, _, workspace, request, _, profile = runtime
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE worker_runs")
        connection.execute("PRAGMA user_version = 5")
    with pytest.raises(ValueError, match="migration"):
        Store(database)
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--db", str(database)]).exit_code == 0
    context = workspace.parent / "context.json"
    context.write_text(request.model_dump_json())
    result = runner.invoke(
        app,
        [
            "run-worker",
            str(context),
            str(workspace),
            str(profile("print('ok')")),
            "--db",
            str(database),
        ],
    )
    assert result.exit_code == 0, result.output
    record = Store(database).events()[-1]
    assert record.kind == "worker_finished"
    inspected = runner.invoke(app, ["get-worker-run", record.entity_id, "--db", str(database)])
    assert inspected.exit_code == 0 and json.loads(inspected.output)["run"]["status"] == "exited"
    assert Store(database).load_index().snapshot.id == index.snapshot.id


def test_mcp_run_inspection_is_readonly_and_has_no_launch_or_cancel_tool(runtime):
    _, database, _, workers, workspace, request, _, profile = runtime
    run = workers.run(request, workspace, profile("print('ok')"))
    before = database.read_bytes()

    async def scenario():
        async with Client(
            create_server(database, enable_planning=True, enable_coordination=True)
        ) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 21
            names = {tool.name for tool in tools}
            assert "get_worker_run" in names
            assert not {"run_worker", "cancel_worker_run"}.intersection(names)
            result = await client.call_tool("get_worker_run", {"run_id": run.id})
            assert not result.is_error and result.structured_content["run"]["exit_code"] == 0
            assert not result.structured_content["run"]["acceptance_verified"]

    asyncio.run(scenario())
    assert database.read_bytes() == before


def test_started_event_failure_stops_spawned_child_and_records_outcome(runtime):
    _, _, _, workers, workspace, request, _, profile = runtime
    with workers.store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_started BEFORE INSERT ON events "
            "WHEN json_extract(NEW.payload, '$.kind') = 'worker_started' "
            "BEGIN SELECT RAISE(ABORT, 'start event failure'); END"
        )
    record = workers.run(request, workspace, profile("import time;time.sleep(20)"))
    assert record.status == "supervision_failed" and record.leader_reaped
    assert record.child_pid is not None and not record.active
    assert "start event failure" in record.error


def test_operator_sigterm_reaches_foreground_supervisor_cleanup(runtime):
    import subprocess

    _, database, _, workers, workspace, request, _, profile = runtime
    context = workspace.parent / "signal-context.json"
    context.write_text(request.model_dump_json())
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "worker_bees",
            "run-worker",
            str(context),
            str(workspace),
            str(profile("import time;time.sleep(20)")),
            "--db",
            str(database),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = process.stderr.readline()
        assert line.startswith("Authorized worker run"), line
        started = wait_started(workers, [line.split()[-1]])
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=8)
        assert process.returncode == 2, error
        assert json.loads(output)["status"] == "interrupted"
        assert workers.get(started.id).leader_reaped
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_child_pid_remains_waitable_until_process_group_cleanup(runtime, monkeypatch):
    import worker_bees.workers as module

    _, _, _, workers, workspace, request, _, profile = runtime
    original = module.stop_group
    observed = []

    def checked(process, grace):
        state = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        assert state is not None and state.si_pid == process.pid
        observed.append(process.pid)
        return original(process, grace)

    monkeypatch.setattr(module, "stop_group", checked)
    record = workers.run(request, workspace, profile("print('done')"))
    assert record.status == "exited" and record.leader_reaped
    assert observed == [record.child_pid]
