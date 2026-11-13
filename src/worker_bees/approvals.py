"""Transactional operator-only approvals and consistent, read-only assignment checks."""

import sqlite3
from typing import Literal

from worker_bees.approval_models import ApprovalReceipt, ApprovalRequest, TaskApproval, TaskPolicy
from worker_bees.lease_state import invalidate_lease, lease_view, load_lease, now_ms
from worker_bees.models import Index, PolicyDecision, Snapshot, Task
from worker_bees.planning_models import StoredPlan
from worker_bees.storage import Store

WARNINGS = [
    "Approval covers preparation for assignment only; execution, acceptance, merge, and release "
    "require separate gates.",
    "Freshness checks indexed metadata and the supplied specification hash, not live files or an "
    "unspecified replacement specification. The operator must confirm the current specification.",
    "Local database/CLI access is the operator boundary; actor labels are not authenticated "
    "identity.",
    "This report does not reserve ownership. Use an explicitly enabled atomic claim "
    "for assignment.",
]


class Approvals:
    def __init__(self, store: Store, index: Index):
        self.store = store
        self.index = index

    def _load(
        self, connection: sqlite3.Connection, plan_id: str, task_id: str
    ) -> tuple[StoredPlan, dict[str, Task], TaskApproval | None]:
        row = connection.execute(
            "SELECT payload FROM plans WHERE id = ? AND snapshot_id = ?",
            (plan_id, self.index.snapshot.id),
        ).fetchone()
        if row is None:
            raise ValueError("plan not found in the selected snapshot")
        plan = StoredPlan.model_validate_json(row[0])
        if task_id not in {task.id for task in plan.proposal.tasks}:
            raise ValueError("task not found in this plan")
        rows = connection.execute(
            "SELECT payload FROM tasks WHERE json_extract(payload, '$.plan_id') = ?", (plan_id,)
        ).fetchall()
        loaded = [Task.model_validate_json(row[0]) for row in rows]
        tasks = {task.id: task for task in loaded}
        if f"{plan_id}:{task_id}" not in tasks:
            raise ValueError("tracked task not found")
        row = connection.execute(
            "SELECT payload FROM task_approvals WHERE task_id = ? ORDER BY generation DESC LIMIT 1",
            (f"{plan_id}:{task_id}",),
        ).fetchone()
        approval = TaskApproval.model_validate_json(row[0]) if row else None
        return plan, tasks, approval

    def _policy(
        self,
        connection: sqlite3.Connection,
        plan: StoredPlan,
        task_id: str,
        tasks: dict[str, Task],
        approval: TaskApproval | None,
        specification_sha256: str | None,
        *,
        include_reservation: bool = True,
    ) -> TaskPolicy:
        proposal = plan.proposal
        task = tasks[f"{proposal.id}:{task_id}"]
        planned = next(entry for entry in proposal.tasks if entry.id == task_id)
        blockers: list[str] = []
        row = connection.execute(
            "SELECT json_extract(payload, '$.snapshot') FROM snapshots "
            "WHERE json_extract(payload, '$.snapshot.repository') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (plan.snapshot.repository,),
        ).fetchone()
        if row is None:
            raise ValueError("repository has no indexed snapshot")
        latest = Snapshot.model_validate_json(row[0])
        excluded = {"id", "created_at"}
        if latest.model_dump(exclude=excluded) != plan.snapshot.model_dump(exclude=excluded):
            blockers.append("repository_index_changed")
        if specification_sha256 is not None and specification_sha256 != plan.specification_sha256:
            blockers.append("specification_changed")
        if task.status not in {"draft", "ready"}:
            blockers.append(f"task_state:{task.status}")
        if task.owner is not None:
            blockers.append("task_already_owned")
        verdict = proposal.feasibility.verdict
        if verdict != "feasible" and not (
            planned.kind == "investigation" and verdict in {"unknown", "needs_investigation"}
        ):
            blockers.append(f"feasibility:{verdict}")
        for decision in proposal.decisions:
            if decision.blocking and set(decision.requirement_ids).intersection(
                task.requirement_ids
            ):
                blockers.append(f"unresolved_decision:{decision.id}")
        for requirement in proposal.requirements:
            if requirement.id in task.requirement_ids and requirement.disposition != "planned":
                blockers.append(f"requirement_disposition:{requirement.id}")
        for dependency_id in task.depends_on:
            dependency = tasks.get(dependency_id)
            if dependency is None or dependency.status != "completed":
                blockers.append(f"dependency_incomplete:{dependency_id}")
        valid_approval = approval is not None and approval.action == "approve"
        if valid_approval and approval is not None:
            if (
                approval.snapshot_id != plan.snapshot.id
                or approval.submission_sha256 != plan.submission_sha256
                or approval.specification_sha256 != plan.specification_sha256
                or approval.base_revision != plan.snapshot.commit
            ):
                valid_approval = False
                blockers.append("approval_context_mismatch")
        if not valid_approval:
            blockers.append("operator_approval_required")
        if valid_approval and task.status != "ready":
            blockers.append("task_not_ready")
        reservation = None
        if include_reservation:
            record = load_lease(connection, task.id)
            if record is not None:
                reasons = list(blockers)
                if record.approval_generation != (approval.generation if approval else 0):
                    reasons.append("approval_generation_changed")
                reservation = lease_view(proposal.id, task_id, record, now_ms(), reasons)
                if reservation.state == "active":
                    blockers.append("task_reserved")
        decision_value: Literal["allow", "require_approval", "deny"] = (
            "deny"
            if any(reason != "operator_approval_required" for reason in blockers)
            else ("require_approval" if blockers else "allow")
        )
        return TaskPolicy(
            plan_id=proposal.id,
            task_id=task_id,
            snapshot_id=plan.snapshot.id,
            base_revision=plan.snapshot.commit,
            submission_sha256=plan.submission_sha256,
            specification_sha256=plan.specification_sha256,
            task_status=task.status,
            approval_generation=approval.generation if approval else 0,
            approval=approval,
            policy=PolicyDecision(
                action="prepare_task_assignment",
                target=task.id,
                decision=decision_value,
                reason="; ".join(blockers)
                if blockers
                else "Operator approval and preparation gates satisfied.",
            ),
            blockers=blockers,
            warnings=WARNINGS,
            reservation=reservation,
        )

    def check(
        self, plan_id: str, task_id: str, specification_sha256: str | None = None
    ) -> TaskPolicy:
        with self.store.connect() as connection:
            # Read plan, tasks, approval, and latest index from one SQLite snapshot.
            connection.execute("BEGIN")
            plan, tasks, approval = self._load(connection, plan_id, task_id)
            return self._policy(connection, plan, task_id, tasks, approval, specification_sha256)

    def decide(self, request: ApprovalRequest) -> ApprovalReceipt:
        if not self.store.writable:
            raise ValueError("operator approval requires explicit write access")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan, tasks, previous = self._load(connection, request.plan_id, request.task_id)
            generation = previous.generation if previous else 0
            if request.expected_generation != generation:
                raise ValueError("stale approval generation; reread policy before updating")
            if request.submission_sha256 != plan.submission_sha256:
                raise ValueError("reviewed submission hash does not match the stored plan")
            if request.specification_sha256 != plan.specification_sha256:
                raise ValueError("reviewed specification hash does not match the stored plan")
            task = tasks[f"{request.plan_id}:{request.task_id}"]
            before = self._policy(
                connection, plan, request.task_id, tasks, previous, request.specification_sha256
            )
            if request.action == "approve":
                # Dependencies may remain pending: readiness is consent, not an execution claim.
                blocking = [
                    reason
                    for reason in before.blockers
                    if reason not in {"operator_approval_required", "task_not_ready"}
                    and not reason.startswith("dependency_incomplete:")
                ]
                if blocking:
                    raise ValueError("task cannot be approved: " + "; ".join(blocking))
                status = "ready"
            else:
                # Revocation remains available for stale plans and terminal/blocked tasks.
                status = "draft" if task.status == "ready" else task.status
            approval = TaskApproval(
                plan_id=request.plan_id,
                task_id=request.task_id,
                snapshot_id=plan.snapshot.id,
                base_revision=plan.snapshot.commit,
                submission_sha256=plan.submission_sha256,
                specification_sha256=plan.specification_sha256,
                generation=generation + 1,
                action=request.action,
                reason=request.reason,
            )
            updated = Task.model_validate(task.model_dump() | {"status": status})
            if request.action == "revoke" and invalidate_lease(connection, task.id):
                self.store.event(connection, task.id, "lease_invalidated", "local_operator")
            connection.execute(
                "INSERT INTO task_approvals(task_id, generation, payload) VALUES (?, ?, ?)",
                (task.id, approval.generation, approval.model_dump_json()),
            )
            connection.execute(
                "UPDATE tasks SET payload = ? WHERE id = ?", (updated.model_dump_json(), task.id)
            )
            self.store.event(
                connection,
                task.id,
                "task_approved" if request.action == "approve" else "task_approval_revoked",
                "local_operator",
            )
            tasks[task.id] = updated
            policy = self._policy(
                connection, plan, request.task_id, tasks, approval, request.specification_sha256
            )
            return ApprovalReceipt(approval=approval, task=updated, policy=policy)
