"""Transaction-local lease persistence and effective state shared by approvals and claims."""

import sqlite3
import time
from typing import Literal

from worker_bees.lease_models import LeaseRecord, LeaseView


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def load_lease(connection: sqlite3.Connection, task_id: str) -> LeaseRecord | None:
    row = connection.execute(
        "SELECT payload FROM task_leases WHERE task_id = ? ORDER BY generation DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return LeaseRecord.model_validate_json(row[0]) if row else None


def update_lease(connection: sqlite3.Connection, record: LeaseRecord) -> None:
    connection.execute(
        "UPDATE task_leases SET payload = ? WHERE task_id = ? AND generation = ?",
        (record.model_dump_json(), f"{record.plan_id}:{record.task_id}", record.generation),
    )


def invalidate_lease(connection: sqlite3.Connection, task_id: str) -> bool:
    record = load_lease(connection, task_id)
    if record is None or record.state != "open":
        return False
    update_lease(connection, record.model_copy(update={"state": "invalidated"}))
    return True


def lease_view(
    plan_id: str,
    task_id: str,
    record: LeaseRecord | None,
    observed: int,
    reasons: list[str] | None = None,
) -> LeaseView:
    blockers = list(reasons or [])
    if record is None:
        return LeaseView(
            plan_id=plan_id,
            task_id=task_id,
            lease_id=None,
            generation=0,
            approval_generation=None,
            owner=None,
            effective_owner=None,
            state="absent",
            observed_at_ms=observed,
            created_at_ms=None,
            renewed_at_ms=None,
            expires_at_ms=None,
            reasons=[],
        )
    state: Literal["active", "expired", "released", "invalidated"]
    if record.state == "open":
        if observed < record.renewed_at_ms:
            blockers.append("clock_moved_backwards")
        if blockers:
            state = "invalidated"
        elif record.expires_at_ms <= observed:
            state = "expired"
        else:
            state = "active"
    else:
        state = record.state
    return LeaseView(
        plan_id=plan_id,
        task_id=task_id,
        lease_id=record.id,
        generation=record.generation,
        approval_generation=record.approval_generation,
        owner=record.owner,
        effective_owner=record.owner if state == "active" else None,
        state=state,
        observed_at_ms=observed,
        created_at_ms=record.created_at_ms,
        renewed_at_ms=record.renewed_at_ms,
        expires_at_ms=record.expires_at_ms,
        reasons=blockers,
    )
