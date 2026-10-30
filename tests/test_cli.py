import json

from typer.testing import CliRunner

from worker_bees.cli import app

runner = CliRunner()


def test_cli_index_query_and_context_after_working_tree_changes(tmp_path, make_repo):
    root = make_repo(fixture=True)
    database = str(tmp_path / "state.db")
    result = runner.invoke(app, ["index", str(root), "--db", database])
    assert result.exit_code == 0, result.output
    status = json.loads(result.output)
    snapshot = status["snapshot"]["id"]
    result = runner.invoke(app, ["find", "total", "--db", database, "--snapshot", snapshot])
    assert result.exit_code == 0, result.output
    symbol_id = json.loads(result.output)["items"][0]["id"]
    result = runner.invoke(app, ["references", symbol_id, "--db", database])
    assert result.exit_code == 0 and json.loads(result.output)["total"] == 3
    (root / "shop" / "pricing.py").write_text("broken(")
    result = runner.invoke(app, ["context", symbol_id, "--db", database])
    assert result.exit_code == 0
    assert "def total" in json.loads(result.output)["source"]
    for command in (["status"], ["unresolved"], ["impact", symbol_id]):
        result = runner.invoke(app, [*command, "--db", database])
        assert result.exit_code == 0, result.output


def test_cli_failure_is_not_empty_success_or_database_creation(tmp_path):
    database = tmp_path / "missing.db"
    result = runner.invoke(app, ["find", "missing", "--db", str(database)])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert not database.exists()


def test_cli_rejects_missing_snapshots_and_invalid_query_limits(tmp_path, make_repo):
    database = str(tmp_path / "state.db")
    root = make_repo(fixture=True)
    assert runner.invoke(app, ["index", str(root), "--db", database]).exit_code == 0
    result = runner.invoke(app, ["status", "--snapshot", "missing", "--db", database])
    assert result.exit_code == 1
    result = runner.invoke(app, ["find", "total", "--limit", "0", "--db", database])
    assert result.exit_code == 1
