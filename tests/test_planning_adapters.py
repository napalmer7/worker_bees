import asyncio
import hashlib
import json
import sqlite3
import sys

import pytest
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from worker_bees.cli import app
from worker_bees.mcp_server import create_server
from worker_bees.storage import Store

PLANNING_TOOLS = {"validate_plan", "submit_plan", "get_plan", "list_tasks", "get_task"}
runner = CliRunner()


def test_cli_validation_submission_and_queries(proposed, tmp_path):
    _, database, index, raw = proposed
    proposal = tmp_path / "plan.json"
    proposal.write_text(json.dumps(raw))
    common = ["--db", str(database), "--snapshot", index.snapshot.id]
    before = database.read_bytes()
    result = runner.invoke(app, ["validate-plan", str(proposal), *common])
    assert result.exit_code == 0 and json.loads(result.output)["valid"]
    assert database.read_bytes() == before
    submitted = runner.invoke(app, ["submit-plan", str(proposal), *common])
    assert submitted.exit_code == 0, submitted.output
    assert json.loads(submitted.output)["created"]
    retry = runner.invoke(app, ["submit-plan", str(proposal), *common])
    assert retry.exit_code == 0 and not json.loads(retry.output)["created"]
    changed_spec = tmp_path / "feature.txt"
    changed_spec.write_text(raw["specification"] + " More requirements.")
    view = runner.invoke(app, ["get-plan", raw["id"], "--spec-file", str(changed_spec), *common])
    assert view.exit_code == 0 and json.loads(view.output)["needs_reassessment"]
    result = runner.invoke(app, ["list-tasks", raw["id"], "--limit", "1", *common])
    assert result.exit_code == 0 and json.loads(result.output)["truncated"]
    result = runner.invoke(app, ["get-task", raw["id"], "T2", *common])
    assert result.exit_code == 0
    assert json.loads(result.output)["task"]["depends_on"] == [raw["id"] + ":T1"]
    schema = runner.invoke(app, ["plan-schema"])
    assert schema.exit_code == 0
    assert "specification" in json.loads(schema.output)["properties"]


def test_cli_invalid_plan_exit_codes_and_input_limits(proposed, tmp_path):
    _, database, _, raw = proposed
    proposal = tmp_path / "invalid.json"
    raw["tasks"][0]["depends_on"] = ["T2"]
    proposal.write_text(json.dumps(raw))
    common = ["--db", str(database)]
    result = runner.invoke(app, ["validate-plan", str(proposal), *common])
    assert result.exit_code == 2 and not json.loads(result.output)["valid"]
    result = runner.invoke(app, ["submit-plan", str(proposal), *common])
    assert result.exit_code == 1
    proposal.write_text("x" * 50_000)
    result = runner.invoke(app, ["submit-plan", str(proposal), *common])
    assert result.exit_code == 1 and "budget" in result.output
    with Store(database).connect() as connection:
        assert connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_real_stdio_opt_in_planning_workflow(proposed, mode, tmp_path):
    _, database, index, raw = proposed
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "worker_bees",
            "serve",
            "--db",
            str(database),
            "--snapshot",
            index.snapshot.id,
            "--enable-planning",
        ],
        cwd=tmp_path,
    )

    async def scenario():
        async with Client(parameters, mode=mode, read_timeout_seconds=10) as client:
            discovery = await client.list_tools()
            assert len(discovery.tools) == 11
            assert PLANNING_TOOLS.issubset({tool.name for tool in discovery.tools})
            annotations = {tool.name: tool.annotations for tool in discovery.tools}
            assert annotations["submit_plan"].read_only_hint is False
            assert annotations["submit_plan"].idempotent_hint is True
            assert annotations["get_plan"].read_only_hint is True
            before = hashlib.sha256(database.read_bytes()).digest()
            result = await client.call_tool("validate_plan", {"proposal": raw})
            assert not result.is_error and result.structured_content["valid"]
            assert hashlib.sha256(database.read_bytes()).digest() == before
            submitted = await client.call_tool("submit_plan", {"proposal": raw})
            assert not submitted.is_error and submitted.structured_content["created"]
            retry = await client.call_tool("submit_plan", {"proposal": raw})
            assert not retry.is_error and not retry.structured_content["created"]
            plan = await client.call_tool("get_plan", {"plan_id": raw["id"]})
            assert not plan.is_error and plan.structured_content["plan"]["status"] == "draft"
            tasks = await client.call_tool("list_tasks", {"plan_id": raw["id"]})
            assert tasks.structured_content["total"] == 2
            task = await client.call_tool("get_task", {"plan_id": raw["id"], "task_id": "T2"})
            assert task.structured_content["task"]["owner"] is None
            assert task.structured_content["task"]["depends_on"] == [raw["id"] + ":T1"]
            raw["tasks"][0]["depends_on"] = ["T2"]
            result = await client.call_tool("validate_plan", {"proposal": raw})
            assert not result.is_error and not result.structured_content["valid"]
            denied = await client.call_tool("submit_plan", {"proposal": raw})
            assert denied.is_error

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))
    assert len(Store(database).events()) == 4
    assert all(event.actor == "mcp_client" for event in Store(database).events()[1:])


def test_default_server_cannot_submit_and_opt_in_cannot_change_snapshot(proposed):
    _, database, index, raw = proposed

    async def scenario():
        async with Client(create_server(database)) as client:
            tools = await client.list_tools()
            assert not PLANNING_TOOLS.intersection({tool.name for tool in tools.tools})
        async with Client(create_server(database, enable_planning=True)) as client:
            raw["snapshot_id"] = "foreign"
            result = await client.call_tool("submit_plan", {"proposal": raw})
            assert result.is_error
            raw["snapshot_id"] = index.snapshot.id
            raw["owner"] = "coordinator"
            result = await client.call_tool("submit_plan", {"proposal": raw})
            assert result.is_error

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))
    with Store(database).connect() as connection:
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_existing_v2_database_requires_explicit_migration(proposed):
    _, database, index, raw = proposed
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE plans")
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(ValueError, match="migration"):
        create_server(database, enable_planning=True)
    result = runner.invoke(app, ["init", "--db", str(database)])
    assert result.exit_code == 0
    assert Store(database).load_index().snapshot.id == index.snapshot.id
    with Store(database).connect() as connection:
        assert connection.execute("SELECT count(*) FROM plans").fetchone()[0] == 0


def test_v2_upgrade_preserves_legacy_task_payloads(proposed):
    _, database, index, _ = proposed
    legacy = {
        "schema_version": 1,
        "id": "legacy",
        "objective": "Old work",
        "acceptance_criteria": ["A"],
        "validation_needs": ["V"],
        "base_revision": index.snapshot.commit,
        "status": "draft",
        "owner": None,
    }
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE plans")
        connection.execute("PRAGMA user_version = 2")
        connection.execute("INSERT INTO tasks VALUES (?, ?)", ("legacy", json.dumps(legacy)))
    upgraded = Store(database, writable=True)
    task = upgraded.get_task("legacy")
    assert task.objective == "Old work" and task.plan_id is None and task.depends_on == []
    assert upgraded.load_index().snapshot.id == index.snapshot.id
    assert len(upgraded.events()) == 1
