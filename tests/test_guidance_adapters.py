import asyncio
import hashlib
import json
import sys

import pytest
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from worker_bees.cli import app
from worker_bees.guidance import get_guidance
from worker_bees.mcp_server import create_server
from worker_bees.planning import Planning
from worker_bees.planning_models import PlanProposal
from worker_bees.storage import Store


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_guidance_prompts_intake_and_advisory_assessment_over_stdio(proposed, mode):
    _, database, index, raw = proposed
    Planning(Store(database, writable=True), index).submit(
        PlanProposal.model_validate(raw), actor="test"
    )
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
    )
    before = hashlib.sha256(database.read_bytes()).digest()

    async def scenario():
        async with Client(parameters, mode=mode, read_timeout_seconds=10) as client:
            prompts = await client.list_prompts()
            assert {p.name for p in prompts.prompts} == {"plan_feature", "review_task_satisfaction"}
            for name in ["plan_feature", "review_task_satisfaction"]:
                prompt = await client.get_prompt(name)
                assert index.snapshot.id in prompt.messages[0].content.text
                assert "does not supply model inference" in prompt.messages[0].content.text
            guidance = await client.call_tool("get_workflow_guidance", {"workflow": "spec_to_plan"})
            assert not guidance.is_error
            assert guidance.structured_content["guidance_version"] == "1.0"
            prepared = await client.call_tool(
                "prepare_specification", {"text": "# Scope\n\nReject negatives."}
            )
            assert not prepared.is_error
            assert prepared.structured_content["blocks"][1]["id"] == "B0002"
            literal_path = await client.call_tool("prepare_specification", {"text": "/etc/passwd"})
            assert literal_path.structured_content["text"] == "/etc/passwd"
            report = {
                "plan_id": raw["id"],
                "task_id": "T1",
                "implementation_revision": index.snapshot.commit,
            }
            assessed = await client.call_tool("assess_task", {"report": report})
            assert not assessed.is_error
            assert assessed.structured_content["recommendation"] == "needs_evidence"
            assert assessed.structured_content["task_status_changed"] is False
            invalid = await client.call_tool("assess_task", {"report": report | {"approved": True}})
            assert invalid.is_error
            invalid = await client.call_tool("get_workflow_guidance", {"workflow": "execute_all"})
            assert invalid.is_error

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))
    assert hashlib.sha256(database.read_bytes()).digest() == before


def test_guidance_cli_and_assessment_do_not_write(proposed, tmp_path):
    _, database, index, raw = proposed
    before = database.read_bytes()
    result = CliRunner().invoke(app, ["workflow-guidance", "spec_to_plan", "--db", str(database)])
    assert result.exit_code == 0
    assert json.loads(result.output) == get_guidance("spec_to_plan", index.snapshot).model_dump()
    assert database.read_bytes() == before
    Planning(Store(database, writable=True), index).submit(
        PlanProposal.model_validate(raw), actor="test"
    )
    report = tmp_path / "assessment.json"
    report.write_text(
        json.dumps(
            {
                "plan_id": raw["id"],
                "task_id": "T1",
                "implementation_revision": index.snapshot.commit,
            }
        )
    )
    result = CliRunner().invoke(app, ["assess-task", str(report), "--db", str(database)])
    assert result.exit_code == 0
    assert json.loads(result.output)["recommendation"] == "needs_evidence"


def test_default_server_does_not_expose_planning_prompts_or_helpers(proposed):
    _, database, _, _ = proposed

    async def scenario():
        async with Client(create_server(database)) as client:
            assert (await client.list_prompts()).prompts == []
            tools = await client.list_tools()
            assert len(tools.tools) == 6

    asyncio.run(scenario())
