import json
import shutil
import subprocess
from pathlib import Path

import pytest

from worker_bees.analysis import build_index
from worker_bees.storage import Store


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
