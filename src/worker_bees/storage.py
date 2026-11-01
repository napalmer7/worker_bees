"""Transactional local state with explicit schema migrations."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from worker_bees.models import Event, Index, Snapshot, Task, TaskStatus, Transition
from worker_bees.planning_models import StoredPlan

MIGRATIONS = (
    (
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
        "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)",
    ),
    (
        "CREATE TABLE snapshots (sequence INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, "
        "payload TEXT NOT NULL)",
    ),
    (
        "CREATE TABLE plans (id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL "
        "REFERENCES snapshots(id), payload TEXT NOT NULL)",
        "CREATE INDEX plans_snapshot ON plans(snapshot_id)",
    ),
)


def migrate(connection: sqlite3.Connection) -> None:
    """Serialize migration discovery and application; rollback the whole upgrade on error."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise ValueError("database was created by a newer Worker Bees version")
        for index in range(version, len(MIGRATIONS)):
            for statement in MIGRATIONS[index]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {index + 1}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


class Store:
    def __init__(self, path: Path, *, writable: bool = False):
        self.path = path.expanduser().resolve()
        self.writable = writable
        if writable:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as connection:
                migrate(connection)
        else:
            with self.connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version != len(MIGRATIONS):
                    raise ValueError("database needs migration; run 'worker-bees init'")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        mode = "rwc" if self.writable else "ro"
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def event(
        connection: sqlite3.Connection, entity: str, kind: str, actor: str = "local_cli"
    ) -> None:
        event = Event(entity_id=entity, kind=kind, actor=actor)
        connection.execute("INSERT INTO events(payload) VALUES (?)", (event.model_dump_json(),))

    def save_index(self, index: Index) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO snapshots(id, payload) VALUES (?, ?)",
                (index.snapshot.id, index.model_dump_json()),
            )
            self.event(connection, index.snapshot.id, "snapshot_published")

    def load_index(self, snapshot_id: str | None = None) -> Index:
        with self.connect() as connection:
            if snapshot_id is None:
                row = connection.execute(
                    "SELECT payload FROM snapshots ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT payload FROM snapshots WHERE id = ?", (snapshot_id,)
                ).fetchone()
        if row is None:
            raise ValueError("snapshot not found; index a repository first")
        return Index.model_validate_json(row[0])

    def create_task(self, task: Task) -> None:
        if task.status != "draft" or task.owner is not None:
            raise ValueError("new tasks must be unowned drafts")
        if task.plan_id is not None:
            raise ValueError("planned tasks must be created atomically with their plan")
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tasks(id, payload) VALUES (?, ?)", (task.id, task.model_dump_json())
            )
            self.event(connection, task.id, "task_created")

    def get_task(self, task_id: str) -> Task:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise ValueError("task not found")
        return Task.model_validate_json(row[0])

    def transition_task(self, task_id: str, expected: TaskStatus, after: TaskStatus) -> Task:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise ValueError("task not found")
            task = Task.model_validate_json(row[0])
            if task.status != expected:
                raise ValueError("stale task state; reread before updating")
            if task.plan_id is not None and after not in {"blocked", "cancelled"}:
                raise ValueError("planned tasks require future approval and execution gates")
            Transition(before=expected, after=after)
            updated = Task.model_validate(task.model_dump() | {"status": after})
            connection.execute(
                "UPDATE tasks SET payload = ? WHERE id = ?", (updated.model_dump_json(), task_id)
            )
            self.event(connection, task_id, f"task_{after}")
        return updated

    def events(self) -> list[Event]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload FROM events ORDER BY sequence").fetchall()
        return [Event.model_validate_json(row[0]) for row in rows]

    def latest_snapshot(self, repository: str) -> Snapshot:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT json_extract(payload, '$.snapshot') FROM snapshots "
                "WHERE json_extract(payload, '$.snapshot.repository') = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (repository,),
            ).fetchone()
        if row is None:
            raise ValueError("repository has no indexed snapshot")
        return Snapshot.model_validate_json(row[0])

    def get_plan(self, plan_id: str, snapshot_id: str) -> StoredPlan:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM plans WHERE id = ? AND snapshot_id = ?",
                (plan_id, snapshot_id),
            ).fetchone()
        if row is None:
            raise ValueError("plan not found in the selected snapshot")
        return StoredPlan.model_validate_json(row[0])

    def save_plan(self, plan: StoredPlan, tasks: list[Task], *, actor: str) -> bool:
        """Commit plan, derived draft tasks, and events together; identical retries are no-ops."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM plans WHERE id = ?",
                (plan.proposal.id,),
            ).fetchone()
            if row is not None:
                existing = StoredPlan.model_validate_json(row[0])
                if existing.submission_sha256 != plan.submission_sha256:
                    raise ValueError("plan ID already exists with different content; use a new ID")
                return False
            connection.execute(
                "INSERT INTO plans(id, snapshot_id, payload) VALUES (?, ?, ?)",
                (plan.proposal.id, plan.snapshot.id, plan.model_dump_json()),
            )
            for task in tasks:
                if task.status != "draft" or task.owner is not None:
                    raise ValueError("planned tasks must be unowned drafts")
                if task.plan_id != plan.proposal.id:
                    raise ValueError("task does not belong to the proposed plan")
                connection.execute(
                    "INSERT INTO tasks(id, payload) VALUES (?, ?)",
                    (task.id, task.model_dump_json()),
                )
                self.event(connection, task.id, "task_created", actor)
            self.event(connection, plan.proposal.id, "plan_submitted", actor)
        return True
