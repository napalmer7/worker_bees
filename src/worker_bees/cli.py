"""Local CLI. JSON output is suitable for scripts and future adapter comparisons."""

import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from worker_bees.analysis import build_index
from worker_bees.planning import MAX_PROPOSAL_BYTES, Planning
from worker_bees.planning_models import PlanProposal
from worker_bees.queries import Investigation
from worker_bees.storage import Store

app = typer.Typer(no_args_is_help=True, help="Investigate committed Python code with evidence.")
DB = Annotated[Path, typer.Option("--db", help="Local state database.")]
SnapshotID = Annotated[
    str | None, typer.Option("--snapshot", help="Default: latest indexed snapshot.")
]
DEFAULT_DB = Path(".worker-bees/state.db")


@contextmanager
def errors() -> Iterator[None]:
    try:
        yield
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error


def output(model: BaseModel) -> None:
    typer.echo(model.model_dump_json(indent=2))


def investigation(db: Path, snapshot: str | None) -> Investigation:
    return Investigation(Store(db).load_index(snapshot))


@app.command()
def init(db: DB = DEFAULT_DB) -> None:
    """Create or migrate the local database."""
    with errors():
        store = Store(db, writable=True)
        typer.echo(f"Initialized {store.path}")


@app.command(name="index")
def index_repository(
    repository: Path,
    revision: str = "HEAD",
    db: DB = DEFAULT_DB,
) -> None:
    """Index committed Python blobs; working-tree edits are excluded."""
    with errors():
        store = Store(db, writable=True)
        index = build_index(repository, revision)
        store.save_index(index)
        output(Investigation(index).status())


@app.command()
def status(db: DB = DEFAULT_DB, snapshot: SnapshotID = None) -> None:
    """Show parser coverage, issues, revision, and call counts."""
    with errors():
        output(investigation(db, snapshot).status())


@app.command()
def find(query: str, db: DB = DEFAULT_DB, snapshot: SnapshotID = None, limit: int = 50) -> None:
    """Find symbols by literal qualified-name substring."""
    with errors():
        output(investigation(db, snapshot).find(query, limit))


@app.command()
def references(
    symbol: str,
    db: DB = DEFAULT_DB,
    snapshot: SnapshotID = None,
    limit: int = 50,
) -> None:
    """Show supported static potential callers (not every use of a symbol)."""
    with errors():
        output(investigation(db, snapshot).references(symbol, limit))


@app.command()
def unresolved(db: DB = DEFAULT_DB, snapshot: SnapshotID = None, limit: int = 50) -> None:
    """Inspect call sites that cannot be resolved safely."""
    with errors():
        output(investigation(db, snapshot).unresolved(limit))


@app.command()
def context(
    symbol: str,
    db: DB = DEFAULT_DB,
    snapshot: SnapshotID = None,
    max_lines: int = 80,
) -> None:
    """Read source from the persisted snapshot, never the current filesystem."""
    with errors():
        output(investigation(db, snapshot).context(symbol, max_lines))


@app.command()
def impact(
    symbol: str,
    db: DB = DEFAULT_DB,
    snapshot: SnapshotID = None,
    depth: int = 2,
    limit: int = 50,
) -> None:
    """Traverse supported potential callers to a bounded depth."""
    with errors():
        output(investigation(db, snapshot).impact(symbol, depth, limit))


@app.command()
def serve(db: DB = DEFAULT_DB, snapshot: SnapshotID = None, enable_planning: bool = False) -> None:
    """Serve pinned MCP investigation; opt in to draft planning with --enable-planning."""
    from worker_bees.mcp_server import serve as serve_mcp

    with errors():
        serve_mcp(db, snapshot, enable_planning=enable_planning)


def read_proposal(path: Path) -> PlanProposal:
    with path.open("rb") as source:
        data = source.read(MAX_PROPOSAL_BYTES + 1)
    if len(data) > MAX_PROPOSAL_BYTES:
        raise ValueError("proposal file exceeds the 48 KiB input budget")
    return PlanProposal.model_validate_json(data)


def planning(db: Path, snapshot: str | None, *, writable: bool = False) -> Planning:
    # Loading through the read-only path first keeps migration an explicit operator action.
    store = Store(db)
    index = store.load_index(snapshot)
    return Planning(Store(db, writable=True) if writable else store, index)


@app.command()
def plan_schema() -> None:
    """Print the JSON schema for client/human-authored feature-plan proposals."""
    import json

    typer.echo(json.dumps(PlanProposal.model_json_schema(), indent=2))


@app.command()
def validate_plan(proposal: Path, db: DB = DEFAULT_DB, snapshot: SnapshotID = None) -> None:
    """Validate a proposal without writing; a structurally invalid plan exits with code 2."""
    with errors():
        result = planning(db, snapshot).validate(read_proposal(proposal))
        output(result)
        if not result.valid:
            raise typer.Exit(2)


@app.command()
def submit_plan(proposal: Path, db: DB = DEFAULT_DB, snapshot: SnapshotID = None) -> None:
    """Persist a valid, unapproved draft and its tasks; identical plan-ID retries are no-ops."""
    with errors():
        candidate = read_proposal(proposal)
        output(planning(db, snapshot, writable=True).submit(candidate, actor="local_cli"))


@app.command()
def get_plan(
    plan_id: str, db: DB = DEFAULT_DB, snapshot: SnapshotID = None, spec_file: Path | None = None
) -> None:
    """Read a draft and freshness flags; optionally compare a current specification file."""
    with errors():
        specification = None
        if spec_file is not None:
            with spec_file.open("rb") as source:
                raw = source.read(MAX_PROPOSAL_BYTES + 1)
            if len(raw) > MAX_PROPOSAL_BYTES:
                raise ValueError("specification file exceeds the input budget")
            specification = raw.decode("utf-8")
        output(planning(db, snapshot).get(plan_id, specification))


@app.command()
def list_tasks(
    plan_id: str,
    db: DB = DEFAULT_DB,
    snapshot: SnapshotID = None,
    limit: int = 20,
    offset: int = 0,
) -> None:
    """List the draft's tracked tasks, dependencies, acceptance, and validation requirements."""
    with errors():
        output(planning(db, snapshot).list_tasks(plan_id, limit, offset))


@app.command()
def get_task(plan_id: str, task_id: str, db: DB = DEFAULT_DB, snapshot: SnapshotID = None) -> None:
    """Read one tracked task using its local task ID within a plan."""
    with errors():
        output(planning(db, snapshot).get_task(plan_id, task_id))
