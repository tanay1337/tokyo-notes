from __future__ import annotations

import hashlib
from pathlib import Path

from core.media_index import build_media_index, find_media_reference_line


def test_build_media_index_tracks_references_and_orphans(tmp_path: Path) -> None:
    images = tmp_path / ".images"
    documents = tmp_path / ".documents"
    images.mkdir()
    documents.mkdir()
    (images / "used.png").write_bytes(b"png")
    (images / "orphan.jpg").write_bytes(b"jpg")
    (documents / "report.pdf").write_bytes(b"pdf")
    notes = {"Meeting": "![Used](.images/used.png)\n![Report](.documents/report.pdf)"}

    assets = build_media_index(
        tmp_path,
        notes,
        notes.__getitem__,
        readable_notes={"Meeting"},
    )

    by_name = {asset.filename: asset for asset in assets}
    assert by_name["used.png"].note_names == ("Meeting",)
    assert by_name["report.pdf"].referenced is True
    assert by_name["orphan.jpg"].referenced is False


def test_cached_remote_pdf_is_indexed_when_referenced(tmp_path: Path) -> None:
    (tmp_path / ".documents").mkdir()
    url = "https://example.test/file.pdf?download=1"
    digest = hashlib.md5(url.encode()).hexdigest()
    cached = tmp_path / ".documents" / f"remote_{digest}.pdf"
    cached.write_bytes(b"pdf")

    assets = build_media_index(
        tmp_path,
        ["Note"],
        lambda _: f"![Remote]({url})",
    )

    assert len(assets) == 1
    assert assets[0].path == cached.resolve()
    assert assets[0].note_names == ("Note",)


def test_locked_note_references_are_not_indexed(tmp_path: Path) -> None:
    images = tmp_path / ".images"
    images.mkdir()
    (images / "private.png").write_bytes(b"png")

    assets = build_media_index(
        tmp_path,
        ["Private"],
        lambda _: "![Private](.images/private.png)",
        readable_notes=set(),
    )

    assert assets[0].note_names == ()


def test_diagrams_are_indexed_with_title_and_note_reference(tmp_path: Path) -> None:
    diagrams = tmp_path / ".diagrams"
    diagrams.mkdir()
    (diagrams / "abc123.json").write_text(
        '{"id": "abc123", "title": "Architecture", "nodes": [], "edges": []}',
        encoding="utf-8",
    )

    assets = build_media_index(
        tmp_path,
        ["Design"],
        lambda _: "![Architecture](abc123)",
    )

    assert len(assets) == 1
    assert assets[0].kind == "diagram"
    assert assets[0].title == "Architecture"
    assert assets[0].note_names == ("Design",)


def test_media_reference_line_is_one_based() -> None:
    content = "# Note\n\nBefore\n![image](.images/photo.png)\nAfter\n"
    assert find_media_reference_line(content, ".images/photo.png") == 4
    assert find_media_reference_line(content, "missing.png") is None
