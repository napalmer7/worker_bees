import json

import pytest
from pydantic import ValidationError

from worker_bees.assessment import AssessmentRequest, assess_task
from worker_bees.planning import Planning, digest
from worker_bees.planning_models import PlanProposal
from worker_bees.specifications import prepare_specification
from worker_bees.storage import Store


@pytest.fixture
def assessment_case(proposed):
    _, database, index, raw = proposed
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    report = {
        "plan_id": raw["id"],
        "task_id": "T1",
        "implementation_revision": index.snapshot.commit,
        "evidence": [
            {
                "id": f"E{i}",
                "criterion_id": f"AC{i}",
                "validation_index": 0,
                "revision": index.snapshot.commit,
                "method": "automated",
                "outcome": "pass",
                "artifact": "reported/test-results.txt",
                "summary": "Reported result, not verified",
            }
            for i in [1, 2]
        ],
        "reviews": [
            {
                "criterion_id": f"AC{i}",
                "judgment": "supported",
                "rationale": "Client-supplied review, not independently verified.",
                "evidence_ids": [f"E{i}"],
            }
            for i in [1, 2]
        ],
    }
    return planner, report, database


def test_complete_reports_are_only_candidates_for_independent_review(assessment_case):
    planner, raw, database = assessment_case
    before = database.read_bytes()
    result = assess_task(planner, AssessmentRequest.model_validate(raw))
    assert result.recommendation == "ready_for_independent_review"
    assert result.blockers == []
    assert result.basis == "reported_evidence_and_client_review"
    assert result.task_status_changed is False
    assert all(criterion.state == "reported_support" for criterion in result.criteria)
    assert result.criteria[0].accepted_evidence_ids == ["E1"]
    assert result.criteria[0].review_rationale
    assert any("client-supplied claims" in warning for warning in result.warnings)
    assert database.read_bytes() == before
    assert planner.get_task(raw["plan_id"], "T1").task.status == "draft"


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda p: p.update(evidence=[], reviews=[]), "needs_evidence"),
        (lambda p: p.update(reviews=[]), "needs_review"),
        (lambda p: p["evidence"][0].update(revision="0" * 40), "needs_evidence"),
        (lambda p: p["evidence"][0].update(method="inspection"), "needs_evidence"),
        (lambda p: p["evidence"][0].update(outcome="not_run"), "needs_evidence"),
        (lambda p: p["evidence"][0].update(outcome="inconclusive"), "needs_evidence"),
        (lambda p: p["evidence"][0].update(outcome="fail"), "blocked"),
        (lambda p: p["reviews"][0].update(judgment="uncertain"), "needs_review"),
        (lambda p: p["reviews"][0].update(judgment="not_met"), "blocked"),
        (lambda p: p["reviews"][0].update(evidence_ids=[]), "needs_review"),
        (lambda p: p["reviews"][0].update(evidence_ids=["E2"]), "needs_review"),
        (lambda p: p.update(current_specification="A changed specification"), "blocked"),
    ],
)
def test_incomplete_or_contradictory_claims_do_not_become_success(
    assessment_case, mutation, expected
):
    planner, raw, _ = assessment_case
    mutation(raw)
    result = assess_task(planner, AssessmentRequest.model_validate(raw))
    assert result.recommendation == expected
    assert result.task_status_changed is False


def test_conflicting_pass_and_fail_never_prefer_the_green_result(assessment_case):
    planner, raw, _ = assessment_case
    raw["evidence"].append(raw["evidence"][0] | {"id": "E3", "outcome": "fail"})
    result = assess_task(planner, AssessmentRequest.model_validate(raw))
    assert result.recommendation == "blocked"
    assert any("even if another" in note for note in result.criteria[0].notes)


@pytest.mark.parametrize("outcome", ["inconclusive", "not_run"])
def test_unresolved_reports_are_not_hidden_by_another_pass(assessment_case, outcome):
    planner, raw, _ = assessment_case
    raw["evidence"].append(raw["evidence"][0] | {"id": "E3", "outcome": outcome})
    assert (
        assess_task(planner, AssessmentRequest.model_validate(raw)).recommendation == "needs_review"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["evidence"].append(p["evidence"][0]),
        lambda p: p["evidence"][0].update(criterion_id="missing"),
        lambda p: p["evidence"][0].update(validation_index=1),
        lambda p: p["reviews"].append(p["reviews"][0]),
        lambda p: p["reviews"][0].update(criterion_id="missing"),
        lambda p: p["reviews"][0].update(evidence_ids=["missing"]),
        lambda p: p["reviews"][0].update(evidence_ids=["E1", "E1"]),
    ],
)
def test_invalid_evidence_links_are_errors(assessment_case, mutation):
    planner, raw, _ = assessment_case
    mutation(raw)
    with pytest.raises(ValueError):
        assess_task(planner, AssessmentRequest.model_validate(raw))


def test_artifact_locators_are_not_opened_and_tasks_are_not_completed(assessment_case, monkeypatch):
    from pathlib import Path

    planner, raw, _ = assessment_case
    raw["evidence"][0]["artifact"] = "/etc/passwd"

    def forbidden(*args, **kwargs):
        raise AssertionError("artifact access is forbidden")

    monkeypatch.setattr(Path, "open", forbidden)
    result = assess_task(planner, AssessmentRequest.model_validate(raw))
    assert result.recommendation == "ready_for_independent_review"
    assert any("were not opened" in warning for warning in result.warnings)


def test_caller_cannot_claim_approval_or_skip_revision_binding(assessment_case):
    _, raw, _ = assessment_case
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(raw | {"approved": True})
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(raw | {"implementation_revision": "HEAD"})


def test_source_citations_are_checked_without_claiming_semantic_coverage(proposed):
    _, database, index, raw = proposed
    prepared = prepare_specification(raw["specification"])
    raw["requirements"][0]["source_blocks"] = [prepared.blocks[0].id]
    planner = Planning(Store(database), index)
    valid = planner.validate(PlanProposal.model_validate(raw))
    assert valid.valid and valid.source_mapping == "provided_unverified"
    assert valid.source_block_coverage["R1"] == ["B0001"]
    raw["requirements"][0]["source_blocks"] = ["B9999"]
    assert not planner.validate(PlanProposal.model_validate(raw)).valid
    raw["requirements"][0]["source_blocks"] = ["B0001", "B0001"]
    assert not planner.validate(PlanProposal.model_validate(raw)).valid


def test_legacy_retries_remain_idempotent_after_optional_citation_fields(proposed):
    _, database, index, raw = proposed
    planner = Planning(Store(database, writable=True), index)
    candidate = PlanProposal.model_validate(raw)
    planner.submit(candidate, actor="test")
    with planner.store.connect() as connection:
        payload = json.loads(connection.execute("SELECT payload FROM plans").fetchone()[0])
        for requirement in payload["proposal"]["requirements"]:
            requirement.pop("source_blocks")
        canonical = json.dumps(payload["proposal"], sort_keys=True, separators=(",", ":"))
        payload["submission_sha256"] = digest(canonical)
        connection.execute("UPDATE plans SET payload = ?", (json.dumps(payload),))
    count = len(planner.store.events())
    assert not planner.submit(candidate, actor="test").created
    assert len(planner.store.events()) == count
    raw["requirements"][0]["source_blocks"] = ["B0001"]
    with pytest.raises(ValueError, match="different content"):
        planner.submit(PlanProposal.model_validate(raw), actor="test")


def test_every_declared_validation_step_needs_matching_reviewed_evidence(proposed):
    _, database, index, raw = proposed
    criterion = raw["tasks"][0]["acceptance_criteria"][0]
    criterion["validation"].append(
        {
            "method": "inspection",
            "procedure": "Review the actual entry path",
            "expected_result": "Guard is reachable",
        }
    )
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    report = AssessmentRequest.model_validate(
        {
            "plan_id": raw["id"],
            "task_id": "T1",
            "implementation_revision": index.snapshot.commit,
            "evidence": [
                {
                    "id": "E1",
                    "criterion_id": "AC1",
                    "validation_index": 0,
                    "revision": index.snapshot.commit,
                    "method": "automated",
                    "outcome": "pass",
                    "artifact": "reported/results",
                    "summary": "Claimed pass",
                }
            ],
        }
    )
    result = assess_task(planner, report)
    assert result.criteria[0].missing_validation_indexes == [1]
    assert result.recommendation == "needs_evidence"


def test_blocked_task_cannot_become_a_review_candidate(assessment_case):
    planner, raw, _ = assessment_case
    planner.store.transition_task(raw["plan_id"] + ":T1", "draft", "blocked")
    result = assess_task(planner, AssessmentRequest.model_validate(raw))
    assert result.recommendation == "blocked" and result.blockers == ["task_state:blocked"]


def test_unmapped_spec_blocks_are_visible_and_not_automatically_requirements(proposed):
    _, database, index, raw = proposed
    raw["specification"] += "\n\nBackground context."
    raw["requirements"][0]["source_blocks"] = ["B0001"]
    result = Planning(Store(database), index).validate(PlanProposal.model_validate(raw))
    assert result.valid and result.unmapped_source_blocks == ["B0002"]
    assert result.source_mapping == "provided_unverified"


def test_large_legacy_retry_is_not_rejected_by_new_default_fields(proposed):
    _, database, index, raw = proposed
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    with planner.store.connect() as connection:
        payload = json.loads(connection.execute("SELECT payload FROM plans").fetchone()[0])
        proposal = payload["proposal"]
        for number in range(3, 101):
            proposal["requirements"].append(
                {
                    "schema_version": 1,
                    "id": f"R{number}",
                    "outcome": "Explicitly deferred",
                    "disposition": "deferred",
                    "rationale": "Outside this change",
                }
            )
        for requirement in proposal["requirements"]:
            requirement.pop("source_blocks", None)
        proposal["constraints"] = []

        def size():
            return len(json.dumps(proposal, separators=(",", ":")).encode())

        while size() < 49_080:
            proposal["constraints"].append("x" * min(2000, 49_100 - size() - 3))
        assert size() < 49_152
        candidate = PlanProposal.model_validate(proposal)
        assert len(candidate.model_dump_json().encode()) > 49_152
        payload["submission_sha256"] = digest(
            json.dumps(proposal, sort_keys=True, separators=(",", ":"))
        )
        connection.execute("UPDATE plans SET payload = ?", (json.dumps(payload),))
    count = len(planner.store.events())
    assert not planner.submit(candidate, actor="test").created
    assert len(planner.store.events()) == count


def test_blocking_decisions_prevent_a_review_candidate(proposed):
    _, database, index, raw = proposed
    raw["feasibility"]["verdict"] = "needs_investigation"
    raw["decisions"] = [
        {"id": "D1", "question": "Confirm the API behavior", "requirement_ids": ["R1"]}
    ]
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    request = AssessmentRequest(
        plan_id=raw["id"], task_id="T1", implementation_revision=index.snapshot.commit
    )
    assert assess_task(planner, request).recommendation == "blocked"
