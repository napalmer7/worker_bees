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
from worker_bees.lease_models import ClaimRequest, LeaseCredential, RenewRequest
from worker_bees.lease_state import now_ms
from worker_bees.leases import Leases
from worker_bees.mcp_server import create_server
from worker_bees.planning import Planning
from worker_bees.planning_models import PlanProposal
from worker_bees.storage import Store


@pytest.fixture
def reservation(proposed):
    root, database, index, raw = proposed
    store = Store(database, writable=True)
    planning = Planning(store, index)
    planning.submit(PlanProposal.model_validate(raw), actor="test")
    plan = planning.get(raw["id"]).plan
    approval = ApprovalRequest(
        plan_id=raw["id"],
        task_id="T1",
        submission_sha256=plan.submission_sha256,
        specification_sha256=plan.specification_sha256,
        expected_generation=0,
        action="approve",
        reason="Reviewed bounded sample task.",
    )
    Approvals(store, index).decide(approval)
    request = ClaimRequest(
        plan_id=raw["id"],
        task_id="T1",
        owner="bee-one",
        submission_sha256=plan.submission_sha256,
        specification_sha256=plan.specification_sha256,
        expected_approval_generation=1,
        expected_lease_generation=0,
        ttl_seconds=30,
    )
    clock = [now_ms()]
    leases = Leases(store, index, clock=lambda: clock[0])
    return root, database, index, leases, request, approval, clock


def credential(grant):
    view = grant.lease
    return LeaseCredential(
        plan_id=view.plan_id,
        task_id=view.task_id,
        owner=view.owner,
        lease_id=view.lease_id,
        generation=view.generation,
        token=grant.token,
    )


def renewal(grant, request):
    return RenewRequest(
        **credential(grant).model_dump(),
        specification_sha256=request.specification_sha256,
        ttl_seconds=30,
    )


def test_claim_heartbeat_release_keep_task_ready_and_credentials_private(reservation):
    _, database, index, leases, request, _, clock = reservation
    assert leases.get(request.plan_id, request.task_id).state == "absent"
    grant = leases.claim(request)
    assert grant.lease.state == "active" and grant.lease.effective_owner == "bee-one"
    assert not grant.lease.execution_available
    assert grant.token not in repr(grant)
    assert grant.token not in database.read_bytes().decode("latin1")
    task = leases.store.get_task(f"{request.plan_id}:{request.task_id}")
    assert task.status == "ready" and task.owner is None
    assert Approvals(leases.store, index).check(request.plan_id, request.task_id).blockers == [
        "task_reserved"
    ]
    clock[0] += 10_000
    heartbeat = leases.renew(renewal(grant, request))
    assert heartbeat.generation == 1
    assert heartbeat.expires_at_ms == clock[0] + 30_000
    before = database.read_bytes()
    observed = Leases(Store(database), index, clock=lambda: clock[0]).get(
        request.plan_id, request.task_id
    )
    assert observed == heartbeat
    assert "token" not in observed.model_dump_json()
    assert database.read_bytes() == before
    released = leases.release(credential(grant))
    assert released.state == "released" and released.effective_owner is None
    events = leases.store.events()
    assert [event.kind for event in events][-3:] == [
        "lease_claimed",
        "lease_renewed",
        "lease_released",
    ]
    assert leases.release(credential(grant)).state == "released"
    assert leases.store.events() == events


def test_only_one_concurrent_claim_and_old_request_cannot_replay(reservation):
    _, database, index, leases, request, _, _ = reservation

    def claim(owner):
        try:
            return Leases(Store(database, writable=True), index, clock=leases.clock).claim(
                request.model_copy(update={"owner": owner})
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["bee-one", "bee-two"]))
    assert sum(not isinstance(result, str) for result in results) == 1
    assert any(isinstance(result, str) and "stale lease generation" in result for result in results)
    assert len([event for event in leases.store.events() if event.kind == "lease_claimed"]) == 1
    with pytest.raises(ValueError, match="stale lease generation"):
        leases.claim(request)
    with pytest.raises(ValueError, match="already reserved"):
        leases.claim(request.model_copy(update={"expected_lease_generation": 1}))


def test_expiry_is_effective_without_writes_and_old_tokens_are_fenced(reservation):
    _, database, _, leases, request, _, clock = reservation
    grant = leases.claim(request)
    clock[0] = grant.lease.expires_at_ms
    before = database.read_bytes()
    view = leases.get(request.plan_id, request.task_id)
    assert view.state == "expired" and view.effective_owner is None
    assert database.read_bytes() == before
    with pytest.raises(ValueError, match="expired"):
        leases.renew(renewal(grant, request))
    replacement = leases.claim(
        request.model_copy(update={"expected_lease_generation": 1, "owner": "bee-two"})
    )
    assert replacement.lease.generation == 2 and replacement.lease.effective_owner == "bee-two"
    for operation, argument in [
        (leases.renew, renewal(grant, request)),
        (leases.release, credential(grant)),
    ]:
        with pytest.raises(ValueError, match="stale lease credentials"):
            operation(argument)
    with leases.store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM task_leases").fetchone()[0] == 2


@pytest.mark.parametrize(
    "field,value",
    [("owner", "someone-else"), ("token", "0" * 64), ("generation", 2), ("lease_id", "other")],
)
def test_credentials_cannot_impersonate_current_holder(reservation, field, value):
    _, database, _, leases, request, _, _ = reservation
    grant = leases.claim(request)
    before = database.read_bytes()
    for operation, argument in [
        (leases.renew, renewal(grant, request)),
        (leases.release, credential(grant)),
    ]:
        with pytest.raises(ValueError, match="credentials"):
            operation(argument.model_copy(update={field: value}))
    assert database.read_bytes() == before


def test_revocation_atomically_invalidates_and_reapproval_needs_new_claim(reservation):
    _, _, index, leases, request, approval, _ = reservation
    grant = leases.claim(request)
    operator = Approvals(leases.store, index)
    with pytest.raises(ValueError, match="task_reserved"):
        operator.decide(approval.model_copy(update={"expected_generation": 1}))
    revoked = operator.decide(
        approval.model_copy(update={"action": "revoke", "expected_generation": 1})
    )
    assert revoked.task.status == "draft"
    assert revoked.policy.reservation.state == "invalidated"
    assert leases.get(request.plan_id, request.task_id).effective_owner is None
    with pytest.raises(ValueError, match="invalidated"):
        leases.renew(renewal(grant, request))
    operator.decide(approval.model_copy(update={"expected_generation": 2}))
    with pytest.raises(ValueError, match="invalidated"):
        leases.renew(renewal(grant, request))
    replacement = leases.claim(
        request.model_copy(
            update={"expected_approval_generation": 3, "expected_lease_generation": 1}
        )
    )
    assert replacement.lease.generation == 2
    with pytest.raises(ValueError, match="credentials"):
        leases.release(credential(grant))


@pytest.mark.parametrize("after", ["blocked", "cancelled"])
def test_lifecycle_change_invalidates_reservation(reservation, after):
    _, _, _, leases, request, _, _ = reservation
    grant = leases.claim(request)
    leases.store.transition_task(f"{request.plan_id}:{request.task_id}", "ready", after)
    assert leases.get(request.plan_id, request.task_id).state == "invalidated"
    with pytest.raises(ValueError, match="invalidated"):
        leases.renew(renewal(grant, request))
    assert leases.store.events()[-1].kind == "lease_invalidated"


def test_context_changes_deny_renewal_and_claim_but_allow_cleanup(reservation):
    root, _, _, leases, request, _, _ = reservation
    grant = leases.claim(request)
    with pytest.raises(ValueError, match="specification_changed"):
        leases.renew(renewal(grant, request).model_copy(update={"specification_sha256": "0" * 64}))
    (root / "new.py").write_text("def new(): pass\n")
    git(root, "add", "--all")
    git(root, "commit", "-m", "new source")
    leases.store.save_index(build_index(root))
    assert leases.get(request.plan_id, request.task_id).state == "invalidated"
    with pytest.raises(ValueError, match="repository_index_changed"):
        leases.renew(renewal(grant, request))
    with pytest.raises(ValueError, match="claim denied"):
        leases.claim(request.model_copy(update={"expected_lease_generation": 1}))
    assert leases.release(credential(grant)).state == "released"


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("expected_approval_generation", 2, "stale approval"),
        ("submission_sha256", "0" * 64, "submission hash"),
        ("specification_sha256", "0" * 64, "specification_changed"),
        ("task_id", "foreign", "task not found"),
    ],
)
def test_claim_rechecks_exact_context(reservation, field, value, message):
    _, database, _, leases, request, _, _ = reservation
    before = database.read_bytes()
    with pytest.raises(ValueError, match=message):
        leases.claim(request.model_copy(update={field: value}))
    assert database.read_bytes() == before


def test_check_then_revoke_and_dependency_change_cannot_race_claim(reservation):
    _, _, index, leases, request, approval, _ = reservation
    operator = Approvals(leases.store, index)
    assert operator.check(request.plan_id, request.task_id).policy.decision == "allow"
    operator.decide(approval.model_copy(update={"action": "revoke", "expected_generation": 1}))
    with pytest.raises(ValueError, match="stale approval"):
        leases.claim(request)
    operator.decide(approval.model_copy(update={"expected_generation": 2}))
    dependent = approval.model_copy(update={"task_id": "T2"})
    receipt = operator.decide(dependent)
    with pytest.raises(ValueError, match="dependency_incomplete"):
        leases.claim(
            request.model_copy(
                update={
                    "task_id": "T2",
                    "expected_approval_generation": receipt.approval.generation,
                }
            )
        )


def test_clock_rollback_and_ttl_limits_do_not_extend_authority(reservation):
    _, _, _, leases, request, _, clock = reservation
    grant = leases.claim(request)
    clock[0] -= 1
    assert leases.get(request.plan_id, request.task_id).state == "invalidated"
    with pytest.raises(ValueError, match="clock_moved_backwards"):
        leases.renew(renewal(grant, request))
    with pytest.raises(ValueError, match="clock moved backwards"):
        leases.claim(request.model_copy(update={"expected_lease_generation": 1}))
    assert leases.release(credential(grant)).state == "released"
    for value in [0, 29, 901, "30", True]:
        with pytest.raises(ValidationError):
            ClaimRequest.model_validate(request.model_dump() | {"ttl_seconds": value})


@pytest.mark.parametrize("operation", ["claim", "renew", "release", "revoke", "block"])
def test_event_failure_rolls_back_lease_and_related_state(reservation, operation):
    _, database, index, leases, request, approval, _ = reservation
    grant = None if operation == "claim" else leases.claim(request)
    with leases.store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'event failure'); END"
        )
    before = database.read_bytes()
    with pytest.raises(sqlite3.IntegrityError):
        if operation == "claim":
            leases.claim(request)
        elif operation == "renew":
            leases.renew(renewal(grant, request))
        elif operation == "release":
            leases.release(credential(grant))
        elif operation == "revoke":
            Approvals(leases.store, index).decide(
                approval.model_copy(update={"action": "revoke", "expected_generation": 1})
            )
        else:
            leases.store.transition_task(f"{request.plan_id}:{request.task_id}", "ready", "blocked")
    assert database.read_bytes() == before
    assert leases.get(request.plan_id, request.task_id).state == (
        "absent" if operation == "claim" else "active"
    )


def test_readonly_and_snapshot_scoping_and_v4_migration(reservation):
    _, database, index, leases, request, _, _ = reservation
    before = database.read_bytes()
    with pytest.raises(ValueError, match="write access"):
        Leases(Store(database), index).claim(request)
    wrong = index.model_copy(update={"snapshot": index.snapshot.model_copy(update={"id": "other"})})
    with pytest.raises(ValueError, match="selected snapshot"):
        Leases(leases.store, wrong).claim(request)
    assert database.read_bytes() == before
    grant = leases.claim(request)
    public = leases.get(request.plan_id, request.task_id).model_dump_json()
    assert grant.token not in public and "token_sha256" not in public
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE task_leases")
        connection.execute("PRAGMA user_version = 4")
    with pytest.raises(ValueError, match="migration"):
        create_server(database, enable_planning=True)
    result = CliRunner().invoke(app, ["init", "--db", str(database)])
    assert result.exit_code == 0
    assert Store(database).get_task(f"{request.plan_id}:{request.task_id}").status == "ready"
    assert (
        Approvals(Store(database), index)
        .check(request.plan_id, request.task_id)
        .approval_generation
        == 1
    )


def test_cli_claim_renew_release_and_request_limits(reservation, tmp_path):
    _, database, _, _, request, _, _ = reservation
    runner = CliRunner()
    common = ["--db", str(database)]
    path = tmp_path / "claim.json"
    path.write_text(request.model_dump_json())
    result = runner.invoke(app, ["claim-task", str(path), *common])
    assert result.exit_code == 0, result.output
    raw = json.loads(result.output)
    cred = {
        "plan_id": request.plan_id,
        "task_id": request.task_id,
        "owner": request.owner,
        "lease_id": raw["lease"]["lease_id"],
        "generation": 1,
        "token": raw["token"],
    }
    path.write_text(json.dumps(cred | {"specification_sha256": request.specification_sha256}))
    assert runner.invoke(app, ["renew-task-lease", str(path), *common]).exit_code == 0
    view = runner.invoke(app, ["get-task-lease", request.plan_id, request.task_id, *common])
    assert view.exit_code == 0 and raw["token"] not in view.output
    path.write_text(json.dumps(cred))
    result = runner.invoke(app, ["release-task-lease", str(path), *common])
    assert result.exit_code == 0 and json.loads(result.output)["state"] == "released"
    path.write_text("x" * 8193)
    result = runner.invoke(app, ["claim-task", str(path), *common])
    assert result.exit_code == 1 and "budget" in result.output


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_coordination_opt_in_real_stdio_flow(reservation, mode):
    _, database, _, _, request, _, _ = reservation
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "worker_bees",
            "serve",
            "--db",
            str(database),
            "--enable-planning",
            "--enable-coordination",
        ],
    )

    async def scenario():
        async with Client(params, mode=mode, read_timeout_seconds=10) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 19
            assert next(t for t in tools if t.name == "get_task_lease").annotations.read_only_hint
            assert not next(t for t in tools if t.name == "claim_task").annotations.idempotent_hint
            result = await client.call_tool("claim_task", {"request": request.model_dump()})
            assert not result.is_error
            raw = result.structured_content
            assert raw["lease"]["state"] == "active"
            cred = {
                "plan_id": request.plan_id,
                "task_id": request.task_id,
                "owner": request.owner,
                "lease_id": raw["lease"]["lease_id"],
                "generation": 1,
                "token": raw["token"],
            }
            result = await client.call_tool(
                "renew_task_lease",
                {"request": cred | {"specification_sha256": request.specification_sha256}},
            )
            assert not result.is_error
            result = await client.call_tool(
                "get_task_lease", {"plan_id": request.plan_id, "task_id": request.task_id}
            )
            assert raw["token"] not in json.dumps(result.structured_content)
            invalid = await client.call_tool(
                "claim_task", {"request": request.model_dump() | {"approved": True}}
            )
            assert invalid.is_error
            result = await client.call_tool("release_task_lease", {"request": cred})
            assert not result.is_error and result.structured_content["state"] == "released"

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))


def test_planning_alone_cannot_mutate_leases_and_coordination_requires_it(reservation):
    _, database, _, _, _, _, _ = reservation
    with pytest.raises(ValueError, match="requires --enable-planning"):
        create_server(database, enable_coordination=True)

    async def scenario():
        async with Client(create_server(database, enable_planning=True)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert "get_task_lease" in tools
            assert not {"claim_task", "renew_task_lease", "release_task_lease"}.intersection(tools)

    asyncio.run(scenario())
