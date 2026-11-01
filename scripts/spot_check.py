"""Exercise the installed CLI and a real stdio MCP subprocess against known source."""

import argparse
import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import Client, StdioServerParameters

PROJECT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "get_index_status",
    "find_symbol",
    "get_symbol_context",
    "get_references",
    "analyze_impact",
    "get_unresolved_calls",
}
PLANNING_TOOLS = {"validate_plan", "submit_plan", "get_plan", "list_tasks", "get_task"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def cli(database, *arguments):
    completed = subprocess.run(
        [sys.executable, "-m", "worker_bees", *arguments, "--db", str(database)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        raise RuntimeError(f"CLI failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def git(repository, *arguments):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Worker Bees Demo",
            "-c",
            "user.email=demo@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={repository.parent / 'empty-hooks'}",
            "-C",
            str(repository),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


async def check_mcp(database, snapshot, symbol):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "worker_bees", "serve", "--db", str(database), "--snapshot", snapshot],
    )
    async with Client(parameters, read_timeout_seconds=10) as client:
        tools = await client.list_tools()
        require({tool.name for tool in tools.tools} == EXPECTED_TOOLS, "Unexpected MCP tool list")
        print("PASS  MCP subprocess discovers all six read-only tools")
        status = await client.call_tool("get_index_status")
        require(not status.is_error, "MCP status failed")
        require(status.structured_content["snapshot"]["id"] == snapshot, "Wrong MCP snapshot")
        references = await client.call_tool("get_references", {"symbol": symbol})
        require(not references.is_error, "MCP references failed")
        require(references.structured_content["total"] == 3, "Expected three MCP potential callers")
        print("PASS  MCP references agree with the CLI on the same snapshot")
        failure = await client.call_tool("get_symbol_context", {"symbol": "not.a.symbol"})
        require(failure.is_error, "Invalid MCP lookup must report an error")
        print("PASS  Invalid MCP lookup reports an error, not a clean empty result")


async def check_planning_mcp(database, snapshot, proposal):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "worker_bees",
            "serve",
            "--db",
            str(database),
            "--snapshot",
            snapshot,
            "--enable-planning",
        ],
    )
    async with Client(parameters, read_timeout_seconds=10) as client:
        discovery = await client.list_tools()
        require(
            {tool.name for tool in discovery.tools} == EXPECTED_TOOLS | PLANNING_TOOLS,
            "Unexpected opt-in planning tools",
        )
        retry = await client.call_tool("submit_plan", {"proposal": proposal})
        require(
            not retry.is_error and not retry.structured_content["created"],
            "MCP retry should preserve the CLI's existing draft",
        )
        print("PASS  Opt-in MCP planning discovers eleven tools and preserves the same draft")
        cyclic = json.loads(json.dumps(proposal))
        cyclic["tasks"][0]["depends_on"] = ["T2"]
        report = await client.call_tool("validate_plan", {"proposal": cyclic})
        require(
            not report.is_error and not report.structured_content["valid"],
            "A cyclic dependency graph must fail validation",
        )
        denied = await client.call_tool("submit_plan", {"proposal": cyclic})
        require(denied.is_error, "A cyclic plan must not be persisted")
        print("PASS  Cyclic task dependencies fail validation and cannot be submitted")


def check_planning(database, snapshot, workspace):
    proposal = json.loads((PROJECT / "examples" / "feature-plan.json").read_text())
    proposal["snapshot_id"] = snapshot
    path = workspace / "feature-plan.json"
    path.write_text(json.dumps(proposal, indent=2))
    report = cli(database, "validate-plan", str(path))
    require(
        report["valid"] and report["ordered_task_ids"] == ["T1", "T2"],
        "Example plan must validate in dependency order",
    )
    require(report["assessment"] == "unverified_proposal", "A plan must not imply approval")
    first = cli(database, "submit-plan", str(path))
    retry = cli(database, "submit-plan", str(path))
    require(first["created"] and not retry["created"], "Identical retries must not duplicate work")
    tasks = cli(database, "list-tasks", proposal["id"])
    require(tasks["total"] == 2, "Expected two tracked tasks")
    require(
        all(task["status"] == "draft" and task["owner"] is None for task in tasks["tasks"]),
        "Tasks must remain unowned drafts",
    )
    require(
        tasks["tasks"][1]["depends_on"] == [proposal["id"] + ":T1"],
        "Second task must depend on the first",
    )
    print(
        "PASS  Plan validation and idempotent submission create two dependent, unowned draft tasks"
    )
    updated = workspace / "updated-spec.txt"
    updated.write_text(proposal["specification"] + " Also support refunds.")
    view = cli(database, "get-plan", proposal["id"], "--spec-file", str(updated))
    require(
        view["needs_reassessment"] and "specification_changed" in view["reasons"],
        "Changed specifications must require reassessment",
    )
    print("PASS  A changed feature specification flags the stored plan for reassessment")
    asyncio.run(asyncio.wait_for(check_planning_mcp(database, snapshot, proposal), timeout=30))


def command_text(arguments):
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def spot_check(workspace, keep=False, planning=False):
    repository = workspace / "repository"
    shutil.copytree(PROJECT / "tests" / "fixtures" / "python_app", repository)
    (workspace / "empty-hooks").mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "add", "--all")
    git(repository, "commit", "-m", "Create Worker Bees spot-check fixture")
    database = workspace / "state.db"
    status = cli(database, "index", str(repository))
    require(status["snapshot"]["status"] == "complete", "Fixture index should be complete")
    require(status["snapshot"]["indexed_files"] == 3, "Expected three Python files")
    snapshot = status["snapshot"]["id"]
    print("PASS  Three committed Python files indexed without executing the application")
    found = cli(database, "find", "shop.pricing.total")
    require(found["total"] == 1, "Expected one total function")
    symbol = found["items"][0]["id"]
    references = cli(database, "references", symbol)
    require(references["total"] == 3, "Expected three potential callers of total")
    require(
        all(item["path"] == "shop/service.py" for item in references["items"]),
        "Expected callers in service.py",
    )
    print("PASS  Alias and relative-import analysis finds three potential callers")
    unknowns = cli(database, "unresolved")
    expressions = {item["expression"] for item in unknowns["items"]}
    require({"handler", "self.quote"}.issubset(expressions), "Dynamic calls must stay unknown")
    print("PASS  Dynamic handler and instance-method calls remain explicitly unresolved")
    before = cli(database, "context", symbol)
    source = repository / "shop" / "pricing.py"
    committed = source.read_bytes()
    try:
        source.write_text("# An uncommitted edit that must not affect the snapshot\n")
        after = cli(database, "context", symbol)
        require(before["source"] == after["source"], "Context changed with the working tree")
    finally:
        source.write_bytes(committed)
    print("PASS  Stored source context survives uncommitted working-tree edits")
    affected = cli(database, "impact", "shop.pricing.normalize", "--depth", "2")
    require(affected["total"] == 4, "Expected four potential callers within two levels")
    print("PASS  Bounded impact query returns the four expected affected symbols")
    asyncio.run(asyncio.wait_for(check_mcp(database, snapshot, symbol), timeout=30))
    if planning:
        check_planning(database, snapshot, workspace)
    print(f"\nCommit:   {status['snapshot']['commit']}")
    print(f"Snapshot: {snapshot}")
    if not keep:
        return
    print("\nManual inspection commands:")
    for arguments in [
        ["worker-bees", "status", "--db", str(database)],
        ["worker-bees", "references", "shop.pricing.total", "--db", str(database)],
        ["worker-bees", "unresolved", "--db", str(database)],
        ["worker-bees", "serve", "--db", str(database), "--snapshot", snapshot],
    ]:
        print(command_text(["uv", "run", *arguments]))
    if planning:
        for arguments in [
            ["get-plan", "negative-price-validation"],
            ["list-tasks", "negative-price-validation"],
        ]:
            print(command_text(["uv", "run", "worker-bees", *arguments, "--db", str(database)]))
        print(f"Editable plan example: {workspace / 'feature-plan.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", type=Path, help="Keep the demo at this new directory; refuses overwrite"
    )
    parser.add_argument("--planning", action="store_true", help="Also test opt-in feature planning")
    arguments = parser.parse_args()
    try:
        if arguments.keep:
            workspace = arguments.keep.expanduser().resolve()
            workspace.mkdir(parents=True, exist_ok=False)
            print(f"Demo directory: {workspace}\n")
            spot_check(workspace, keep=True, planning=arguments.planning)
        else:
            with tempfile.TemporaryDirectory(prefix="worker-bees-spot-check-") as temporary:
                spot_check(Path(temporary).resolve(), planning=arguments.planning)
            print("\nTemporary demo removed. Use --keep NEW_DIRECTORY to inspect it afterward.")
    except (RuntimeError, OSError, subprocess.SubprocessError, TimeoutError) as error:
        print(f"FAIL  {error}", file=sys.stderr)
        return 1
    print("\nAll spot checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
