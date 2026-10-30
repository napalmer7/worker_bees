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
