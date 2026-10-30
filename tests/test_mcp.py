import asyncio
import hashlib
import sqlite3
import sys

import pytest
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from worker_bees.analysis import build_index
from worker_bees.cli import app
from worker_bees.mcp_server import create_server
from worker_bees.queries import Investigation
from worker_bees.storage import Store

TOOLS = {
    "get_index_status",
    "find_symbol",
    "get_symbol_context",
    "get_references",
    "analyze_impact",
    "get_unresolved_calls",
}


@pytest.fixture
def indexed(tmp_path, make_repo):
    root = make_repo(fixture=True)
    index = build_index(root)
    database = tmp_path / "state.db"
    Store(database, writable=True).save_index(index)
    return root, database, index


def run(scenario):
    return asyncio.run(asyncio.wait_for(scenario, timeout=30))


def test_discovery_and_results_match_core_queries_without_writes(indexed):
    _, database, index = indexed
    before = hashlib.sha256(database.read_bytes()).digest()

    async def scenario():
        async with Client(create_server(database), read_timeout_seconds=10) as client:
            discovery = await client.list_tools()
            assert {tool.name for tool in discovery.tools} == TOOLS
            for tool in discovery.tools:
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.destructive_hint is False
                assert tool.annotations.open_world_hint is False
                assert tool.output_schema
                assert not {"path", "database", "db", "snapshot", "repository"}.intersection(
                    tool.input_schema.get("properties", {})
                )
            expected = Investigation(index)
            queries = [
                ("get_index_status", {}, expected.status()),
                ("find_symbol", {"query": "total"}, expected.find("total")),
                (
                    "get_symbol_context",
                    {"symbol": "shop.pricing.total"},
                    expected.context("shop.pricing.total"),
                ),
                (
                    "get_references",
                    {"symbol": "shop.pricing.total"},
                    expected.references("shop.pricing.total"),
                ),
                (
                    "analyze_impact",
                    {"symbol": "shop.pricing.normalize"},
                    expected.impact("shop.pricing.normalize"),
                ),
                ("get_unresolved_calls", {}, expected.unresolved()),
            ]
            for name, arguments, result in queries:
                received = await client.call_tool(name, arguments)
                assert not received.is_error
                assert received.structured_content == result.model_dump(mode="json")

    run(scenario())
    assert hashlib.sha256(database.read_bytes()).digest() == before


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("find_symbol", {"query": ""}),
        ("find_symbol", {"query": "x" * 513}),
        ("find_symbol", {"query": "total", "limit": 201}),
        ("get_references", {"symbol": "shop.pricing.total", "limit": 0}),
        ("get_unresolved_calls", {"limit": True}),
        ("analyze_impact", {"symbol": "shop.pricing.total", "depth": 11}),
        ("get_symbol_context", {"symbol": "shop.pricing.total", "max_lines": 201}),
        ("get_symbol_context", {"symbol": "/etc/passwd"}),
        ("get_references", {"symbol": "does.not.exist"}),
    ],
)
def test_invalid_requests_are_errors_not_empty_success(indexed, name, arguments):
    _, database, _ = indexed

    async def scenario():
        async with Client(create_server(database)) as client:
            result = await client.call_tool(name, arguments)
            assert result.is_error
            assert not result.structured_content

    run(scenario())


def test_snapshot_pinning_and_no_cross_repository_selection(indexed, make_repo, tmp_path):
    root, database, old = indexed
    original = create_server(database)
    other = build_index(make_repo({"secret.py": "def foreign():\n    pass\n"}))
    Store(database, writable=True).save_index(other)
    (root / "shop" / "pricing.py").write_text("not valid Python")
    canary = tmp_path / "private.txt"
    canary.write_text("PRIVATE-CANARY-CONTENT")

    async def scenario():
        async with Client(original) as client:
            # Even unsolicited path/snapshot keys have no authority to change the pinned index.
            result = await client.call_tool("get_index_status", {"snapshot": other.snapshot.id})
            assert result.structured_content["snapshot"]["id"] == old.snapshot.id
            context = await client.call_tool("get_symbol_context", {"symbol": "shop.pricing.total"})
            assert "def total" in context.structured_content["source"]
            foreign = await client.call_tool("get_symbol_context", {"symbol": other.symbols[-1].id})
            assert foreign.is_error
            outside = await client.call_tool("get_symbol_context", {"symbol": str(canary)})
            assert outside.is_error
            assert "PRIVATE-CANARY-CONTENT" not in outside.model_dump_json()
        async with Client(create_server(database, old.snapshot.id)) as explicit:
            result = await explicit.call_tool("get_index_status")
            assert result.structured_content["snapshot"]["id"] == old.snapshot.id
        async with Client(create_server(database)) as restarted:
            result = await restarted.call_tool("get_index_status")
            assert result.structured_content["snapshot"]["id"] == other.snapshot.id

    run(scenario())


def test_partial_coverage_and_empty_results_preserve_warnings(make_repo, tmp_path):
    index = build_index(make_repo({"valid.py": "pass", "broken.py": "def bad(:"}))
    database = tmp_path / "state.db"
    Store(database, writable=True).save_index(index)

    async def scenario():
        async with Client(create_server(database)) as client:
            result = await client.call_tool("find_symbol", {"query": "missing"})
            assert not result.is_error
            assert result.structured_content["total"] == 0
            assert result.structured_content["snapshot"]["status"] == "partial"
            assert any(
                "Partial index" in warning for warning in result.structured_content["warnings"]
            )

    run(scenario())


def test_oversized_metadata_produces_bounded_error(make_repo, tmp_path):
    source = 'def enormous():\n    """' + "x" * 70_000 + '"""\n    pass\n'
    database = tmp_path / "state.db"
    Store(database, writable=True).save_index(build_index(make_repo({"a.py": source})))

    async def scenario():
        async with Client(create_server(database)) as client:
            result = await client.call_tool("find_symbol", {"query": "enormous"})
            assert result.is_error
            assert "budget" in result.model_dump_json()
            assert len(result.model_dump_json()) < 2000

    run(scenario())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_real_stdio_process_supports_current_and_legacy_clients(indexed, tmp_path, mode):
    _, database, index = indexed
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "worker_bees", "serve", "--db", str(database), "--snapshot", index.snapshot.id],
        cwd=tmp_path,
    )

    async def scenario():
        async with Client(parameters, mode=mode, read_timeout_seconds=10) as client:
            discovery = await client.list_tools()
            assert {tool.name for tool in discovery.tools} == TOOLS
            found = await client.call_tool("find_symbol", {"query": "total"})
            symbol = found.structured_content["items"][0]["id"]
            result = await client.call_tool("get_references", {"symbol": symbol})
            assert result.structured_content["total"] == 3
            assert result.structured_content["snapshot"]["id"] == index.snapshot.id

    run(scenario())


def test_startup_requires_existing_compatible_snapshot_and_keeps_stdout_clean(tmp_path):
    database = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        create_server(database)
    assert not database.exists()
    runner = CliRunner()
    result = runner.invoke(app, ["serve", "--db", str(database)])
    assert result.exit_code == 1 and result.stdout == ""
    Store(database, writable=True)
    with pytest.raises(ValueError, match="snapshot not found"):
        create_server(database)
