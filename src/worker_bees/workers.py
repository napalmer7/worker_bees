"""Foreground POSIX worker supervision. No model provider or remote execution is assumed."""

import hashlib
import os
import selectors
import signal
import sqlite3
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from worker_bees.lease_models import LeaseContext, RenewRequest
from worker_bees.lease_state import now_ms
from worker_bees.leases import Leases
from worker_bees.models import Index
from worker_bees.storage import Store
from worker_bees.worker_models import RunStatus, WorkerProfile, WorkerRun, WorkerRunInspection
from worker_bees.workspaces import TaskBrief, WorkspaceManifest, Workspaces, entries, git


def bounded_file(path: Path, limit: int) -> bytes:
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("worker input/workspace content must be a regular file")
        value = source.read(limit + 1)
    if len(value) > limit:
        raise ValueError("worker configuration/manifest exceeds its input budget")
    return value


def read_profile(path: Path) -> tuple[WorkerProfile, str]:
    raw = bounded_file(path, 16_384)
    profile = WorkerProfile.model_validate_json(raw)
    if not Path(profile.argv[0]).is_absolute():
        raise ValueError("worker executable must be an absolute operator-selected path")
    if profile.heartbeat_seconds * 3 > profile.lease_ttl_seconds:
        raise ValueError("worker heartbeat must be at most one third of the lease TTL")
    if any(name.startswith(("GIT_", "WORKER_BEES_")) for name in profile.inherit_environment):
        raise ValueError("reserved Git/Worker Bees environment overrides cannot be inherited")
    return profile, hashlib.sha256(raw).hexdigest()


class Workers:
    def __init__(self, store: Store, index: Index):
        self.store = store
        self.index = index
        self.workspaces = Workspaces(store, index)
        self.leases = Leases(store, index)

    def _load(self, connection: sqlite3.Connection, run_id: str) -> WorkerRun:
        row = connection.execute(
            "SELECT payload FROM worker_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError("worker run not found")
        record = WorkerRun.model_validate_json(row[0])
        if record.snapshot_id != self.index.snapshot.id:
            raise ValueError("worker run not found in the selected snapshot")
        return record

    def get(self, run_id: str) -> WorkerRun:
        with self.store.connect() as connection:
            return self._load(connection, run_id)

    def inspect(self, run_id: str) -> WorkerRunInspection:
        record = self.get(run_id)
        age = now_ms() - record.observed_at_ms
        return WorkerRunInspection(
            run=record,
            last_observed_age_ms=max(0, age),
            needs_reconciliation=record.active
            and (age < 0 or age > max(5, record.heartbeat_seconds * 3) * 1000),
        )

    def _save(self, connection: sqlite3.Connection, record: WorkerRun) -> None:
        connection.execute(
            "UPDATE worker_runs SET active = ?, payload = ? WHERE id = ?",
            (int(record.active), record.model_dump_json(), record.id),
        )

    def _update(self, run_id: str, changes: dict[str, Any], event: str | None = None) -> WorkerRun:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self._load(connection, run_id)
            record = WorkerRun.model_validate(
                record.model_dump() | changes | {"observed_at_ms": now_ms()}
            )
            self._save(connection, record)
            if event:
                self.store.event(connection, record.id, event, "local_operator")
            return record

    def cancel(self, run_id: str, reason: str) -> WorkerRun:
        if not self.store.writable:
            raise ValueError("worker cancellation requires operator write access")
        if not reason.strip() or len(reason) > 2000:
            raise ValueError("cancellation reason must contain 1–2,000 characters")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self._load(connection, run_id)
            if record.active and not record.cancel_requested:
                record = record.model_copy(
                    update={
                        "cancel_requested": True,
                        "cancel_reason": reason,
                    }
                )
                self._save(connection, record)
                self.store.event(connection, run_id, "worker_cancel_requested", "local_operator")
            return record

    def _workspace(self, root: Path, brief: TaskBrief, request: LeaseContext) -> WorkspaceManifest:
        manifest = WorkspaceManifest.model_validate_json(
            bounded_file(root / "manifest.json", 16_384)
        )
        checkout = root / "checkout"
        if root.is_symlink() or checkout.is_symlink() or not checkout.is_dir():
            raise ValueError("worker workspace must be a prepared real directory")
        bounded_file(checkout / ".git", 16_384)
        if (
            manifest.status != "prepared"
            or manifest.plan_id != request.plan_id
            or manifest.task_id != request.task_id
            or manifest.lease_id != request.lease_id
            or manifest.lease_generation != request.generation
            or manifest.approval_generation != brief.reservation.approval_generation
            or manifest.snapshot_id != brief.snapshot.id
            or manifest.base_revision != brief.snapshot.commit
            or manifest.tree != brief.snapshot.tree
            or manifest.directory != str(root)
            or manifest.checkout != str(checkout)
            or manifest.brief_file != str(root / "task-brief.json")
        ):
            raise ValueError(
                "workspace manifest does not match the active lease and original snapshot"
            )
        repository = Path(brief.snapshot.repository)
        if git(checkout, "rev-parse", "HEAD").decode().strip() != brief.snapshot.commit:
            raise ValueError("workspace HEAD changed; prepare a fresh workspace")
        common = git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir") != common:
            raise ValueError("workspace uses a different Git object database")
        # Inspect bytes directly rather than invoking clean filters via git status.
        expected = entries(repository, brief.snapshot.commit)
        names = {entry[0] for entry in expected}
        actual: set[str] = set()
        for directory, folders, files in os.walk(checkout, followlinks=False):
            for name in folders:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("workspace contains a symlink")
            for name in files:
                path = Path(directory) / name
                relative = path.relative_to(checkout).as_posix()
                if relative == ".git":
                    continue
                if path.is_symlink():
                    raise ValueError("workspace contains a symlink")
                actual.add(relative)
                if len(actual) > 10_000:
                    raise ValueError("workspace file count exceeds the runtime budget")
        if actual != names:
            raise ValueError("workspace has added/missing files; prepare a fresh workspace")
        for name, _, blob, size in expected:
            if bounded_file(checkout / name, size) != git(repository, "cat-file", "blob", blob):
                raise ValueError("workspace content changed; prepare a fresh workspace")
        return manifest

    def run(
        self,
        request: LeaseContext,
        workspace: Path,
        profile_path: Path,
        on_authorized: Callable[[WorkerRun], None] | None = None,
    ) -> WorkerRun:
        if not self.store.writable:
            raise ValueError("worker execution requires explicit operator write access")
        if os.name != "posix" or not hasattr(os, "waitid") or not hasattr(os, "WNOWAIT"):
            raise ValueError(
                "local worker supervision requires POSIX process groups and waitid/WNOWAIT"
            )
        root = workspace.expanduser().absolute()
        if root.is_symlink():
            raise ValueError("worker workspace cannot be a symlink")
        root = root.resolve(strict=True)
        profile_path = profile_path.resolve(strict=True)
        if profile_path.is_relative_to(root):
            raise ValueError("operator worker profile must be outside the workspace bundle")
        profile, profile_hash = read_profile(profile_path)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            brief = self.workspaces._brief(connection, request)
            manifest = self._workspace(root, brief, request)
            record = WorkerRun(
                plan_id=request.plan_id,
                task_id=request.task_id,
                snapshot_id=brief.snapshot.id,
                base_revision=brief.snapshot.commit,
                workspace_id=manifest.id,
                workspace=str(root),
                lease_id=request.lease_id,
                lease_generation=request.generation,
                owner=request.owner,
                profile_id=profile.id,
                profile_sha256=profile_hash,
                directory="",
                supervisor_pid=os.getpid(),
                started_at_ms=now_ms(),
                observed_at_ms=now_ms(),
                heartbeat_seconds=profile.heartbeat_seconds,
            )
            record = record.model_copy(update={"directory": str(root / "runs" / record.id)})
            try:
                connection.execute(
                    "INSERT INTO worker_runs(id, task_id, active, payload) VALUES (?, ?, 1, ?)",
                    (record.id, f"{request.plan_id}:{request.task_id}", record.model_dump_json()),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    "task already has an active worker run; inspect it before retrying"
                ) from error
            self.store.event(connection, record.id, "worker_run_authorized", "local_operator")
        return self._supervise(record, request, profile, brief, on_authorized)

    def _supervise(
        self,
        record: WorkerRun,
        request: LeaseContext,
        profile: WorkerProfile,
        brief: TaskBrief,
        on_authorized: Callable[[WorkerRun], None] | None,
    ) -> WorkerRun:
        process: subprocess.Popen[bytes] | None = None
        status: RunStatus = "supervision_failed"
        error: str | None = None
        counts = {"stdout": 0, "stderr": 0}
        reaped = False
        directory = Path(record.directory)
        try:
            if on_authorized:
                on_authorized(record)
            runs = directory.parent
            if runs.is_symlink():
                raise ValueError("worker run directory cannot be a symlink")
            runs.mkdir(mode=0o700, exist_ok=True)
            directory.mkdir(mode=0o700, exist_ok=False)
            renewed = RenewRequest(**request.model_dump(), ttl_seconds=profile.lease_ttl_seconds)
            try:
                self.leases.renew(renewed)
                brief = self.workspaces.brief(request)
            except (ValueError, sqlite3.Error):
                status = "lease_lost"
                raise
            envelope = {
                "protocol_version": 1,
                "run_id": record.id,
                "profile_id": profile.id,
                "profile_sha256": record.profile_sha256,
                "authority": "operator_local_worker_invocation",
                "brief": brief.model_dump(mode="json"),
            }
            import json

            (directory / "input.json").write_text(json.dumps(envelope), encoding="utf-8")
            environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"}
            environment.update(
                {
                    name: os.environ[name]
                    for name in profile.inherit_environment
                    if name in os.environ
                }
            )
            environment["WORKER_BEES_RUN_ID"] = record.id
            with (
                (directory / "input.json").open("rb") as stdin,
                (directory / "stdout.log").open("xb") as stdout,
                (directory / "stderr.log").open("xb") as stderr,
            ):
                with self.store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    fresh = self._load(connection, record.id)
                    try:
                        self.workspaces._brief(connection, request)
                    except (ValueError, sqlite3.Error):
                        status = "lease_lost"
                        raise
                    if fresh.cancel_requested:
                        status = "cancelled"
                        raise ValueError("worker cancelled before launch")
                    process = subprocess.Popen(
                        profile.argv,
                        cwd=Path(record.workspace) / "checkout",
                        env=environment,
                        stdin=stdin,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                    record = fresh.model_copy(
                        update={
                            "status": "running",
                            "child_pid": process.pid,
                            "observed_at_ms": now_ms(),
                        }
                    )
                    self._save(connection, record)
                    self.store.event(connection, record.id, "worker_started", "local_operator")
                status = self._monitor(
                    process,
                    record,
                    request,
                    renewed,
                    profile,
                    {"stdout": stdout, "stderr": stderr},
                    counts,
                )
        except KeyboardInterrupt:
            status, error = "interrupted", "Operator interrupted the supervisor."
        except Exception as failure:
            error = str(failure)
        finally:
            if process is not None:
                try:
                    reaped = stop_group(process, profile.terminate_grace_seconds)
                except OSError as failure:
                    error = "Could not signal/reap the owned worker process: " + str(failure)
                if not reaped:
                    status = "stop_unconfirmed"
            try:
                record = self._update(
                    record.id,
                    {
                        "status": status,
                        "active": process is not None and not reaped,
                        "finished_at_ms": now_ms(),
                        "child_pid": process.pid if process else None,
                        "exit_code": process.returncode if process else None,
                        "leader_reaped": reaped,
                        "stdout_bytes": counts["stdout"],
                        "stderr_bytes": counts["stderr"],
                        "error": error,
                    },
                    "worker_finished",
                )
            except Exception as failure:
                raise ValueError(
                    "Worker stopped/stop attempted, but final state could not be saved; "
                    f"inspect run {record.id}. No automatic retry is allowed."
                ) from failure
        return record

    def _monitor(
        self,
        process: subprocess.Popen[bytes],
        record: WorkerRun,
        request: LeaseContext,
        renewal: RenewRequest,
        profile: WorkerProfile,
        logs: dict[str, BinaryIO],
        counts: dict[str, int],
    ) -> RunStatus:
        started = time.monotonic()
        heartbeat = started + profile.heartbeat_seconds
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while True:
                current = self.get(record.id)
                if current.cancel_requested:
                    return "cancelled"
                try:
                    self.workspaces.brief(request)
                    if time.monotonic() >= heartbeat:
                        self.leases.renew(renewal)
                        self._update(record.id, {}, "worker_heartbeat")
                        heartbeat = time.monotonic() + profile.heartbeat_seconds
                except (ValueError, sqlite3.Error):
                    return "lease_lost"
                if time.monotonic() - started >= profile.timeout_seconds:
                    return "timed_out"
                for key, _ in selector.select(timeout=0.1):
                    content = os.read(key.fd, 65_536)
                    if not content:
                        selector.unregister(key.fileobj)
                        continue
                    name = key.data
                    remaining = profile.max_output_bytes - counts[name]
                    logs[name].write(content[:remaining])
                    counts[name] += min(len(content), remaining)
                    if len(content) > remaining:
                        return "output_limit"
                code = child_exit_code(process)
                if code is not None and not selector.get_map():
                    return "exited" if code == 0 else "failed"


def child_exit_code(process: subprocess.Popen[bytes]) -> int | None:
    """Observe the owned child without reaping it, keeping its PID reserved until group cleanup."""
    result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if result is None:
        return None
    return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status


def stop_group(process: subprocess.Popen[bytes], grace: int) -> bool:
    """Signal only the new session created for this live supervisor's child; never stored PIDs."""
    # Refuse signalling if ownership was lost through an external reaper/SIGCHLD policy.
    child_exit_code(process)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace
    while child_exit_code(process) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return False
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()
    return True
