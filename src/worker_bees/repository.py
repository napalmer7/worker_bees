"""Read committed Git blobs only. Never import the target application."""

import io
import platform
import subprocess
import tokenize
from pathlib import Path, PurePosixPath

from worker_bees.models import Document, Issue, Snapshot

EXCLUDED = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "vendor"})


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(root), *args],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts.pop(0)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__root__"


def read_snapshot(
    repository: Path,
    revision: str = "HEAD",
    *,
    max_file_bytes: int = 1_000_000,
    max_total_bytes: int = 16_000_000,
) -> tuple[Snapshot, list[Document]]:
    root = Path(git(repository.resolve(), "rev-parse", "--show-toplevel").decode().strip())
    commit = git(root, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
    commit_id = commit.decode().strip()
    tree = git(root, "rev-parse", f"{commit_id}^{{tree}}").decode().strip()
    entries = git(root, "ls-tree", "-r", "-z", "--full-tree", "-l", commit_id).split(b"\0")
    documents: list[Document] = []
    issues: list[Issue] = []
    excluded: list[str] = []
    count = total_bytes = 0
    for entry in entries:
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if not path.endswith(".py"):
            continue
        count += 1
        mode, kind, blob, size = metadata.split()
        if EXCLUDED.intersection(PurePosixPath(path).parts):
            excluded.append(path)
            continue
        if mode not in (b"100644", b"100755") or kind != b"blob":
            issues.append(Issue(path=path, reason="not a regular committed file (symlink skipped)"))
            continue
        byte_count = int(size)
        if byte_count > max_file_bytes or total_bytes + byte_count > max_total_bytes:
            issues.append(Issue(path=path, reason="configured source byte limit exceeded"))
            continue
        total_bytes += byte_count
        raw = git(root, "cat-file", "blob", blob.decode())
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding)
        except (SyntaxError, UnicodeError, LookupError) as error:
            issues.append(Issue(path=path, reason=f"decode failure: {error}"))
            continue
        documents.append(
            Document(
                path=path,
                module=module_name(path),
                source=source,
                blob=blob.decode(),
                is_package=PurePosixPath(path).name == "__init__.py",
            )
        )
    snapshot = Snapshot(
        repository=str(root),
        commit=commit_id,
        tree=tree,
        parser=f"python-{platform.python_version()}/ast-lexical-v1",
        status="partial" if issues else "complete",
        python_files=count,
        indexed_files=0,
        excluded_paths=excluded,
        issues=issues,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    return snapshot, documents
