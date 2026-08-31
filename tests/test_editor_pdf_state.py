"""Tests for in-editor PDF reading state helpers."""

from __future__ import annotations

from ui.editor import Editor


class _PdfStateConfig:
    def __init__(self) -> None:
        self.states: dict[str, dict] = {}

    def get_pdf_state(self, key: str) -> dict:
        return self.states.get(key, {})

    def set_pdf_state(self, key: str, state: dict) -> None:
        self.states[key] = state


def _editor(config: _PdfStateConfig, note_name: str | None = "Note") -> Editor:
    editor = Editor.__new__(Editor)
    editor._config_manager = config
    editor._get_current_note = lambda: note_name
    return editor


def test_pdf_state_key_uses_note_and_pdf_reference() -> None:
    editor = _editor(_PdfStateConfig(), "Research")

    assert editor._pdf_state_key("docs/paper.pdf") == "Research::docs/paper.pdf"


def test_get_pdf_page_state_clamps_to_page_count() -> None:
    config = _PdfStateConfig()
    config.states["Note::docs/paper.pdf"] = {"page": 99}
    editor = _editor(config)

    assert editor._get_pdf_page_state("docs/paper.pdf", 10) == 9


def test_get_pdf_page_state_ignores_invalid_state() -> None:
    config = _PdfStateConfig()
    config.states["Note::docs/paper.pdf"] = {"page": "3"}
    editor = _editor(config)

    assert editor._get_pdf_page_state("docs/paper.pdf", 10) == 0


def test_save_pdf_page_state_persists_clamped_page() -> None:
    config = _PdfStateConfig()
    editor = _editor(config)

    editor._save_pdf_page_state("docs/paper.pdf", 99, 10)

    assert config.states["Note::docs/paper.pdf"] == {
        "page": 9,
        "total_pages": 10,
        "pdf": "docs/paper.pdf",
    }


def test_pdf_state_is_disabled_without_current_note() -> None:
    config = _PdfStateConfig()
    editor = _editor(config, None)

    assert editor._pdf_state_key("docs/paper.pdf") is None
    assert editor._get_pdf_page_state("docs/paper.pdf", 10) == 0
    editor._save_pdf_page_state("docs/paper.pdf", 3, 10)
    assert config.states == {}
