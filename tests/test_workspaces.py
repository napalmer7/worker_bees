import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from conftest import git
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

import worker_bees.workspaces as module
from worker_bees.analysis import build_index
from worker_bees.approval_models import ApprovalRequest
from worker_bees.approvals import Approvals
from worker_bees.cli import app
from worker_bees.lease_models import ClaimRequest, LeaseContext
from worker_bees.lease_state import now_ms
from worker_bees.leases import Leases
from worker_bees.planning import Planning
from worker_bees.planning_models import PlanProposal
from worker_bees.storage import Store
from worker_bees.workspaces import Workspaces


@pytest.fixture
def assignment(proposed):
    root, database, index, raw = proposed

    def setup(extra=None):
        if extra:
            for name, content in extra.items():
                (root / name).write_bytes(content)
            git(root, "add", "--all")
            git(root, "commit", "-m", "workspace fixture")
        selected = build_index(root) if extra else index
        store = Store(database, writable=True)
        if extra:
            store.save_index(selected)
        raw["snapshot_id"] = selected.snapshot.id
        planner = Planning(store, selected)
        planner.submit(PlanProposal.model_validate(raw), actor="test")
        plan = planner.get(raw["id"]).plan
        approval = ApprovalRequest(
            plan_id=raw["id"],
            task_id="T1",
            submission_sha256=plan.submission_sha256,
            specification_sha256=plan.specification_sha256,
            expected_generation=0,
            action="approve",
            reason="Reviewed workspace preparation scope.",
        )
        Approvals(store, selected).decide(approval)
        clock = [now_ms()]
        leases = Leases(store, selected, clock=lambda: clock[0])
        claim = leases.claim(
            ClaimRequest(
                plan_id=raw["id"],
                task_id="T1",
                owner="bee-one",
                submission_sha256=plan.submission_sha256,
                specification_sha256=plan.specification_sha256,
                expected_approval_generation=1,
                expected_lease_generation=0,
            )
        )
        request = LeaseContext(
            plan_id=raw["id"],
            task_id="T1",
            owner="bee-one",
            lease_id=claim.lease.lease_id,
            generation=claim.lease.generation,
            token=claim.token,
            specification_sha256=plan.specification_sha256,
        )
        return (
            root,
            database,
            selected,
            Workspaces(store, selected, clock=lambda: clock[0]),
            request,
            approval,
            clock,
        )

    return setup


def test_preparation_materializes_exact_revision_and_preserves_source_and_lease(
    assignment, tmp_path
):
    root, database, index, workspaces, request, _, _ = assignment(
        {"exact.bin": b"raw\r\n\x00bytes"}
    )
    (root / "shop" / "pricing.py").write_text("# user draft\n")
    before_refs = git(root, "show-ref")
    lease_before = Leases(Store(database), index).get(request.plan_id, request.task_id)
    destination = tmp_path / "workspace with spaces"
    manifest = workspaces.prepare(request, destination)
    assert manifest.status == "prepared" and not manifest.execution_available
    assert git(destination / "checkout", "rev-parse", "HEAD") == index.snapshot.commit
    assert git(destination / "checkout", "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert (destination / "checkout" / "exact.bin").read_bytes() == b"raw\r\n\x00bytes"
    assert (root / "shop" / "pricing.py").read_text() == "# user draft\n"
    assert git(root, "show-ref") == before_refs
    brief = json.loads(Path(manifest.brief_file).read_text())
    assert brief["task"]["exclusions"] and brief["task"]["shared_contracts"]
    assert len(brief["task"]["acceptance_criteria"]) == 2
    assert (
        brief["submission_sha256"]
        == Approvals(Store(database), index)
        .check(request.plan_id, request.task_id)
        .submission_sha256
    )
    assert brief["reservation"]["generation"] == request.generation
    assert request.token not in json.dumps(brief)
    assert request.token not in (destination / "manifest.json").read_text()
    assert (
        Leases(Store(database), index).get(request.plan_id, request.task_id).expires_at_ms
        == lease_before.expires_at_ms
    )
    assert Store(database).get_task(f"{request.plan_id}:{request.task_id}").status == "ready"
    assert Store(database).events()[-1].kind == "workspace_prepared"


def test_brief_is_readonly_scoped_and_rejects_stale_credentials(assignment):
    _, database, index, workspaces, request, _, clock = assignment()
    readonly = Workspaces(Store(database), index, clock=lambda: clock[0])
    before = database.read_bytes()
    brief = readonly.brief(request)
    assert brief.task.id == "T1" and brief.reservation.state == "active"
    assert brief.specification and brief.constraints and brief.requirements
    assert request.token not in brief.model_dump_json()
    for update in [{"token": "0" * 64}, {"generation": 2}, {"task_id": "T2"}]:
        with pytest.raises(ValueError, match="credentials"):
            readonly.brief(request.model_copy(update=update))
    with pytest.raises(ValueError, match="specification_changed"):
        readonly.brief(request.model_copy(update={"specification_sha256": "0" * 64}))
    assert database.read_bytes() == before
    Leases(workspaces.store, index).release(request)
    with pytest.raises(ValueError, match="released"):
        readonly.brief(request)


def test_existing_paths_and_in_source_destinations_are_never_overwritten(assignment, tmp_path):
    root, _, _, workspaces, request, _, _ = assignment()
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        workspaces.prepare(request, destination)
    assert sentinel.read_text() == "preserve"
    with pytest.raises(ValueError, match="outside"):
        workspaces.prepare(request, root / "nested")
    if os.name != "nt":
        alias = tmp_path / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        with pytest.raises(FileExistsError):
            workspaces.prepare(request, alias)
    assert not (root / "nested").exists()


def test_hooks_filters_and_inherited_git_environment_are_not_executed(
    assignment, tmp_path, monkeypatch
):
    root, _, index, workspaces, request, _, _ = assignment(
        {".gitattributes": b"*.py filter=trap\n"}
    )
    sentinel = tmp_path / "executed"
    hook = root / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\ntouch " + shlex.quote(str(sentinel)) + "\n")
    hook.chmod(0o755)
    trap = "touch " + shlex.quote(str(sentinel))
    git(root, "config", "filter.trap.smudge", trap)
    git(root, "config", "filter.trap.process", trap)
    git(root, "config", "filter.trap.required", "true")
    git(root, "config", "core.fsmonitor", trap)
    monkeypatch.setenv("GIT_DIR", "/does/not/exist")
    destination = tmp_path / "safe"
    manifest = workspaces.prepare(request, destination)
    assert manifest.status == "prepared"
    assert not sentinel.exists()
    assert (
        (destination / "checkout" / "shop" / "pricing.py").read_text().startswith("def normalize")
    )
    assert manifest.base_revision == index.snapshot.commit


@pytest.mark.parametrize(
    "output",
    [
        b"120000 blob abc 3\tlink\x00",
        b"160000 commit abc -\tsubmodule\x00",
        b"100644 blob abc 3\t../outside\x00",
        b"100644 blob abc 3\t.git/config\x00",
        b"100644 blob abc 3\ta\\b\x00",
        b"100644 blob abc 3\ta:b\x00",
        b"100644 blob abc 3\tA/x\x00100644 blob abc 3\ta/y\x00",
    ],
)
def test_unsupported_or_unsafe_entries_are_rejected(output, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "git", lambda *args: output)
    with pytest.raises(ValueError):
        module.entries(tmp_path, "revision")


@pytest.mark.parametrize(
    "budget,value", [("MAX_FILES", 1), ("MAX_FILE_BYTES", 1), ("MAX_TOTAL_BYTES", 1)]
)
def test_limits_fail_before_creating_a_workspace(assignment, tmp_path, monkeypatch, budget, value):
    _, _, _, workspaces, request, _, _ = assignment()
    monkeypatch.setattr(module, budget, value)
    destination = tmp_path / "too-large"
    with pytest.raises(ValueError, match="budget"):
        workspaces.prepare(request, destination)
    assert not destination.exists()


def test_expiry_after_git_work_retains_a_failed_attempt_and_no_event(
    assignment, tmp_path, monkeypatch
):
    _, database, _, workspaces, request, _, clock = assignment()
    original = module.git

    def expire_after_add(root, *args):
        result = original(root, *args)
        if args[:2] == ("worktree", "add"):
            clock[0] += 120_000
        return result

    monkeypatch.setattr(module, "git", expire_after_add)
    destination = tmp_path / "expired"
    before = database.read_bytes()
    with pytest.raises(ValueError, match="retained attempt"):
        workspaces.prepare(request, destination)
    assert json.loads((destination / "manifest.json").read_text())["status"] == "failed"
    assert not (destination / "task-brief.json").exists()
    assert database.read_bytes() == before


def test_event_failure_marks_manifest_failed_and_preserves_attempt(assignment, tmp_path):
    _, database, _, workspaces, request, _, _ = assignment()
    with workspaces.store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'event failure'); END"
        )
    before = database.read_bytes()
    destination = tmp_path / "failure"
    with pytest.raises(ValueError, match="retained attempt"):
        workspaces.prepare(request, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert (destination / "checkout" / "shop" / "pricing.py").is_file()
    assert database.read_bytes() == before


def test_readonly_preparation_and_revoked_lease_write_nothing(assignment, tmp_path):
    _, database, index, workspaces, request, approval, _ = assignment()
    with pytest.raises(ValueError, match="write access"):
        Workspaces(Store(database), index).prepare(request, tmp_path / "read-only")
    Approvals(workspaces.store, index).decide(
        approval.model_copy(update={"action": "revoke", "expected_generation": 1})
    )
    with pytest.raises(ValueError, match="active lease"):
        workspaces.prepare(request, tmp_path / "revoked")
    assert not (tmp_path / "revoked").exists()


def test_operator_cli_preparation_and_brief(assignment, tmp_path):
    _, database, _, _, request, _, _ = assignment()
    runner = CliRunner()
    path = tmp_path / "context.json"
    path.write_text(request.model_dump_json())
    common = ["--db", str(database)]
    result = runner.invoke(app, ["get-task-brief", str(path), *common])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["task"]["id"] == "T1"
    result = runner.invoke(
        app, ["prepare-workspace", str(path), str(tmp_path / "cli-work"), *common]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "prepared"
    assert request.token not in result.output


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_exports_briefs_but_has_no_workspace_mutation_tool(assignment, mode):
    _, database, _, _, request, _, _ = assignment()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "worker_bees",
            "serve",
            "--db",
            str(database),
            "--enable-planning",
        ],
    )
    before = database.read_bytes()

    async def scenario():
        async with Client(parameters, mode=mode, read_timeout_seconds=10) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 17
            assert "prepare_workspace" not in {tool.name for tool in tools}
            assert next(t for t in tools if t.name == "get_task_brief").annotations.read_only_hint
            result = await client.call_tool("get_task_brief", {"request": request.model_dump()})
            assert not result.is_error and result.structured_content["task"]["id"] == "T1"
            assert request.token not in json.dumps(result.structured_content)
            invalid = await client.call_tool(
                "get_task_brief",
                {"request": request.model_dump() | {"destination": "/tmp/arbitrary"}},
            )
            assert invalid.is_error

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))
    assert database.read_bytes() == before


@pytest.mark.parametrize("key", ["extensions.partialClone", "remote.origin.promisor"])
def test_partial_clones_are_rejected_before_workspace_registration(assignment, tmp_path, key):
    root, _, _, workspaces, request, _, _ = assignment()
    git(root, "config", key, "true" if key.endswith("promisor") else "origin")
    destination = tmp_path / "partial-clone"
    with pytest.raises(ValueError, match="partial/promisor clones"):
        workspaces.prepare(request, destination)
    assert not destination.exists()
