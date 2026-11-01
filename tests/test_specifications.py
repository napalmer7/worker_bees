import hashlib
import io
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from typer.testing import CliRunner

from worker_bees.cli import app
from worker_bees.specifications import prepare_specification, read_specification

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(path, body, extra=None):
    with ZipFile(path, "w", ZIP_DEFLATED) as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        package.writestr(
            "word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>"
        )
        for name, value in (extra or {}).items():
            package.writestr(name, value)
    return path


def test_text_blocks_keep_exact_text_offsets_and_hash():
    text = "# Scope\r\n\r\nKeep café.\r\nWrapped line.\r\n \r\nReject negatives."
    prepared = prepare_specification(text)
    assert prepared.text == text
    assert prepared.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert [block.id for block in prepared.blocks] == ["B0001", "B0002", "B0003"]
    assert prepared.blocks[0].kind == "heading"
    assert text[prepared.blocks[1].start : prepared.blocks[1].end] == "Keep café.\r\nWrapped line."


@pytest.mark.parametrize("text", [" ", "x" * 32_001, "\n\n".join(["x"] * 257)])
def test_intake_limits_are_explicit(text):
    with pytest.raises(ValueError):
        prepare_specification(text)


def test_docx_reads_body_headings_and_tables_without_opening_external_parts(tmp_path):
    body = """<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Feature</w:t></w:r></w:p>
    <w:p><w:r><w:t>Reject negatives.</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Input</w:t></w:r></w:p></w:tc>
    <w:tc><w:p><w:r><w:t>Expected result</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    <w:p><w:r><w:drawing><w:txbxContent><w:p><w:r><w:t>OMITTED</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r></w:p>"""
    path = docx(
        tmp_path / "feature.docx",
        body,
        {
            "../must-not-be-extracted": "no extraction",
            "word/header1.xml": "PRIVATE-HEADER",
            "word/_rels/document.xml.rels": (
                '<Relationships><Relationship Target="https://invalid.example" '
                'TargetMode="External"/></Relationships>'
            ),
        },
    )
    prepared = read_specification(path)
    assert prepared.text == "# Feature\n\nReject negatives.\n\nInput\tExpected result"
    assert prepared.source_format == "docx"
    assert prepared.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not (tmp_path.parent / "must-not-be-extracted").exists()
    assert "OMITTED" not in prepared.text and "PRIVATE-HEADER" not in prepared.text
    assert any("not imported" in warning for warning in prepared.warnings)


@pytest.mark.parametrize(
    "body",
    [
        "<w:p><w:ins><w:r><w:t>Unaccepted insertion</w:t></w:r></w:ins></w:p>",
        "<w:p><w:del><w:r><w:delText>Unaccepted deletion</w:delText></w:r></w:del></w:p>",
    ],
)
def test_tracked_changes_require_resolution(tmp_path, body):
    with pytest.raises(ValueError, match="tracked changes"):
        read_specification(docx(tmp_path / "changes.docx", body))


def test_docx_entity_declarations_and_invalid_packages_fail(tmp_path):
    path = tmp_path / "unsafe.docx"
    with ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", "types")
        package.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY x "EXPAND">]><root/>')
    with pytest.raises(ValueError, match="DTD/entity"):
        read_specification(path)
    path.write_text("not a zip")
    with pytest.raises(ValueError, match="DOCX package"):
        read_specification(path)


def test_macro_and_zip_bombs_are_rejected(tmp_path):
    path = docx(tmp_path / "macro.docx", "", {"word/vbaProject.bin": b"not executable"})
    with pytest.raises(ValueError, match="macro"):
        read_specification(path)
    path = docx(tmp_path / "large.docx", "x" * 1_000_001)
    with pytest.raises(ValueError, match="oversized"):
        read_specification(path)


def test_text_cli_and_unsupported_formats(tmp_path):
    path = tmp_path / "feature.md"
    path.write_text("# Feature\n\nDo the thing.")
    assert read_specification(path).source_format == "markdown"
    result = CliRunner().invoke(app, ["prepare-spec", str(path)])
    assert result.exit_code == 0 and '"B0002"' in result.output
    path = tmp_path / "old.doc"
    path.write_bytes(b"legacy binary")
    with pytest.raises(ValueError, match="supported"):
        read_specification(path)


def test_duplicate_package_entries_are_rejected(tmp_path):
    path = docx(tmp_path / "duplicate.docx", "")
    with pytest.warns(UserWarning), ZipFile(path, "a") as package:
        package.writestr("word/document.xml", "different")
    with pytest.raises(ValueError, match="duplicate"):
        read_specification(path)


def test_docx_hash_is_for_the_exact_bytes_parsed(tmp_path, monkeypatch):
    path = docx(tmp_path / "same.docx", "<w:p><w:r><w:t>Original</w:t></w:r></w:p>")
    original = path.read_bytes()
    path.write_bytes(b"different live file; not a ZIP package")
    original_open = type(path).open

    def open_once(self, *args, **kwargs):
        if self == path:
            return io.BytesIO(original)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", open_once)
    prepared = read_specification(path)
    assert prepared.text == "Original"
    assert prepared.source_sha256 == hashlib.sha256(original).hexdigest()
