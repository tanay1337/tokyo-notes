"""Tests for the editor's auto-pair delimiter behavior."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

import pytest
from gi.repository import Gdk, Gtk

from ui.editor import Editor


def _call(buffer: Gtk.TextBuffer, keyval: int) -> bool:
    """Invoke _auto_pair_delimiter without instantiating the full editor."""
    return Editor._auto_pair_delimiter(None, buffer, keyval)  # type: ignore[arg-type]


def _text(buffer: Gtk.TextBuffer) -> str:
    return buffer.get_text(*buffer.get_bounds(), True)


def _cursor_offset(buffer: Gtk.TextBuffer) -> int:
    return buffer.get_iter_at_mark(buffer.get_insert()).get_offset()


def _seed(buffer: Gtk.TextBuffer, text: str, cursor: int | None = None) -> None:
    buffer.set_text(text)
    if cursor is None:
        cursor = len(text)
    buffer.place_cursor(buffer.get_iter_at_offset(cursor))


@pytest.mark.gtk
class TestAutoPairEqual:
    def test_single_equals_on_empty_buffer_inserts_one(self) -> None:
        buffer = Gtk.TextBuffer()

        result = _call(buffer, Gdk.KEY_equal)

        assert result is True
        assert _text(buffer) == "="
        assert _cursor_offset(buffer) == 1

    def test_single_equals_after_different_char_inserts_one(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "a")

        result = _call(buffer, Gdk.KEY_equal)

        assert result is True
        assert _text(buffer) == "a="
        assert _cursor_offset(buffer) == 2

    def test_second_consecutive_equals_inserts_closing_pair(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "=")

        result = _call(buffer, Gdk.KEY_equal)

        assert result is True
        assert _text(buffer) == "===="
        assert _cursor_offset(buffer) == 2

    def test_equals_not_paired_after_letter_inside_word(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "a=")

        _call(buffer, Gdk.KEY_equal)

        assert _text(buffer) == "a===="
        assert _cursor_offset(buffer) == 3


@pytest.mark.gtk
class TestAutoPairTilde:
    def test_single_tilde_on_empty_buffer_inserts_one(self) -> None:
        buffer = Gtk.TextBuffer()

        result = _call(buffer, Gdk.KEY_asciitilde)

        assert result is True
        assert _text(buffer) == "~"
        assert _cursor_offset(buffer) == 1

    def test_single_tilde_after_different_char_inserts_one(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "a")

        _call(buffer, Gdk.KEY_asciitilde)

        assert _text(buffer) == "a~"
        assert _cursor_offset(buffer) == 2

    def test_second_consecutive_tilde_inserts_closing_pair(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "~")

        result = _call(buffer, Gdk.KEY_asciitilde)

        assert result is True
        assert _text(buffer) == "~~~~"
        assert _cursor_offset(buffer) == 2


@pytest.mark.gtk
class TestAutoPairSelectionWrap:
    def test_selection_equals_wraps_with_highlight_markers(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "hello")
        start, end = buffer.get_bounds()
        buffer.select_range(start, end)

        _call(buffer, Gdk.KEY_equal)

        assert _text(buffer) == "==hello=="
        assert _cursor_offset(buffer) == 9

    def test_selection_tilde_wraps_with_strikethrough_markers(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "hello")
        start, end = buffer.get_bounds()
        buffer.select_range(start, end)

        _call(buffer, Gdk.KEY_asciitilde)

        assert _text(buffer) == "~~hello~~"
        assert _cursor_offset(buffer) == 9


@pytest.mark.gtk
class TestAutoPairRegression:
    def test_asterisk_still_paires_on_single_press(self) -> None:
        buffer = Gtk.TextBuffer()

        result = _call(buffer, Gdk.KEY_asterisk)

        assert result is True
        assert _text(buffer) == "**"
        assert _cursor_offset(buffer) == 1

    def test_parenleft_still_paires_on_single_press(self) -> None:
        buffer = Gtk.TextBuffer()

        result = _call(buffer, Gdk.KEY_parenleft)

        assert result is True
        assert _text(buffer) == "()"
        assert _cursor_offset(buffer) == 1

    def test_bracketleft_does_not_autoclose(self) -> None:
        buffer = Gtk.TextBuffer()
        _seed(buffer, "")

        result = _call(buffer, Gdk.KEY_bracketleft)

        assert result is False
        assert _text(buffer) == ""

    def test_unrelated_keyval_returns_false(self) -> None:
        buffer = Gtk.TextBuffer()

        result = _call(buffer, Gdk.KEY_a)

        assert result is False
        assert _text(buffer) == ""
