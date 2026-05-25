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

    def test_rejects_sibling_prefix_escape(self, tmp_path) -> None:
        sibling = tmp_path.parent / f"{tmp_path.name}_evil" / "secret.png"
        image = resolve_image_path(tmp_path, str(sibling))

        assert image is None
