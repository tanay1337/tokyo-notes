from unittest.mock import MagicMock

import pytest

from core.highlighter import MarkdownHighlighter
from main import TokyoNotes


@pytest.mark.gtk
class TestHighlighterIntegration:
    def test_highlighter_applies_tags(self):
        from gi.repository import Gtk

        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer)
        buffer.set_text("# Heading\n**bold**")

        highlighter.highlight()

        # Check H1 tag
        start = buffer.get_start_iter()
        h1_tag = buffer.get_tag_table().lookup("h1")
        assert start.has_tag(h1_tag)

        # Check bold tag
        # "# Heading\n**" is 10 chars + 2 for ** = 12?
        # offset 0: #
        # offset 10: \n
        # offset 11: *
        # offset 12: *
        # offset 13: b
        bold_start = buffer.get_iter_at_offset(13)
        bold_tag = buffer.get_tag_table().lookup("bold")
        assert bold_start.has_tag(bold_tag)

    def test_invisible_tags_managed_by_selection(self, monkeypatch):
        from gi.repository import Gtk
        # We simulate the TokyoNotes environment enough to test the workaround logic.

        # Create a real buffer
        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer)

        # Mock app-like object
        class MockApp:
            def __init__(self):
                self.buffer = buffer
                self.highlighter = highlighter
                self._has_selection = False
                self.highlight_called = False
                self.content_stack = MagicMock()
                self.content_stack.get_visible_child_name.return_value = "editor"

            def _do_highlight(self):
                self.highlight_called = True
                self.highlighter.highlight()
                return False

        app = MockApp()

        # Connect the real handler from TokyoNotes
        buffer.connect(
            "mark-set",
            lambda b, location, m: TokyoNotes._on_mark_set(app, b, location, m),
        )

        buffer.set_text("# Heading")
        highlighter.highlight()

        # 1. Verify 'invisible' tag is present (cursor not on line)
        start = buffer.get_start_iter()
        invisible_tag = buffer.get_tag_table().lookup("invisible")
        assert start.has_tag(invisible_tag)

        # 2. Start selection
        # select_range triggers mark-set for 'insert' and 'selection_bound'
        buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())

        # 3. Verify 'invisible' tag is REMOVED to prevent Pango crash
        assert not start.has_tag(invisible_tag)
        assert app._has_selection is True

        # 4. Clear selection
        buffer.place_cursor(buffer.get_start_iter())

        # 5. Verify tag is RESTORED via _do_highlight call
        assert app.highlight_called is True
        assert start.has_tag(invisible_tag)
        assert app._has_selection is False
