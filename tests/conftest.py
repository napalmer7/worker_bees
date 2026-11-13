import json
import shutil
import subprocess
from pathlib import Path

import pytest

from worker_bees.analysis import build_index
from worker_bees.approval_models import ApprovalRequest
from worker_bees.approvals import Approvals
from worker_bees.lease_models import ClaimRequest, LeaseContext
from worker_bees.lease_state import now_ms
from worker_bees.leases import Leases
from worker_bees.planning import Planning
from worker_bees.planning_models import PlanProposal
from worker_bees.storage import Store
from worker_bees.workspaces import Workspaces


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Worker Bees Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(root),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def make_repo(tmp_path):
    count = 0

    def make(files=None, *, fixture=False):
        nonlocal count
        count += 1
        root = tmp_path / f"repo-{count}"
        root.mkdir()
        if fixture:
            shutil.copytree(
                Path(__file__).parent / "fixtures" / "python_app", root, dirs_exist_ok=True
            )
        for name, content in (files or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content)
        git(root, "init", "-b", "main")
        git(root, "add", "--all")
        git(root, "commit", "-m", "fixture")
        return root

    return make


@pytest.fixture
def proposed(tmp_path, make_repo):
    root = make_repo(fixture=True)
    index = build_index(root)
    database = tmp_path / "state.db"
    store = Store(database, writable=True)
    store.save_index(index)
    raw = json.loads((Path(__file__).parents[1] / "examples" / "feature-plan.json").read_text())
    raw["snapshot_id"] = index.snapshot.id
    return root, database, index, raw


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
