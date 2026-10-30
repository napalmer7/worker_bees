"""Contracts for unapproved feature plans supplied by a human or client model."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from worker_bees.models import Record, Snapshot, Task, timestamp

Key = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000, pattern=r"\S")]
Specification = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=32_000, pattern=r"\S")
]
SymbolReference = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]


class Requirement(Record):
    id: Key
    outcome: Text
    disposition: Literal["planned", "deferred", "needs_decision"] = "planned"
    rationale: Text | None = None


class ValidationStep(Record):
    method: Literal["automated", "manual", "inspection"]
    procedure: Text
    expected_result: Text


class AcceptanceCriterion(Record):
    id: Key
    outcome: Text
    validation: list[ValidationStep] = Field(min_length=1, max_length=10)


class PlannedTask(Record):
    id: Key
    outcome: Text
    kind: Literal["implementation", "investigation", "validation"] = "implementation"
    requirement_ids: list[Key] = Field(min_length=1, max_length=100)
    depends_on: list[Key] = Field(default_factory=list, max_length=100)
    affected_symbols: list[SymbolReference] = Field(default_factory=list, max_length=50)
    exclusions: list[Text] = Field(default_factory=list, max_length=20)
    shared_contracts: list[Text] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=20)


class CodeReference(Record):
    symbol: SymbolReference
    purpose: Text


class Feasibility(Record):
    verdict: Literal["feasible", "needs_investigation", "blocked", "unknown"]
    summary: Text
    code_references: list[CodeReference] = Field(default_factory=list, max_length=50)
    assumptions: list[Text] = Field(default_factory=list, max_length=20)
    unknowns: list[Text] = Field(default_factory=list, max_length=20)
    risks: list[Text] = Field(default_factory=list, max_length=20)


class DecisionNeeded(Record):
    id: Key
    question: Text
    requirement_ids: list[Key] = Field(min_length=1, max_length=100)
    blocking: bool = True


class PlanProposal(Record):
    id: Key
    title: Text
    snapshot_id: Key
    specification: Specification
    constraints: list[Text] = Field(default_factory=list, max_length=20)
    feasibility: Feasibility
    requirements: list[Requirement] = Field(min_length=1, max_length=100)
    tasks: list[PlannedTask] = Field(default_factory=list, max_length=100)
    decisions: list[DecisionNeeded] = Field(default_factory=list, max_length=50)


class PlanValidation(Record):
    valid: bool
    specification_sha256: str
    ordered_task_ids: list[str]
    requirement_coverage: dict[str, list[str]]
    errors: list[str]
    warnings: list[str]
    assessment: Literal["unverified_proposal"] = "unverified_proposal"


class StoredPlan(Record):
    proposal: PlanProposal
    snapshot: Snapshot
    specification_sha256: str
    submission_sha256: str
    created_at: str = Field(default_factory=timestamp)
    status: Literal["draft"] = "draft"


class PlanReceipt(Record):
    plan_id: str
    snapshot_id: str
    base_revision: str
    specification_sha256: str
    task_ids: list[str]
    created: bool
    status: Literal["draft"] = "draft"
    warnings: list[str]


class PlanView(Record):
    plan: StoredPlan
    needs_reassessment: bool
    reasons: list[str]
    warnings: list[str]


class PlanTaskList(Record):
    plan_id: str
    snapshot_id: str
    tasks: list[Task]
    total: int
    offset: int
    truncated: bool
    needs_reassessment: bool
    reasons: list[str]
    warnings: list[str]


class PlanTaskView(Record):
    plan_id: str
    snapshot_id: str
    task: Task
    needs_reassessment: bool
    reasons: list[str]
    warnings: list[str]
