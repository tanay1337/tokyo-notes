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

    def test_completed_task_text_is_dimmed_without_dimming_checkbox(self):
        from gi.repository import Gtk

        text = "- [x] Done item\n- [ ] Open item"
        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
        buffer.set_text(text)

        highlighter.highlight()

        dim_tag = buffer.get_tag_table().lookup("task_done_dim")
        checked_tag = buffer.get_tag_table().lookup("checkbox_checked")
        unchecked_text_offset = text.index("Open item")

        assert buffer.get_iter_at_offset(text.index("Done item")).has_tag(dim_tag)
        assert not buffer.get_iter_at_offset(text.index("[x]")).has_tag(dim_tag)
        assert buffer.get_iter_at_offset(text.index("[x]")).has_tag(checked_tag)
        assert not buffer.get_iter_at_offset(unchecked_text_offset).has_tag(dim_tag)

    def test_spell_setup_does_not_force_markdown_retag(self):
        from gi.repository import Gtk

        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
        highlighter.highlight = MagicMock()
        spell_checker = MagicMock()

        highlighter.set_spell_checker(spell_checker, enabled=True)

        highlighter.highlight.assert_not_called()

    def test_spell_tags_use_safe_line_relative_unicode_iters(self):
        from gi.repository import Gtk

        text = "# Sample Note\nMispelled naïve wordt"
        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
        spell_checker = MagicMock()
        spell_checker.all_known_words.return_value = set()
        highlighter.set_spell_checker(spell_checker, enabled=True)
        buffer.set_text(text)
        highlighter.highlight()

        buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
        highlighter._spell_check_pass(1, 2, set())

        misspelled = buffer.get_tag_table().lookup("misspelled")
        naive_offset = text.index("naïve")
        assert buffer.get_iter_at_offset(naive_offset).has_tag(misspelled)
        assert buffer.get_iter_at_offset(naive_offset + 4).has_tag(misspelled)

    def test_incremental_highlight_applies_new_strikethrough(self):
        from gi.repository import Gtk

        text = "A newly ~~crossed out~~ phrase"
        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
        buffer.set_text(text)

        highlighter.highlight_line_range(0, 0, cursor_line=0)

        tag = buffer.get_tag_table().lookup("strikethrough")
        content_offset = text.index("crossed")
        assert buffer.get_iter_at_offset(content_offset).has_tag(tag)

    def test_full_highlight_invalidates_same_line_count_structure_cache(self):
        from gi.repository import Gtk

        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
        buffer.set_text("```\ninside\n```")
        assert highlighter._code_block_line_set() == {1}

        buffer.set_text("plain\ninside\nplain")
        highlighter.highlight()

        assert highlighter._code_block_line_set() == set()

    def test_cache_invalidation_clears_front_matter_with_same_line_count(self):
        from gi.repository import Gtk

        buffer = Gtk.TextBuffer()
        highlighter = MarkdownHighlighter(buffer, _make_tm())
        buffer.set_text("---\nkey: value\n---")
        assert highlighter._front_matter_range() == (0, 2)

        buffer.set_text("text\nkey: value\ntext")
        highlighter.invalidate_structure_cache()

        assert highlighter._front_matter_range() is None

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
                self._invisible_markers_suspended = False
                self._selection_marker_line = -1
                self._pending_highlight_range = None
                self._pending_spell_range = None
                self.highlight_called = False
                self.marker_restore_called = False
                self.content_stack = MagicMock()
                self.content_stack.get_visible_child_name.return_value = "editor"
                self.is_loading = False

            def _do_highlight(self):
                self.highlight_called = True
                self.highlighter.highlight()
                return False

            def _schedule_marker_restore(self):
                self.marker_restore_called = True
                cursor_line = self.buffer.get_iter_at_mark(
                    self.buffer.get_insert()
                ).get_line()
                self.highlighter.restore_marker_range(
                    0,
                    self.buffer.get_line_count(),
                    cursor_line,
                )

            def _set_invisible_markers_suspended(self, suspended):
                TokyoNotes._set_invisible_markers_suspended(self, suspended)

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

        # 3. The range stays attached, but its invisibility is suspended.
        assert start.has_tag(invisible_tag)
        assert invisible_tag.get_property("invisible") is False
        assert app._has_selection is True

        # 4. Clear selection
        buffer.place_cursor(buffer.get_start_iter())
        # Flush any deferred marker callbacks.
        while ctx.iteration(False):
            pass

        # 5. Verify markers are restored without a full-document highlight.
        assert app.marker_restore_called is True
        assert app.highlight_called is False
        dim_tag = buffer.get_tag_table().lookup("dim")
        assert start.has_tag(dim_tag)
        assert invisible_tag.get_property("invisible") is True
        assert app._has_selection is False
