import asyncio
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import git
from mcp import Client, StdioServerParameters
from pydantic import ValidationError
from typer.testing import CliRunner

from worker_bees.analysis import build_index
from worker_bees.approval_models import ApprovalRequest
from worker_bees.approvals import Approvals
from worker_bees.cli import app
from worker_bees.planning import Planning
from worker_bees.planning_models import PlanProposal
from worker_bees.storage import MIGRATIONS, Store


@pytest.fixture
def approved_plan(proposed):
    root, database, index, raw = proposed
    store = Store(database, writable=True)
    planner = Planning(store, index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    plan = planner.get(raw["id"]).plan
    request = ApprovalRequest(
        plan_id=raw["id"],
        task_id="T1",
        submission_sha256=plan.submission_sha256,
        specification_sha256=plan.specification_sha256,
        expected_generation=0,
        action="approve",
        reason="Reviewed scope, source, acceptance criteria, and current specification.",
    )
    return root, database, index, raw, request


def test_approval_and_revocation_are_bound_audited_and_durable(approved_plan):
    _, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    assert gate.check(raw["id"], "T1").policy.decision == "require_approval"
    receipt = gate.decide(request)
    assert receipt.task.status == "ready" and receipt.task.owner is None
    assert receipt.policy.policy.decision == "allow"
    assert not receipt.policy.execution_available
    assert receipt.approval.scope == "prepare_task_assignment"
    revoked = gate.decide(request.model_copy(update={"action": "revoke", "expected_generation": 1}))
    assert revoked.task.status == "draft"
    assert revoked.policy.policy.decision == "require_approval"
    assert revoked.policy.approval_generation == 2
    assert Approvals(Store(database), index).check(raw["id"], "T1") == revoked.policy
    with Store(database).connect() as connection:
        records = connection.execute(
            "SELECT payload FROM task_approvals ORDER BY generation"
        ).fetchall()
    assert [json.loads(row[0])["action"] for row in records] == ["approve", "revoke"]
    assert [event.kind for event in Store(database).events()][-2:] == [
        "task_approved",
        "task_approval_revoked",
    ]
    assert Store(database).events()[-1].actor == "local_operator"


def test_pending_dependency_does_not_prevent_consent_but_blocks_assignment(approved_plan):
    _, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    receipt = gate.decide(request.model_copy(update={"task_id": "T2"}))
    assert receipt.task.status == "ready"
    assert receipt.policy.policy.decision == "deny"
    assert receipt.policy.blockers == [f"dependency_incomplete:{raw['id']}:T1"]
    # A later genuine completion gate can unblock assignment without rewriting the approval.
    with gate.store.connect() as connection:
        task = gate.store.get_task(f"{raw['id']}:T1")
        completed = task.model_copy(update={"status": "completed"})
        connection.execute(
            "UPDATE tasks SET payload=? WHERE id=?", (completed.model_dump_json(), task.id)
        )
    assert gate.check(raw["id"], "T2").policy.decision == "allow"


@pytest.mark.parametrize("field", ["submission_sha256", "specification_sha256"])
def test_review_hash_mismatch_never_writes(approved_plan, field):
    _, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    before = database.read_bytes()
    with pytest.raises(ValueError, match="hash"):
        gate.decide(request.model_copy(update={field: "0" * 64}))
    assert database.read_bytes() == before
    assert gate.check(raw["id"], "T1").approval_generation == 0


def test_stale_context_blocks_approval_but_never_revocation(approved_plan):
    root, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    gate.decide(request)
    (root / "new.py").write_text("def new(): pass\n")
    git(root, "add", "--all")
    git(root, "commit", "-m", "new revision")
    gate.store.save_index(build_index(root))
    policy = gate.check(raw["id"], "T1", "0" * 64)
    assert policy.policy.decision == "deny"
    assert "repository_index_changed" in policy.blockers
    assert "specification_changed" in policy.blockers
    with pytest.raises(ValueError, match="repository_index_changed"):
        gate.decide(request.model_copy(update={"expected_generation": 1}))
    revoked = gate.decide(request.model_copy(update={"action": "revoke", "expected_generation": 1}))
    assert revoked.approval.action == "revoke"
    assert revoked.task.status == "draft"


def test_reindex_of_identical_metadata_preserves_approval(approved_plan):
    root, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    gate.decide(request)
    gate.store.save_index(build_index(root))
    assert gate.check(raw["id"], "T1").policy.decision == "allow"


@pytest.mark.parametrize("status", ["blocked", "cancelled", "completed", "running"])
def test_approval_cannot_override_lifecycle_and_revocation_preserves_it(approved_plan, status):
    _, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    task = gate.store.get_task(f"{raw['id']}:T1").model_copy(update={"status": status})
    with gate.store.connect() as connection:
        connection.execute(
            "UPDATE tasks SET payload=? WHERE id=?", (task.model_dump_json(), task.id)
        )
    with pytest.raises(ValueError, match="task_state"):
        gate.decide(request)
    revoked = gate.decide(request.model_copy(update={"action": "revoke"}))
    assert revoked.task.status == status
    assert revoked.policy.policy.decision == "deny"


def test_event_failure_rolls_back_approval_and_task_update(approved_plan):
    _, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    with gate.store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        gate.decide(request)
    assert gate.check(raw["id"], "T1").approval_generation == 0
    assert gate.store.get_task(f"{raw['id']}:T1").status == "draft"


def test_concurrent_decisions_and_old_retries_cannot_overwrite_revoke(approved_plan):
    _, database, index, raw, request = approved_plan

    def attempt():
        try:
            return Approvals(Store(database, writable=True), index).decide(request).approval.action
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(2)))
    assert outcomes.count("approve") == 1
    assert sum("stale approval generation" in outcome for outcome in outcomes) == 1
    gate = Approvals(Store(database, writable=True), index)
    gate.decide(request.model_copy(update={"action": "revoke", "expected_generation": 1}))
    assert "stale approval generation" in attempt()
    assert gate.check(raw["id"], "T1").approval.action == "revoke"


def test_scoping_readonly_and_legacy_transition_cannot_bypass_gate(approved_plan):
    _, database, index, raw, request = approved_plan
    gate = Approvals(Store(database), index)
    before = database.read_bytes()
    with pytest.raises(ValueError, match="write access"):
        gate.decide(request)
    with pytest.raises(ValueError, match="task not found"):
        gate.check(raw["id"], "foreign")
    other = index.model_copy(
        update={"snapshot": index.snapshot.model_copy(update={"id": "foreign"})}
    )
    with pytest.raises(ValueError, match="selected snapshot"):
        Approvals(Store(database), other).check(raw["id"], "T1")
    assert database.read_bytes() == before
    store = Store(database, writable=True)
    with pytest.raises(ValueError, match="approval gate"):
        store.transition_task(f"{raw['id']}:T1", "draft", "ready")
    receipt = Approvals(store, index).decide(request)
    with pytest.raises(ValueError, match="execution is disabled"):
        store.transition_task(receipt.task.id, "ready", "running")
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate(request.model_dump() | {"actor": "human"})


@pytest.mark.parametrize(
    "verdict,kind,allowed",
    [
        ("blocked", "investigation", False),
        ("unknown", "implementation", False),
        ("needs_investigation", "implementation", False),
        ("unknown", "investigation", True),
        ("needs_investigation", "investigation", True),
    ],
)
def test_feasibility_gates_keep_investigation_possible(proposed, verdict, kind, allowed):
    _, database, index, raw = proposed
    raw["feasibility"]["verdict"] = verdict
    raw["tasks"][0]["kind"] = kind
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    plan = planner.get(raw["id"]).plan
    request = ApprovalRequest(
        plan_id=raw["id"],
        task_id="T1",
        submission_sha256=plan.submission_sha256,
        specification_sha256=plan.specification_sha256,
        expected_generation=0,
        action="approve",
        reason="Reviewed investigation scope.",
    )
    gate = Approvals(planner.store, index)
    if allowed:
        assert gate.decide(request).policy.policy.decision == "allow"
    else:
        with pytest.raises(ValueError, match="feasibility"):
            gate.decide(request)


def test_unresolved_decisions_cannot_be_silently_approved(proposed):
    _, database, index, raw = proposed
    raw["feasibility"]["verdict"] = "needs_investigation"
    raw["tasks"][0]["kind"] = "investigation"
    raw["decisions"] = [
        {"id": "D1", "question": "Which error contract?", "requirement_ids": ["R1"]}
    ]
    planner = Planning(Store(database, writable=True), index)
    planner.submit(PlanProposal.model_validate(raw), actor="test")
    plan = planner.get(raw["id"]).plan
    request = ApprovalRequest(
        plan_id=raw["id"],
        task_id="T1",
        submission_sha256=plan.submission_sha256,
        specification_sha256=plan.specification_sha256,
        expected_generation=0,
        action="approve",
        reason="Reviewed scope.",
    )
    with pytest.raises(ValueError, match="unresolved_decision:D1"):
        Approvals(planner.store, index).decide(request)


def test_v3_upgrade_preserves_plans_tasks_and_requires_explicit_init(approved_plan):
    _, database, index, raw, _ = approved_plan
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE worker_runs")
        connection.execute("DROP TABLE task_leases")
        connection.execute("DROP TABLE task_approvals")
        connection.execute("PRAGMA user_version = 3")
    with pytest.raises(ValueError, match="migration"):
        Store(database)
    assert CliRunner().invoke(app, ["init", "--db", str(database)]).exit_code == 0
    store = Store(database)
    assert store.get_plan(raw["id"], index.snapshot.id).proposal.id == raw["id"]
    assert store.get_task(f"{raw['id']}:T1").status == "draft"
    with store.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_operator_cli_and_policy_reports(approved_plan, tmp_path):
    _, database, _, raw, request = approved_plan
    common = ["--db", str(database)]
    runner = CliRunner()
    before = database.read_bytes()
    policy = runner.invoke(app, ["check-task-policy", raw["id"], "T1", *common])
    assert (
        policy.exit_code == 0
        and json.loads(policy.output)["policy"]["decision"] == "require_approval"
    )
    assert database.read_bytes() == before
    path = tmp_path / "approval.json"
    path.write_text(request.model_dump_json())
    result = runner.invoke(app, ["decide-task", str(path), *common])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["task"]["status"] == "ready"
    assert runner.invoke(app, ["decide-task", str(path), *common]).exit_code == 1
    path.write_text("x" * 8193)
    result = runner.invoke(app, ["decide-task", str(path), *common])
    assert result.exit_code == 1 and "budget" in result.output


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_policy_observes_operator_revocation_but_cannot_approve(approved_plan, mode):
    _, database, index, raw, request = approved_plan
    gate = Approvals(Store(database, writable=True), index)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "worker_bees", "serve", "--db", str(database), "--enable-planning"],
    )

    async def scenario():
        async with Client(parameters, mode=mode, read_timeout_seconds=10) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 18
            names = {tool.name for tool in tools}
            assert not {"decide_task", "approve_task", "revoke_task"}.intersection(names)
            assert next(
                t for t in tools if t.name == "check_task_policy"
            ).annotations.read_only_hint
            args = {"plan_id": raw["id"], "task_id": "T1"}
            before = database.read_bytes()
            result = await client.call_tool("check_task_policy", args)
            assert result.structured_content["policy"]["decision"] == "require_approval"
            assert database.read_bytes() == before
            gate.decide(request)
            result = await client.call_tool("check_task_policy", args)
            assert result.structured_content["policy"]["decision"] == "allow"
            gate.decide(request.model_copy(update={"action": "revoke", "expected_generation": 1}))
            result = await client.call_tool("check_task_policy", args)
            assert result.structured_content["policy"]["decision"] == "require_approval"
            ignored = await client.call_tool("check_task_policy", args | {"approved": True})
            assert ignored.structured_content["policy"]["decision"] == "require_approval"
            invalid = await client.call_tool(
                "check_task_policy", args | {"specification_sha256": "invalid"}
            )
            assert invalid.is_error

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))
