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
