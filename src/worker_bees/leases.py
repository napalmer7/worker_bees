"""Atomic, fenced assignment reservations. These operations never start execution."""

import hashlib
import secrets
import sqlite3
from collections.abc import Callable

from worker_bees.approval_models import TaskPolicy
from worker_bees.approvals import Approvals
from worker_bees.lease_models import (
    ClaimRequest,
    LeaseCredential,
    LeaseGrant,
    LeaseRecord,
    LeaseView,
    RenewRequest,
)
from worker_bees.lease_state import lease_view, load_lease, now_ms, update_lease
from worker_bees.models import Index
from worker_bees.storage import Store

WARNINGS = [
    "A lease reserves assignment only; no process, repository edit, acceptance, "
    "or release is authorized.",
    "Keep the bearer token private. Public reads omit tokens; local database access is trusted.",
    "Fencing protects these SQLite operations. A future runtime must enforce it "
    "for external effects.",
    "Expiry uses the local wall clock. Clock jumps, database rollback, and distributed clocks "
    "require runtime controls before execution is enabled.",
]


class Leases:
    def __init__(self, store: Store, index: Index, *, clock: Callable[[], int] = now_ms):
        self.store = store
        self.approvals = Approvals(store, index)
        self.clock = clock

    def _context(
        self,
        connection: sqlite3.Connection,
        plan_id: str,
        task_id: str,
        specification_sha256: str | None = None,
    ) -> tuple[TaskPolicy, LeaseRecord | None]:
        plan, tasks, approval = self.approvals._load(connection, plan_id, task_id)
        policy = self.approvals._policy(
            connection,
            plan,
            task_id,
            tasks,
            approval,
            specification_sha256,
            include_reservation=False,
        )
        return policy, load_lease(connection, f"{plan_id}:{task_id}")

    @staticmethod
    def _view(policy: TaskPolicy, record: LeaseRecord | None, observed: int) -> LeaseView:
        reasons = list(policy.blockers)
        if record is not None and record.approval_generation != policy.approval_generation:
            reasons.append("approval_generation_changed")
        if record is not None and (
            record.snapshot_id != policy.snapshot_id
            or record.base_revision != policy.base_revision
            or record.submission_sha256 != policy.submission_sha256
            or record.specification_sha256 != policy.specification_sha256
        ):
            reasons.append("lease_context_mismatch")
        return lease_view(policy.plan_id, policy.task_id, record, observed, reasons)

    def get(self, plan_id: str, task_id: str) -> LeaseView:
        with self.store.connect() as connection:
            connection.execute("BEGIN")
            policy, record = self._context(connection, plan_id, task_id)
            return self._view(policy, record, self.clock())

    def _writable(self) -> None:
        if not self.store.writable:
            raise ValueError("lease mutation requires explicit write access")

    def claim(self, request: ClaimRequest) -> LeaseGrant:
        self._writable()
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            policy, previous = self._context(
                connection, request.plan_id, request.task_id, request.specification_sha256
            )
            observed = self.clock()
            generation = previous.generation if previous else 0
            if request.expected_lease_generation != generation:
                raise ValueError("stale lease generation; reread before claiming")
            if request.expected_approval_generation != policy.approval_generation:
                raise ValueError("stale approval generation; reread before claiming")
            if request.submission_sha256 != policy.submission_sha256:
                raise ValueError("reviewed submission hash does not match the plan")
            if policy.policy.decision != "allow":
                raise ValueError("claim denied: " + "; ".join(policy.blockers))
            view = self._view(policy, previous, observed)
            if view.state == "active":
                raise ValueError("task already reserved; wait for release or expiry")
            if "clock_moved_backwards" in view.reasons:
                raise ValueError("clock moved backwards; wait for the recorded heartbeat time")
            token = secrets.token_hex(32)
            record = LeaseRecord(
                plan_id=request.plan_id,
                task_id=request.task_id,
                owner=request.owner,
                generation=generation + 1,
                approval_generation=policy.approval_generation,
                snapshot_id=policy.snapshot_id,
                base_revision=policy.base_revision,
                submission_sha256=policy.submission_sha256,
                specification_sha256=policy.specification_sha256,
                token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                created_at_ms=observed,
                renewed_at_ms=observed,
                expires_at_ms=observed + request.ttl_seconds * 1000,
            )
            connection.execute(
                "INSERT INTO task_leases(task_id, generation, payload) VALUES (?, ?, ?)",
                (
                    f"{request.plan_id}:{request.task_id}",
                    record.generation,
                    record.model_dump_json(),
                ),
            )
            self.store.event(
                connection, f"{request.plan_id}:{request.task_id}", "lease_claimed", request.owner
            )
            return LeaseGrant(
                lease=self._view(policy, record, observed), token=token, warnings=WARNINGS
            )

    @staticmethod
    def _credentials(record: LeaseRecord | None, request: LeaseCredential) -> LeaseRecord:
        if record is None or (
            record.id != request.lease_id
            or record.generation != request.generation
            or record.owner != request.owner
            or not secrets.compare_digest(
                record.token_sha256, hashlib.sha256(request.token.encode()).hexdigest()
            )
        ):
            raise ValueError("invalid or stale lease credentials")
        return record

    def renew(self, request: RenewRequest) -> LeaseView:
        self._writable()
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            policy, previous = self._context(
                connection, request.plan_id, request.task_id, request.specification_sha256
            )
            record = self._credentials(previous, request)
            observed = self.clock()
            view = self._view(policy, record, observed)
            if view.state != "active":
                raise ValueError(
                    "lease cannot renew: " + view.state + "; " + "; ".join(view.reasons)
                )
            updated = record.model_copy(
                update={
                    "renewed_at_ms": observed,
                    "expires_at_ms": max(
                        record.expires_at_ms, observed + request.ttl_seconds * 1000
                    ),
                }
            )
            update_lease(connection, updated)
            self.store.event(
                connection, f"{request.plan_id}:{request.task_id}", "lease_renewed", request.owner
            )
            return self._view(policy, updated, observed)

    def release(self, request: LeaseCredential) -> LeaseView:
        self._writable()
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Cleanup remains possible when source/spec/approval gates change or time expires.
            policy, previous = self._context(connection, request.plan_id, request.task_id)
            record = self._credentials(previous, request)
            if record.state == "open":
                record = record.model_copy(update={"state": "released"})
                update_lease(connection, record)
                self.store.event(
                    connection,
                    f"{request.plan_id}:{request.task_id}",
                    "lease_released",
                    request.owner,
                )
            return self._view(policy, record, self.clock())
