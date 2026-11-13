import subprocess
import sys
from pathlib import Path


def test_spot_check_runs_cli_and_stdio_and_keeps_demo(tmp_path):
    project = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "demo with spaces"
    command = [sys.executable, str(project / "scripts" / "spot_check.py"), "--keep", str(workspace)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All spot checks passed." in result.stdout
    assert "MCP subprocess discovers all six" in result.stdout
    assert (workspace / "state.db").is_file()
    assert (
        (workspace / "repository" / "shop" / "pricing.py").read_text().startswith("def normalize")
    )
    again = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert again.returncode == 1
    assert (workspace / "state.db").is_file()


def test_default_spot_check_cleans_up_without_printing_commands_to_deleted_state():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project / "scripts" / "spot_check.py")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All spot checks passed." in result.stdout
    assert "Temporary demo removed." in result.stdout
    assert "Manual inspection commands" not in result.stdout


def test_planning_spot_check_keeps_an_editable_plan(tmp_path):
    project = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "planning demo"
    result = subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "spot_check.py"),
            "--planning",
            "--keep",
            str(workspace),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unowned draft tasks" in result.stdout
    assert "Cyclic task dependencies" in result.stdout
    assert (workspace / "feature-plan.json").is_file()


def test_coordination_spot_check_exercises_real_lease_tools():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project / "scripts" / "spot_check.py"), "--coordination"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "claims, heartbeats, and releases" in result.stdout
    assert "All spot checks passed." in result.stdout


def test_workspace_spot_check_keeps_a_prepared_worktree_and_brief(tmp_path):
    import json

    project = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace demo"
    result = subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "spot_check.py"),
            "--workspaces",
            "--keep",
            str(workspace),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "detached workspace and lease-bound task brief" in result.stdout
    manifest = json.loads((workspace / "task-workspace" / "manifest.json").read_text())
    assert manifest["status"] == "prepared" and not manifest["execution_available"]
    assert (workspace / "task-workspace" / "checkout" / "shop" / "pricing.py").is_file()
    assert (workspace / "task-workspace" / "task-brief.json").is_file()


def test_worker_spot_check_runs_deterministic_profile_and_retains_logs(tmp_path):
    import json

    project = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "worker demo"
    result = subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "spot_check.py"),
            "--workers",
            "--keep",
            str(workspace),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Deterministic operator worker exits without completing the task" in result.stdout
    runs = list((workspace / "task-workspace" / "runs").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "stdout.log").read_text().strip() == "Demo task: T1"
    envelope = json.loads((runs[0] / "input.json").read_text())
    assert envelope["protocol_version"] == 1
    assert "token" not in envelope
