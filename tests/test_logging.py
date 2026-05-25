"""Tests for core/logging_setup.py — sanitizing formatter."""

from __future__ import annotations

import logging

from core.logging_setup import SanitizingFormatter


class TestSanitizingFormatter:
    def setup_method(self) -> None:
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

    def test_plain_message_short_tokens(self) -> None:
        # Single char is below the 2-char threshold of the note-name regex
        assert self._format("x") == "x"
        assert self._format("") == ""

    def test_note_name_replaced(self) -> None:
        result = self._format("Saved note MySecretNote")
        assert "MySecretNote" not in result
        assert "<name>" in result

    def test_empty_message(self) -> None:
        assert self._format("") == ""
