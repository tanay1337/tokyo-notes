"""Tests for core/logging_setup.py — sanitizing formatter."""

from __future__ import annotations

import logging

from core.logging_setup import SanitizingFormatter, set_note_names


class TestSanitizingFormatter:
    def setup_method(self) -> None:
        set_note_names(set())
        self.fmt = SanitizingFormatter("%(message)s")

    def _format(self, msg: str) -> str:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        return self.fmt.format(record)

    def test_empty_message(self) -> None:
        assert self._format("") == ""

    def test_no_names_set_passes_through(self) -> None:
        assert self._format("pdftoppm produced no output for file.pdf page 0") == (
            "pdftoppm produced no output for file.pdf page 0"
        )

    def test_note_name_replaced_when_known(self) -> None:
        set_note_names({"MySecretNote"})
        result = self._format("Saved note MySecretNote")
        assert "MySecretNote" not in result
        assert "<name>" in result

    def test_unknown_tokens_not_replaced(self) -> None:
        set_note_names({"ShoppingList"})
        msg = "pdftoppm exited 1 for /Users/test/file.pdf: error text"
        result = self._format(msg)
        assert result == msg  # nothing should be redacted

    def test_multi_word_note_name_replaced(self) -> None:
        set_note_names({"My Shopping List"})
        result = self._format("Saved note 'My Shopping List' successfully")
        assert "My Shopping List" not in result
        assert "<name>" in result

    def test_clear_names_disables_sanitization(self) -> None:
        set_note_names({"Secret"})
        assert "<name>" in self._format("Secret")
        set_note_names(set())
        assert self._format("Secret") == "Secret"
