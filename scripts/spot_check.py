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


def command_text(arguments):
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def spot_check(workspace, keep=False):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", type=Path, help="Keep the demo at this new directory; refuses overwrite"
    )
    arguments = parser.parse_args()
    try:
        if arguments.keep:
            workspace = arguments.keep.expanduser().resolve()
            workspace.mkdir(parents=True, exist_ok=False)
            print(f"Demo directory: {workspace}\n")
            spot_check(workspace, keep=True)
        else:
            with tempfile.TemporaryDirectory(prefix="worker-bees-spot-check-") as temporary:
                spot_check(Path(temporary).resolve())
            print("\nTemporary demo removed. Use --keep NEW_DIRECTORY to inspect it afterward.")
    except (RuntimeError, OSError, subprocess.SubprocessError, TimeoutError) as error:
        print(f"FAIL  {error}", file=sys.stderr)
        return 1
    print("\nAll spot checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
