import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import git
from pydantic import ValidationError

from worker_bees.analysis import build_index
from worker_bees.planning import Planning, digest
from worker_bees.planning_models import PlanProposal
from worker_bees.storage import Store


def test_valid_plan_has_coverage_topology_and_unverified_status(proposed):
    _, database, index, raw = proposed
    planner = Planning(Store(database), index)
    report = planner.validate(PlanProposal.model_validate(raw))
    assert report.valid
    assert report.ordered_task_ids == ["T1", "T2"]
    assert report.requirement_coverage == {"R1": ["T1", "T2"], "R2": ["T1", "T2"]}
    assert report.specification_sha256 == digest(raw["specification"])
    assert report.assessment == "unverified_proposal"
    assert "not approval" in report.warnings[0]
    assert len(Store(database).events()) == 1


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda p: p.update(snapshot_id="foreign"), "snapshot does not match"),
        (
            lambda p: p["requirements"].append(copy.deepcopy(p["requirements"][0])),
            "duplicate requirement",
        ),
        (lambda p: p["tasks"].append(copy.deepcopy(p["tasks"][0])), "duplicate task"),
        (lambda p: p["tasks"][0].update(depends_on=["absent"]), "unknown dependency"),
        (lambda p: p["tasks"][0].update(depends_on=["T1"]), "depend on itself"),
        (lambda p: p["tasks"][0].update(depends_on=["T2"]), "contains a cycle"),
        (lambda p: p["tasks"][0].update(requirement_ids=["missing"]), "unknown requirement"),
        (
            lambda p: p["requirements"].append({"id": "R3", "outcome": "Uncovered"}),
            "no task covers",
        ),
        (lambda p: p["tasks"][0].update(affected_symbols=["/etc/passwd"]), "absent or ambiguous"),
        (lambda p: p["feasibility"].update(code_references=[]), "requires at least one"),
        (
            lambda p: p["feasibility"].update(unknowns=["Unresolved behavior"]),
            "unresolved unknowns",
        ),
        (lambda p: p["tasks"][0].update(depends_on=["T2", "T2"]), "duplicate dependencies"),
        (
            lambda p: p["tasks"][0].update(requirement_ids=["R1", "R1"]),
            "duplicate requirement links",
        ),
        (
            lambda p: p["tasks"][0]["acceptance_criteria"].append(
                copy.deepcopy(p["tasks"][0]["acceptance_criteria"][0])
            ),
            "duplicate acceptance",
        ),
        (lambda p: p["requirements"][0].update(disposition="deferred"), "requires a rationale"),
        (
            lambda p: p["requirements"][0].update(disposition="deferred", rationale="Later"),
            "deferred requirement R1 has work",
        ),
    ],
)
def test_invalid_semantics_never_persist(proposed, mutation, expected):
    _, database, index, raw = proposed
    mutation(raw)
    planner = Planning(Store(database, writable=True), index)
    candidate = PlanProposal.model_validate(raw)
    report = planner.validate(candidate)
    assert not report.valid and any(expected in message for message in report.errors)
    with pytest.raises(ValueError, match="invalid plan"):
        planner.submit(candidate, actor="test")
    with Store(database).connect() as connection:
        assert connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    assert len(Store(database).events()) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(owner="queen"),
        lambda p: p.update(status="approved"),
        lambda p: p.update(id="../../outside"),
        lambda p: p.update(specification="   "),
        lambda p: p["tasks"][0].update(owner="developer"),
        lambda p: p["tasks"][0].update(acceptance_criteria=[]),
        lambda p: p["tasks"][0]["acceptance_criteria"][0].update(validation=[]),
        lambda p: p["tasks"][0]["acceptance_criteria"][0]["validation"][0].update(
            expected_result=""
        ),
    ],
)
def test_contracts_reject_authority_and_missing_validation(proposed, mutation):
    *_, raw = proposed
    mutation(raw)
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(raw)


def test_unknown_and_deferred_requirements_are_explicit(proposed):
    _, database, index, raw = proposed
    raw["requirements"] = [
        {"id": "R1", "outcome": "Later", "disposition": "deferred", "rationale": "Out of scope"},
        {
            "id": "R2",
            "outcome": "Choose behavior",
            "disposition": "needs_decision",
            "rationale": "The specification leaves this open",
        },
    ]
    raw["tasks"] = []
    raw["feasibility"].update(verdict="unknown", code_references=[], unknowns=["Choose exception"])
    raw["decisions"] = [{"id": "D1", "question": "Which exception?", "requirement_ids": ["R2"]}]
    planner = Planning(Store(database, writable=True), index)
    proposal = PlanProposal.model_validate(raw)
    assert planner.validate(proposal).valid
    assert planner.submit(proposal, actor="test").task_ids == []
    raw["decisions"] = []
    assert not planner.validate(PlanProposal.model_validate(raw)).valid


def test_submission_retry_is_idempotent_and_preserves_task_changes(proposed):
    _, database, index, raw = proposed
    planner = Planning(Store(database, writable=True), index)
    proposal = PlanProposal.model_validate(raw)
    first = planner.submit(proposal, actor="test")
    assert first.created and first.status == "draft"
    store = Store(database, writable=True)
    task = store.get_task(first.task_ids[1])
    assert task.plan_id == proposal.id and task.local_id == "T2"
    assert task.depends_on == [first.task_ids[0]] and task.owner is None
    assert task.base_revision == index.snapshot.commit
    assert task.validation_needs and task.acceptance_criteria
    with pytest.raises(ValueError, match="approval"):
        store.transition_task(first.task_ids[0], "draft", "ready")
    store.transition_task(first.task_ids[0], "draft", "blocked")
    count = len(store.events())
    assert not planner.submit(proposal, actor="test").created
    assert len(store.events()) == count
    assert store.get_task(first.task_ids[0]).status == "blocked"
    raw["title"] = "Changed plan"
    with pytest.raises(ValueError, match="different content"):
        planner.submit(PlanProposal.model_validate(raw), actor="test")
    assert planner.get(proposal.id).plan.proposal.title == proposal.title


def test_concurrent_identical_submissions_create_one_plan(proposed):
    _, database, index, raw = proposed
    proposal = PlanProposal.model_validate(raw)

    def submit(_):
        return (
            Planning(Store(database, writable=True), index).submit(proposal, actor="test").created
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(submit, range(4)))
    assert outcomes.count(True) == 1
    assert len(Store(database).events()) == 4  # snapshot + two tasks + plan


def test_plan_tasks_and_events_rollback_together(proposed):
    _, database, index, raw = proposed
    store = Store(database, writable=True)
    with store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_plan BEFORE INSERT ON events "
            "WHEN json_extract(NEW.payload, '$.kind') = 'plan_submitted' "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        Planning(store, index).submit(PlanProposal.model_validate(raw), actor="test")
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    assert len(store.events()) == 1


def test_freshness_and_scope_survive_restart(proposed, make_repo):
    root, database, index, raw = proposed
    proposal = PlanProposal.model_validate(raw)
    Planning(Store(database, writable=True), index).submit(proposal, actor="test")
    reader = Planning(Store(database), index)
    assert not reader.get(proposal.id).needs_reassessment
    Store(database, writable=True).save_index(build_index(root))  # Same content, new snapshot ID.
    assert not reader.get(proposal.id).needs_reassessment
    foreign = build_index(make_repo({"other.py": "pass"}))
    Store(database, writable=True).save_index(foreign)
    assert not reader.get(proposal.id).needs_reassessment
    with pytest.raises(ValueError, match="selected snapshot"):
        Planning(Store(database), foreign).get(proposal.id)
    (root / "shop" / "pricing.py").write_text("def replacement():\n    pass\n")
    git(root, "add", "--all")
    git(root, "commit", "-m", "change pricing")
    Store(database, writable=True).save_index(build_index(root))
    view = reader.get(proposal.id, specification="Updated specification")
    assert view.needs_reassessment
    assert view.reasons == ["repository_index_changed", "specification_changed"]
    assert view.plan.proposal.specification == proposal.specification
    listed = reader.list_tasks(proposal.id, limit=1)
    assert listed.total == 2 and listed.truncated and listed.needs_reassessment
    assert reader.get_task(proposal.id, "T2").task.depends_on
    with pytest.raises(ValueError, match="task not found"):
        reader.get_task(proposal.id, "foreign")


def test_conflicting_concurrent_submissions_do_not_overwrite(proposed):
    _, database, index, raw = proposed
    first = PlanProposal.model_validate(raw)
    second = PlanProposal.model_validate(raw | {"title": "A conflicting proposal"})

    def submit(proposal):
        try:
            return Planning(Store(database, writable=True), index).submit(proposal, actor="test")
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, [first, second]))
    assert sum(isinstance(result, str) for result in results) == 1
    assert len(Store(database).events()) == 4


def test_submission_size_and_read_only_access_are_enforced(proposed):
    _, database, index, raw = proposed
    proposal = PlanProposal.model_validate(raw)
    with pytest.raises(ValueError, match="write access"):
        Planning(Store(database), index).submit(proposal, actor="test")
    raw["constraints"] = ["x" * 2000] * 20
    raw["feasibility"]["risks"] = ["y" * 2000] * 20
    huge = PlanProposal.model_validate(raw)
    planner = Planning(Store(database, writable=True), index)
    assert not planner.validate(huge).valid
    with pytest.raises(ValueError, match="input budget"):
        planner.submit(huge, actor="test")
    assert len(Store(database).events()) == 1


def test_decision_references_and_blocking_feasibility_are_checked(proposed):
    _, database, index, raw = proposed
    raw["decisions"] = [
        {"id": "D1", "question": "Is the behavior approved?", "requirement_ids": ["R1"]},
        {"id": "D1", "question": "Another question", "requirement_ids": ["foreign"]},
    ]
    report = Planning(Store(database), index).validate(PlanProposal.model_validate(raw))
    assert not report.valid
    assert any("duplicate decision" in error for error in report.errors)
    assert any("unknown requirement" in error for error in report.errors)
    assert any("blocking decisions" in error for error in report.errors)


def test_snapshot_metadata_change_also_requires_reassessment(proposed):
    _, database, index, raw = proposed
    store = Store(database, writable=True)
    planner = Planning(store, index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    from worker_bees.models import new_id

    changed = index.model_copy(
        update={
            "snapshot": index.snapshot.model_copy(
                update={"id": new_id(), "parser": "another-parser"}
            )
        }
    )
    store.save_index(changed)
    assert planner.get(raw["id"]).reasons == ["repository_index_changed"]


def test_proposed_validation_commands_are_never_executed(proposed, tmp_path):
    _, database, index, raw = proposed
    canary = tmp_path / "must-not-exist"
    raw["tasks"][0]["acceptance_criteria"][0]["validation"][0]["procedure"] = (
        f"python -c 'from pathlib import Path; Path({str(canary)!r}).touch()'"
    )
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    assert not canary.exists()


def test_latest_index_staleness_does_not_inspect_uncommitted_source(proposed):
    root, database, index, raw = proposed
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    (root / "shop" / "pricing.py").write_text("changed but unindexed")
    view = planner.get(raw["id"])
    assert not view.needs_reassessment
    assert any("unindexed changes" in warning for warning in view.warnings)


def test_task_id_collision_rolls_back_the_whole_plan(proposed):
    _, database, index, raw = proposed
    from worker_bees.models import Task

    store = Store(database, writable=True)
    store.create_task(
        Task(
            id=raw["id"] + ":T2",
            objective="Existing standalone task",
            acceptance_criteria=["A"],
            validation_needs=["V"],
            base_revision=index.snapshot.commit,
        )
    )
    with pytest.raises(sqlite3.IntegrityError):
        Planning(store, index).submit(PlanProposal.model_validate(raw), actor="test")
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert len(store.events()) == 2


def test_oversized_snapshot_metadata_is_rejected_before_any_plan_write(proposed):
    _, database, index, raw = proposed
    from worker_bees.models import Issue, new_id

    snapshot = index.snapshot.model_copy(
        update={
            "id": new_id(),
            "status": "partial",
            "issues": [Issue(path="x" * 1000, reason="r" * 1000) for _ in range(40)],
        }
    )
    large = index.model_copy(update={"snapshot": snapshot})
    store = Store(database, writable=True)
    store.save_index(large)
    raw["snapshot_id"] = snapshot.id
    with pytest.raises(ValueError, match="readable result budget"):
        Planning(store, large).submit(PlanProposal.model_validate(raw), actor="test")
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
