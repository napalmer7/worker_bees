import pytest
from conftest import git

from worker_bees.analysis import build_index
from worker_bees.repository import read_snapshot


def test_committed_blobs_ignore_dirty_files_and_never_execute(make_repo, tmp_path):
    marker = tmp_path / "executed"
    root = make_repo(
        {
            "sample.py": (
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
                "def committed():\n    pass\n"
            )
        }
    )
    (root / "sample.py").write_text("def dirty():\n    pass\n")
    (root / "untracked.py").write_text("def unseen():\n    pass\n")
    index = build_index(root)
    assert index.snapshot.commit == git(root, "rev-parse", "HEAD")
    assert {s.name for s in index.symbols if s.kind == "function"} == {"committed"}
    assert not marker.exists()


def test_partial_index_reports_syntax_encoding_and_symlink_failures(make_repo):
    root = make_repo(
        {
            "ok.py": "def valid():\n    pass\n",
            "bad.py": "def invalid(:",
            "bytes.py": b"\xff\xfe\xfd",
        }
    )
    (root / "link.py").symlink_to("/etc/passwd")
    git(root, "add", "link.py")
    git(root, "commit", "-m", "symlink")
    index = build_index(root)
    assert index.snapshot.status == "partial"
    assert index.snapshot.python_files == 4
    assert index.snapshot.indexed_files == 1
    assert {issue.path for issue in index.snapshot.issues} == {"bad.py", "bytes.py", "link.py"}
    assert {doc.path for doc in index.documents} == {"ok.py"}


def test_source_limits_and_exclusions_are_visible(make_repo):
    root = make_repo({"a.py": "pass\n", "vendor/b.py": "pass\n"})
    snapshot, documents = read_snapshot(root, max_file_bytes=1)
    assert snapshot.status == "partial" and not documents
    assert snapshot.excluded_paths == ["vendor/b.py"]
    assert snapshot.issues[0].path == "a.py"
    snapshot, documents = read_snapshot(root, max_total_bytes=5)
    assert [document.path for document in documents] == ["a.py"]


def test_python_source_encoding_and_src_layout(make_repo):
    root = make_repo({"src/pkg/__init__.py": b"# coding: latin-1\n# caf\xe9\n"})
    index = build_index(root)
    assert index.snapshot.status == "complete"
    assert index.documents[0].module == "pkg"
    assert "café" in index.documents[0].source


def test_invalid_revision_fails_instead_of_empty_success(make_repo):
    root = make_repo({"a.py": "pass"})
    with pytest.raises(ValueError):
        build_index(root, "--help")
    with pytest.raises(ValueError):
        build_index(root, "does-not-exist")


def test_index_empty_repository_exposes_zero_coverage(make_repo):
    index = build_index(make_repo({"README.md": "No Python source"}))
    assert index.snapshot.python_files == index.snapshot.indexed_files == 0
    assert index.symbols == index.calls == []
