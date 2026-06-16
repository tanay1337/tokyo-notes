"""Security-focused tests for editor helpers."""

from __future__ import annotations

from ui.editor import resolve_image_path


class TestResolveImagePath:
    def test_allows_relative_path_inside_notes_dir(self, tmp_path) -> None:
        image = resolve_image_path(tmp_path, "images/pasted.png")

        assert image == (tmp_path / "images" / "pasted.png").resolve()

    def test_rejects_parent_directory_escape(self, tmp_path) -> None:
        image = resolve_image_path(tmp_path, "../secret.png")

        assert image is None

    def test_allows_tilde_path(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        image = resolve_image_path(tmp_path, "~/photo.jpg")

        assert image == (tmp_path / "photo.jpg").resolve()

    def test_allows_absolute_path(self, tmp_path) -> None:
        target = tmp_path / "stuff" / "doc.pdf"
        image = resolve_image_path(tmp_path, str(target))

        assert image == target.resolve()
