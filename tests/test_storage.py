import sqlite3

import pytest
from conftest import git
from pydantic import ValidationError

from worker_bees.analysis import build_index
from worker_bees.models import Evidence, Task
from worker_bees.queries import Investigation
from worker_bees.storage import MIGRATIONS, Store


def task():
    return Task(
        objective="Index Python",
        acceptance_criteria=["Definitions have source spans"],
        validation_needs=["Check curated fixture"],
        base_revision="abc",
    )


def test_tasks_events_and_indexes_survive_new_store_instance(tmp_path, make_repo):
    path = tmp_path / "state.db"
    store = Store(path, writable=True)
    work = task()
    store.create_task(work)
    store.transition_task(work.id, "draft", "ready")
    index = build_index(make_repo(fixture=True))
    store.save_index(index)
    reopened = Store(path)
    assert reopened.get_task(work.id).status == "ready"
    assert reopened.load_index().snapshot.id == index.snapshot.id
    assert [event.kind for event in reopened.events()] == [
        "task_created",
        "task_ready",
        "snapshot_published",
    ]
    with pytest.raises(sqlite3.OperationalError):
        reopened.create_task(task())


def test_failed_and_stale_transitions_do_not_write_state_or_events(tmp_path):
    store = Store(tmp_path / "state.db", writable=True)
    work = task()
    store.create_task(work)
    store.transition_task(work.id, "draft", "ready")
    with pytest.raises(ValueError, match="stale"):
        store.transition_task(work.id, "draft", "cancelled")
    with pytest.raises(ValidationError):
        store.transition_task(work.id, "ready", "completed")
    assert store.get_task(work.id).status == "ready"
    assert len(store.events()) == 2


def test_task_creation_cannot_skip_lifecycle(tmp_path):
    store = Store(tmp_path / "state.db", writable=True)
    with pytest.raises(ValueError):
        store.create_task(task().model_copy(update={"status": "completed"}))
    assert store.events() == []


def test_upgrade_from_previous_database_preserves_existing_task(tmp_path):
    path = tmp_path / "old.db"
    work = task()
    with sqlite3.connect(path) as connection:
        for statement in MIGRATIONS[0]:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.execute("INSERT INTO tasks VALUES (?, ?)", (work.id, work.model_dump_json()))
    with pytest.raises(ValueError, match="migration"):
        Store(path)
    store = Store(path, writable=True)
    assert store.get_task(work.id) == work
    with store.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_newer_database_is_not_silently_downgraded(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="newer"):
        Store(path, writable=True)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_state_and_event_rollback_together(tmp_path):
    store = Store(tmp_path / "state.db", writable=True)
    with store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'injected event failure'); END"
        )
    work = task()
    with pytest.raises(sqlite3.IntegrityError):
        store.create_task(work)
    with pytest.raises(ValueError, match="not found"):
        store.get_task(work.id)


def test_snapshots_do_not_leak_deleted_symbols_and_context_is_durable(tmp_path, make_repo):
    root = make_repo({"a.py": "def old():\n    pass\n"})
    store = Store(tmp_path / "state.db", writable=True)
    old = build_index(root)
    store.save_index(old)
    (root / "a.py").unlink()
    (root / "b.py").write_text("def new():\n    pass\n")
    git(root, "add", "--all")
    git(root, "commit", "-m", "replace source")
    store.save_index(build_index(root))
    assert Investigation(store.load_index()).find("old").total == 0
    previous = Investigation(store.load_index(old.snapshot.id))
    assert previous.context("a.old").source == "def old():\n    pass"
    assert previous.references("a.old").warnings


def test_contracts_reject_unknown_fields_coercion_and_empty_acceptance():
    with pytest.raises(ValidationError):
        Task.model_validate(task().model_dump() | {"hidden_authority": "merge"})
    with pytest.raises(ValidationError):
        Task.model_validate(task().model_dump() | {"acceptance_criteria": []})
    with pytest.raises(ValidationError):
        Evidence(
            task_id="1",
            criterion="c",
            artifact="a",
            revision="r",
            method="m",
            environment="local",
            outcome="pass",
            schema_version="1",
        )
