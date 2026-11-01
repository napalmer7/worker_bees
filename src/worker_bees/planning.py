"""Structural plan validation and durable draft tracking, without model execution."""

import hashlib
import json

from worker_bees.models import Index, Task
from worker_bees.planning_models import (
    PlanProposal,
    PlanReceipt,
    PlanTaskList,
    PlanTaskView,
    PlanValidation,
    PlanView,
    StoredPlan,
)
from worker_bees.queries import Investigation
from worker_bees.storage import Store

MAX_PROPOSAL_BYTES = 49_152
MAX_PLAN_RESULT_BYTES = 65_536
UNVERIFIED = (
    "Feasibility, task scope, and acceptance quality are supplied proposals, not independently "
    "verified conclusions. Structural validity is not approval or permission to execute."
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Planning:
    def __init__(self, store: Store, index: Index):
        self.store = store
        self.index = index
        self.investigation = Investigation(index)

    def validate(self, proposal: PlanProposal) -> PlanValidation:
        errors: list[str] = []
        warnings = [UNVERIFIED]
        if len(proposal.model_dump_json().encode("utf-8")) > MAX_PROPOSAL_BYTES:
            errors.append("proposal exceeds the 48 KiB serialized input budget")
        if proposal.snapshot_id != self.index.snapshot.id:
            errors.append("proposal snapshot does not match the selected snapshot")
        if self.index.snapshot.status == "partial":
            warnings.append("Repository index is partial; inspect its issues and coverage.")
        for label, ids in [
            ("requirement", [r.id for r in proposal.requirements]),
            ("task", [t.id for t in proposal.tasks]),
            ("decision", [d.id for d in proposal.decisions]),
        ]:
            if len(ids) != len(set(ids)):
                errors.append(f"duplicate {label} IDs")
        requirements = {r.id: r for r in proposal.requirements}
        tasks = {t.id: t for t in proposal.tasks}
        coverage: dict[str, list[str]] = {key: [] for key in requirements}
        for task in proposal.tasks:
            if len(task.requirement_ids) != len(set(task.requirement_ids)):
                errors.append(f"task {task.id}: duplicate requirement links")
            for requirement_id in task.requirement_ids:
                if requirement_id not in requirements:
                    errors.append(f"task {task.id}: unknown requirement {requirement_id}")
                else:
                    coverage[requirement_id].append(task.id)
                    if requirements[requirement_id].disposition == "deferred":
                        errors.append(
                            f"task {task.id}: deferred requirement {requirement_id} has work"
                        )
            if len(task.depends_on) != len(set(task.depends_on)):
                errors.append(f"task {task.id}: duplicate dependencies")
            for dependency in task.depends_on:
                if dependency not in tasks:
                    errors.append(f"task {task.id}: unknown dependency {dependency}")
                if dependency == task.id:
                    errors.append(f"task {task.id}: cannot depend on itself")
            criteria = [criterion.id for criterion in task.acceptance_criteria]
            if len(criteria) != len(set(criteria)):
                errors.append(f"task {task.id}: duplicate acceptance criterion IDs")
            for symbol in task.affected_symbols:
                self._check_symbol(symbol, f"task {task.id}", errors)
        decision_coverage: set[str] = set()
        for decision in proposal.decisions:
            for requirement_id in decision.requirement_ids:
                if requirement_id not in requirements:
                    errors.append(f"decision {decision.id}: unknown requirement {requirement_id}")
                decision_coverage.add(requirement_id)
        for requirement in proposal.requirements:
            if requirement.disposition == "planned" and not coverage[requirement.id]:
                errors.append(f"requirement {requirement.id}: no task covers the planned outcome")
            if requirement.disposition != "planned" and not requirement.rationale:
                errors.append(f"requirement {requirement.id}: disposition requires a rationale")
            if (
                requirement.disposition == "needs_decision"
                and requirement.id not in decision_coverage
            ):
                errors.append(f"requirement {requirement.id}: missing durable decision question")
        feasibility = proposal.feasibility
        for reference in feasibility.code_references:
            self._check_symbol(reference.symbol, "feasibility", errors)
        if feasibility.verdict == "feasible":
            if not feasibility.code_references:
                errors.append("feasible assessment requires at least one indexed code reference")
            if feasibility.unknowns or any(d.blocking for d in proposal.decisions):
                errors.append(
                    "feasible assessment cannot have unresolved unknowns or blocking decisions"
                )
        if not feasibility.code_references:
            warnings.append("No repository code evidence is cited for the proposed assessment.")
        if feasibility.verdict in {"blocked", "unknown", "needs_investigation"}:
            warnings.append(f"Proposed feasibility remains {feasibility.verdict}.")
        if proposal.decisions:
            warnings.append(
                "Decision questions are unresolved; recording them does not approve them."
            )
        if not proposal.tasks:
            warnings.append("This proposal creates no executable work items.")
        ordered: list[str] = []
        pending = {key: set(task.depends_on).intersection(tasks) for key, task in tasks.items()}
        while pending:
            ready = [key for key, dependencies in pending.items() if not dependencies]
            if not ready:
                errors.append("task dependency graph contains a cycle")
                ordered = []
                break
            ordered.extend(ready)
            for key in ready:
                del pending[key]
            for dependencies in pending.values():
                dependencies.difference_update(ready)
        return PlanValidation(
            valid=not errors,
            specification_sha256=digest(proposal.specification),
            ordered_task_ids=ordered,
            requirement_coverage=coverage,
            errors=errors,
            warnings=warnings,
        )

    def _check_symbol(self, identifier: str, context: str, errors: list[str]) -> None:
        try:
            self.investigation.symbol(identifier)
        except ValueError:
            errors.append(
                f"{context}: code reference {identifier!r} is absent or ambiguous in snapshot"
            )

    def submit(self, proposal: PlanProposal, *, actor: str) -> PlanReceipt:
        if not self.store.writable:
            raise ValueError("planning submission requires explicit write access")
        validation = self.validate(proposal)
        if not validation.valid:
            raise ValueError("invalid plan: " + "; ".join(validation.errors))
        canonical = json.dumps(
            proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        record = StoredPlan(
            proposal=proposal,
            snapshot=self.index.snapshot,
            specification_sha256=validation.specification_sha256,
            submission_sha256=digest(canonical),
        )
        tasks = [
            Task(
                id=f"{proposal.id}:{task.id}",
                plan_id=proposal.id,
                local_id=task.id,
                objective=task.outcome,
                base_revision=self.index.snapshot.commit,
                requirement_ids=task.requirement_ids,
                depends_on=[f"{proposal.id}:{dependency}" for dependency in task.depends_on],
                affected_symbols=task.affected_symbols,
                acceptance_criteria=[f"{c.id}: {c.outcome}" for c in task.acceptance_criteria],
                validation_needs=[
                    f"{c.id} [{step.method}] {step.procedure}; expected: {step.expected_result}"
                    for c in task.acceptance_criteria
                    for step in c.validation
                ],
            )
            for task in proposal.tasks
        ]
        if len(record.model_dump_json().encode("utf-8")) > MAX_PLAN_RESULT_BYTES - 2048:
            raise ValueError("stored plan and snapshot metadata exceed the readable result budget")
        if any(
            len(task.model_dump_json().encode("utf-8")) > MAX_PLAN_RESULT_BYTES - 2048
            for task in tasks
        ):
            raise ValueError("derived task exceeds the readable result budget")
        created = self.store.save_plan(record, tasks, actor=actor)
        return PlanReceipt(
            plan_id=proposal.id,
            snapshot_id=self.index.snapshot.id,
            base_revision=self.index.snapshot.commit,
            specification_sha256=validation.specification_sha256,
            task_ids=[task.id for task in tasks],
            created=created,
            warnings=validation.warnings,
        )

    def get(self, plan_id: str, specification: str | None = None) -> PlanView:
        record = self.store.get_plan(plan_id, self.index.snapshot.id)
        latest = self.store.latest_snapshot(record.snapshot.repository)
        excluded = {"id", "created_at"}
        reasons: list[str] = []
        if latest.model_dump(exclude=excluded) != record.snapshot.model_dump(exclude=excluded):
            reasons.append("repository_index_changed")
        if specification is not None and digest(specification) != record.specification_sha256:
            reasons.append("specification_changed")
        warnings = self.validate(record.proposal).warnings + [
            "Freshness compares the latest indexed snapshot for this repository; unindexed changes "
            "and an unspecified replacement specification are not checked."
        ]
        if reasons:
            warnings.append("Plan needs reassessment; the stored proposal has not been updated.")
        return PlanView(
            plan=record, needs_reassessment=bool(reasons), reasons=reasons, warnings=warnings
        )

    def list_tasks(self, plan_id: str, limit: int = 20, offset: int = 0) -> PlanTaskList:
        if not 1 <= limit <= 100 or not 0 <= offset <= 100:
            raise ValueError("task limit must be 1–100 and offset 0–100")
        view = self.get(plan_id)
        entries = view.plan.proposal.tasks
        tasks = [
            self.store.get_task(f"{plan_id}:{task.id}") for task in entries[offset : offset + limit]
        ]
        return PlanTaskList(
            plan_id=plan_id,
            snapshot_id=self.index.snapshot.id,
            tasks=tasks,
            total=len(entries),
            offset=offset,
            truncated=offset + len(tasks) < len(entries),
            needs_reassessment=view.needs_reassessment,
            reasons=view.reasons,
            warnings=view.warnings,
        )

    def get_task(self, plan_id: str, task_id: str) -> PlanTaskView:
        view = self.get(plan_id)
        if task_id not in {task.id for task in view.plan.proposal.tasks}:
            raise ValueError("task not found in this plan")
        return PlanTaskView(
            plan_id=plan_id,
            snapshot_id=self.index.snapshot.id,
            task=self.store.get_task(f"{plan_id}:{task_id}"),
            needs_reassessment=view.needs_reassessment,
            reasons=view.reasons,
            warnings=view.warnings,
        )
