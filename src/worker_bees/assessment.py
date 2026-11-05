"""Advisory acceptance checks over reported evidence, never independent verification."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from worker_bees.models import Record
from worker_bees.planning import Planning
from worker_bees.planning_models import Key, Specification, Text

Revision = Annotated[
    str, StringConstraints(strict=True, pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
]


class ReportedEvidence(Record):
    id: Key
    criterion_id: Key
    validation_index: int = Field(ge=0, le=9)
    revision: Revision
    method: Literal["automated", "manual", "inspection"]
    outcome: Literal["pass", "fail", "inconclusive", "not_run"]
    artifact: Text
    summary: Text


class CriterionReview(Record):
    criterion_id: Key
    judgment: Literal["supported", "not_met", "uncertain"]
    rationale: Text
    evidence_ids: list[Key] = Field(default_factory=list, max_length=200)


class AssessmentRequest(Record):
    plan_id: Key
    task_id: Key
    implementation_revision: Revision
    evidence: list[ReportedEvidence] = Field(default_factory=list, max_length=200)
    reviews: list[CriterionReview] = Field(default_factory=list, max_length=20)
    current_specification: Specification | None = None


class CriterionAssessment(Record):
    criterion_id: str
    outcome: str
    state: Literal["reported_support", "not_met", "unknown"]
    missing_validation_indexes: list[int]
    rejected_evidence_ids: list[str]
    accepted_evidence_ids: list[str]
    review_judgment: str | None
    review_rationale: str | None
    review_evidence_ids: list[str]
    notes: list[str]


class TaskAssessment(Record):
    plan_id: str
    task_id: str
    snapshot_id: str
    implementation_revision: str
    recommendation: Literal[
        "blocked", "needs_evidence", "needs_review", "ready_for_independent_review"
    ]
    criteria: list[CriterionAssessment]
    blockers: list[str]
    plan_reassessment_reasons: list[str]
    basis: Literal["reported_evidence_and_client_review"] = "reported_evidence_and_client_review"
    task_status_changed: Literal[False] = False
    warnings: list[str]


def assess_task(planner: Planning, request: AssessmentRequest) -> TaskAssessment:
    if len(request.model_dump_json().encode()) > 49_152:
        raise ValueError("assessment request exceeds the 48 KiB input budget")
    view = planner.get(request.plan_id, request.current_specification)
    task_view = planner.get_task(request.plan_id, request.task_id)
    task = next(t for t in view.plan.proposal.tasks if t.id == request.task_id)
    criteria = {criterion.id: criterion for criterion in task.acceptance_criteria}
    evidence = {item.id: item for item in request.evidence}
    if len(evidence) != len(request.evidence):
        raise ValueError("duplicate evidence IDs")
    reviews = {review.criterion_id: review for review in request.reviews}
    if len(reviews) != len(request.reviews):
        raise ValueError("duplicate criterion reviews")
    for item in request.evidence:
        if item.criterion_id not in criteria:
            raise ValueError("evidence cites an unknown criterion")
        if item.validation_index >= len(criteria[item.criterion_id].validation):
            raise ValueError("evidence cites an unknown validation step")
    for submitted_review in request.reviews:
        if submitted_review.criterion_id not in criteria:
            raise ValueError("review cites an unknown criterion")
        if len(submitted_review.evidence_ids) != len(set(submitted_review.evidence_ids)):
            raise ValueError("duplicate evidence references in review")
        if any(identifier not in evidence for identifier in submitted_review.evidence_ids):
            raise ValueError("review cites unknown evidence")
    results: list[CriterionAssessment] = []
    for criterion in task.acceptance_criteria:
        matching = [item for item in request.evidence if item.criterion_id == criterion.id]
        accepted: list[ReportedEvidence] = []
        rejected: list[str] = []
        notes: list[str] = []
        for item in matching:
            expected_method = criterion.validation[item.validation_index].method
            if item.revision.lower() != request.implementation_revision.lower():
                rejected.append(item.id)
                notes.append(f"{item.id}: evidence is for a different revision")
            elif item.method != expected_method:
                rejected.append(item.id)
                notes.append(f"{item.id}: method does not match the required validation step")
            else:
                accepted.append(item)
        passed_steps = {item.validation_index for item in accepted if item.outcome == "pass"}
        missing = sorted(set(range(len(criterion.validation))) - passed_steps)
        failed = any(item.outcome == "fail" for item in accepted)
        unresolved = any(item.outcome in {"inconclusive", "not_run"} for item in accepted)
        review = reviews.get(criterion.id)
        semantic_support = False
        if review is None:
            notes.append("No semantic criterion review was supplied")
        else:
            cited = [evidence[identifier] for identifier in review.evidence_ids]
            valid_ids = {item.id for item in accepted if item.outcome == "pass"}
            cited_steps = {item.validation_index for item in cited if item.id in valid_ids}
            semantic_support = (
                review.judgment == "supported"
                and not missing
                and not unresolved
                and bool(cited)
                and all(item.id in valid_ids for item in cited)
                and cited_steps == set(range(len(criterion.validation)))
            )
            if not semantic_support:
                notes.append(
                    "Semantic review is uncertain, negative, or lacks matching passing evidence"
                )
        if failed:
            notes.append("Reported failure is unresolved, even if another report says pass")
        if unresolved:
            notes.append("Inconclusive or unrun validation is still present in the evidence set")
        state: Literal["reported_support", "not_met", "unknown"]
        if failed or (review is not None and review.judgment == "not_met"):
            state = "not_met"
        elif semantic_support:
            state = "reported_support"
        else:
            state = "unknown"
        results.append(
            CriterionAssessment(
                criterion_id=criterion.id,
                outcome=criterion.outcome,
                state=state,
                missing_validation_indexes=missing,
                rejected_evidence_ids=rejected,
                accepted_evidence_ids=[item.id for item in accepted],
                review_judgment=review.judgment if review else None,
                review_rationale=review.rationale if review else None,
                review_evidence_ids=review.evidence_ids if review else [],
                notes=notes,
            )
        )
    blockers: list[str] = []
    if "specification_changed" in view.reasons:
        blockers.append("specification_changed")
    if task_view.task.status in {"blocked", "cancelled", "failed"}:
        blockers.append(f"task_state:{task_view.task.status}")
    blockers.extend(
        f"criterion_not_met:{result.criterion_id}"
        for result in results
        if result.state == "not_met"
    )
    blockers.extend(
        f"unresolved_decision:{decision.id}"
        for decision in view.plan.proposal.decisions
        if decision.blocking and set(decision.requirement_ids).intersection(task.requirement_ids)
    )
    recommendation: Literal[
        "blocked", "needs_evidence", "needs_review", "ready_for_independent_review"
    ]
    if blockers:
        recommendation = "blocked"
    elif any(result.missing_validation_indexes for result in results):
        recommendation = "needs_evidence"
    elif view.needs_reassessment or any(result.state != "reported_support" for result in results):
        recommendation = "needs_review"
    else:
        recommendation = "ready_for_independent_review"
    return TaskAssessment(
        plan_id=request.plan_id,
        task_id=request.task_id,
        snapshot_id=planner.index.snapshot.id,
        implementation_revision=request.implementation_revision,
        recommendation=recommendation,
        criteria=results,
        blockers=blockers,
        plan_reassessment_reasons=view.reasons,
        warnings=view.warnings
        + [
            "All test outcomes and semantic judgments are client-supplied claims. Artifacts and "
            "candidate source were not opened, fetched, or executed by this helper.",
            "Matching revisions and declared validation steps do not prove instrument quality, "
            "semantic correctness, reviewer independence, or task completion.",
            "A review recommendation does not approve work or change task status.",
        ],
    )
