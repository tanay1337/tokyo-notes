from unittest.mock import MagicMock

import pytest
from gi.repository import GLib

from core.highlighter import MarkdownHighlighter
from main import TokyoNotes


def _make_tm() -> MagicMock:
    tm = MagicMock()
    tm.get_syntax_colors.return_value = {}
    return tm


@pytest.mark.gtk
class TestHighlighterIntegration:
    def test_highlighter_applies_tags(self):
        from gi.repository import Gtk

        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
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

    def test_bare_url_underscores_are_not_hidden_as_italic_markers(self):
        from gi.repository import Gtk

        text = "Visit https://linkedin.com/name=tan_pan_hello and _this_"
        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
        buffer.set_text(text)

        highlighter.highlight()

        invisible_tag = buffer.get_tag_table().lookup("invisible")
        url_first_underscore = text.index("_")
        url_second_underscore = text.index("_", url_first_underscore + 1)
        italic_marker = text.rindex("_")

        assert not buffer.get_iter_at_offset(url_first_underscore).has_tag(
            invisible_tag
        )
        assert not buffer.get_iter_at_offset(url_second_underscore).has_tag(
            invisible_tag
        )
        assert buffer.get_iter_at_offset(italic_marker).has_tag(invisible_tag)

        highlighter._set_line_markers(0, is_cursor=False)

        assert not buffer.get_iter_at_offset(url_first_underscore).has_tag(
            invisible_tag
        )
        assert not buffer.get_iter_at_offset(url_second_underscore).has_tag(
            invisible_tag
        )
        assert buffer.get_iter_at_offset(italic_marker).has_tag(invisible_tag)

    def test_invisible_tags_managed_by_selection(self, monkeypatch):
        from gi.repository import Gtk
        # We simulate the TokyoNotes environment enough to test the workaround logic.

        # Create a real buffer
        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())

        # Mock app-like object
        class MockApp:
            def __init__(self):
                self.buffer = buffer
                self.highlighter = highlighter
                self._has_selection = False
                self._has_invisible_tags = False
                self.highlight_called = False
                self.content_stack = MagicMock()
                self.content_stack.get_visible_child_name.return_value = "editor"
                self.is_loading = False

            def _do_highlight(self):
                self.highlight_called = True
                self.highlighter.highlight()
                return False

            def _deferred_remove_invisible(self, buffer):
                highlighter = self.highlighter
                if not highlighter:
                    return False
                start, end = buffer.get_bounds()
                highlighter.buffer.remove_tag_by_name("invisible", start, end)
                return False

        app = MockApp()

        # Wire the invisible-tag tracking callback (as done in main.py)
        highlighter._on_invisible_applied = lambda: setattr(
            app, "_has_invisible_tags", True
        )

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
        # Flush deferred idle callbacks (remove invisible, etc.)
        ctx = GLib.main_context_default()
        while ctx.iteration(False):
            pass

        # 3. Verify 'invisible' tag is REMOVED to prevent Pango crash
        assert not start.has_tag(invisible_tag)
        assert app._has_selection is True

        # 4. Clear selection
        buffer.place_cursor(buffer.get_start_iter())
        # Flush deferred idle callbacks (_do_highlight)
        while ctx.iteration(False):
            pass

        # 5. Verify tag is RESTORED via _do_highlight call
        assert app.highlight_called is True
        assert start.has_tag(invisible_tag)
        assert app._has_selection is False
