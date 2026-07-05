"""Tests for the note lifecycle manager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.note_lifecycle import NoteLifecycleManager


def _make_row(note_name: str) -> MagicMock:
    row = MagicMock()
    row.note_name = note_name
    return row


def _make_app(**overrides: object) -> MagicMock:
    """Build a mock TokyoNotes app with sensible defaults.

    Individual tests can override any attribute via keyword arguments.
    """
    notes_manager = MagicMock()
    notes_manager.get_notes.return_value = []
    notes_manager.is_encrypted.return_value = False
    notes_manager.reserve_name.return_value = "untitled"
    notes_manager.read_plain.return_value = ""

    nav = MagicMock()
    nav.default_filter = "today"

    sidebar = MagicMock()
    sidebar.main_list = MagicMock()
    sidebar.archive_list = MagicMock()
    sidebar.search_entry = MagicMock()
    sidebar.search_entry.get_text.return_value = ""
    sidebar.maybe_exit_archive_view = MagicMock()

    def _sidebar_child_iter():
        child = MagicMock()
        child.note_name = None
        child.get_next_sibling.return_value = None
        return child

    sidebar.main_list.get_first_child = _sidebar_child_iter
    sidebar.archive_list.get_first_child = _sidebar_child_iter

    buffer = MagicMock()
    buffer.get_text.return_value = ""
    buffer.get_bounds.return_value = (MagicMock(), MagicMock())
    buffer.get_line_count.return_value = 0
    buffer.get_start_iter.return_value = MagicMock()
    buffer.get_iter_at_line.return_value = (MagicMock(), MagicMock())

    cfg = MagicMock()
    cfg.encrypted = set()

    editor = MagicMock()
    editor._image_update_running = False
    editor.status_bar.get_visible.return_value = False

    template_manager = MagicMock()
    template_manager.templates_dir = MagicMock()
    template_manager.update_template = MagicMock()

    defaults = {
        "notes_manager": notes_manager,
        "nav": nav,
        "sidebar": sidebar,
        "buffer": buffer,
        "cfg": cfg,
        "editor": editor,
        "template_manager": template_manager,
        "current_note": None,
        "is_loading": False,
        "split_editor": None,
        "win": MagicMock(),
        "highlighter": MagicMock(),
        "content_stack": MagicMock(),
        "text_view": MagicMock(),
        "_is_session_locked": False,
        "_session_password_bytes": None,
        "_buffer_mod_counter": 0,
        "_last_sidebar_update_counter": 0,
        "_has_images": False,
        "_pending_highlight_id": 0,
        "_full_pass_complete": False,
        "changed_handler_id": 123,
        "refresh_list": MagicMock(),
        "_flush_pending_save": MagicMock(),
        "_select_sidebar_row": MagicMock(return_value=True),
        "_set_buffer_text": MagicMock(),
        "_show_unlock_popover": MagicMock(),
        "_set_backlinks_visible": MagicMock(),
        "_load_encrypted_note": MagicMock(),
        "_safe_source_remove": MagicMock(),
        "_reschedule": MagicMock(),
        "_reset_lock_timer_on_activity": MagicMock(),
        "_update_backlinks": MagicMock(),
        "do_delayed_highlight": MagicMock(),
        "do_delayed_images": MagicMock(),
        "on_empty": MagicMock(),
    }
    defaults.update(overrides)
    app = MagicMock()
    for k, v in defaults.items():
        setattr(app, k, v)
    app.sidebar.set_active_view = MagicMock()
    return app


class TestInitialLoad:
    def test_loads_most_recent_note(self) -> None:
        app = _make_app()
        notes_manager = app.notes_manager
        notes_manager.get_notes.return_value = ["note-b", "note-a"]
        lifecycle = NoteLifecycleManager(app)
        lifecycle.initial_load()
        app._select_sidebar_row.assert_called_once_with("note-b")

    def test_creates_new_note_when_no_notes(self) -> None:
        app = _make_app()
        app.notes_manager.get_notes.return_value = []
        lifecycle = NoteLifecycleManager(app)
        with patch.object(lifecycle, "on_new_note") as mock_new:
            lifecycle.initial_load()
            mock_new.assert_called_once_with(None)


class TestNewNote:
    def test_reserves_name_and_clears_buffer(self) -> None:
        app = _make_app()
        app.notes_manager.reserve_name.return_value = "untitled-1"
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_new_note(None)
        assert app.current_note == "untitled-1"
        app._flush_pending_save.assert_called_once()
        app._set_buffer_text.assert_called_once_with("")
        app.editor.set_editable.assert_called_once_with(True)
        app.content_stack.set_visible_child_name.assert_called_with("editor")
        app.text_view.grab_focus.assert_called()

    def test_global_new_note_grabs_focus(self) -> None:
        app = _make_app()
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_new_note_global()
        app.text_view.grab_focus.assert_called()


class TestScrollToLine:
    def test_scrolls_to_valid_line(self) -> None:
        app = _make_app()
        line_iter = MagicMock()
        app.buffer.get_iter_at_line.return_value = (line_iter, MagicMock())
        lifecycle = NoteLifecycleManager(app)
        result = lifecycle.scroll_to_line(3)
        app.buffer.get_iter_at_line.assert_called_once_with(2)
        app.text_view.scroll_to_mark.assert_called_once()
        assert result is False

    def test_returns_false_on_invalid_line(self) -> None:
        app = _make_app()
        app.buffer.get_iter_at_line.side_effect = TypeError
        lifecycle = NoteLifecycleManager(app)
        result = lifecycle.scroll_to_line(999)
        assert result is False


class TestDelete:
    def test_delete_empty_note_skips_dialog(self) -> None:
        app = _make_app()
        app.notes_manager.read_plain.return_value = ""
        lifecycle = NoteLifecycleManager(app)
        with patch.object(lifecycle, "confirm_delete") as mock_confirm:
            parameter = MagicMock(get_string=MagicMock(return_value="empty-note"))
            lifecycle.on_delete_action(None, parameter)
            mock_confirm.assert_called_once_with("empty-note")

    def test_confirm_delete_cleans_up_current_note(self) -> None:
        app = _make_app()
        app.current_note = "to-delete"
        app.notes_manager.get_notes.return_value = ["other-note"]
        app.notes_manager.read_plain.return_value = ""
        app.sidebar.search_entry.get_text.return_value = ""
        app._select_sidebar_row.return_value = False
        lifecycle = NoteLifecycleManager(app)
        lifecycle.confirm_delete("to-delete")
        app.notes_manager.delete_note.assert_called_once_with("to-delete")
        app.cfg.remove_note.assert_called_once_with("to-delete")
        app._set_buffer_text.assert_called_once_with("")

    def test_confirm_delete_reloads_remaining(self) -> None:
        app = _make_app()
        app.current_note = "to-delete"
        app.notes_manager.get_notes.return_value = ["survivor"]
        app.sidebar.search_entry.get_text.return_value = ""
        app.sidebar.maybe_exit_archive_view = MagicMock()
        lifecycle = NoteLifecycleManager(app)
        with patch.object(lifecycle, "initial_load") as mock_init:
            lifecycle.confirm_delete("to-delete")
            mock_init.assert_called_once()

    def test_confirm_delete_creates_new_when_none_remain(self) -> None:
        app = _make_app()
        app.current_note = "last-note"
        app.notes_manager.get_notes.return_value = []
        app.sidebar.search_entry.get_text.return_value = ""
        app.sidebar.maybe_exit_archive_view = MagicMock()
        lifecycle = NoteLifecycleManager(app)
        with patch.object(lifecycle, "on_new_note") as mock_new:
            lifecycle.confirm_delete("last-note")
            mock_new.assert_called_once_with(None)


class TestDeleteShortcut:
    def test_returns_false_when_text_view_focused(self) -> None:
        app = _make_app()
        app.text_view.has_focus.return_value = True
        lifecycle = NoteLifecycleManager(app)
        result = lifecycle.on_delete_shortcut()
        assert result is False


class TestHighlightChunk:
    def test_highlights_full_document_in_chunks(self) -> None:
        app = _make_app()
        app.current_note = "test-note"
        app.buffer.get_line_count.return_value = 120
        lifecycle = NoteLifecycleManager(app)

        lifecycle._highlight_chunk("test-note", 0)
        app.highlighter.highlight.assert_called_once_with(start_line=0, end_line=50)

    def test_aborts_if_note_changed(self) -> None:
        app = _make_app()
        app.current_note = "different-note"
        lifecycle = NoteLifecycleManager(app)

        result = lifecycle._highlight_chunk("expected-note", 0)
        assert result is False

    def test_aborts_if_highlighter_gone(self) -> None:
        app = _make_app()
        app.current_note = "test-note"
        app.highlighter = None
        lifecycle = NoteLifecycleManager(app)

        result = lifecycle._highlight_chunk("test-note", 0)
        assert result is False


class TestLinkClicked:
    def test_navigates_to_note(self) -> None:
        app = _make_app()
        app._select_sidebar_row.return_value = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_link_clicked("linked-note")
        app._select_sidebar_row.assert_called_once_with("linked-note")
        app.content_stack.set_visible_child_name.assert_not_called()
        app._set_backlinks_visible.assert_called_once_with(True)


class TestHandleRowClick:
    def test_scrolls_to_line_on_row_click(self) -> None:
        app = _make_app()
        app._select_sidebar_row.return_value = True
        lifecycle = NoteLifecycleManager(app)
        cb = {"note": "some-note", "line": 5}
        with patch.object(lifecycle, "scroll_to_line"):
            lifecycle.handle_row_click(MagicMock(), 2, 100.0, 50.0, cb)
            app._select_sidebar_row.assert_called_once_with("some-note")
            app.content_stack.set_visible_child_name.assert_called_with("editor")


class TestFinishSave:
    def test_renames_note_when_title_changes(self) -> None:
        app = _make_app()
        app.current_note = "old-title"
        app.notes_manager.is_encrypted.return_value = False
        app.cfg.encrypted = set()
        app.cfg.sync_encrypted_set = MagicMock()

        with patch(
            "core.note_lifecycle.update_note_title",
            return_value=("new-title", True),
        ):
            lifecycle = NoteLifecycleManager(app)
            lifecycle._finish_save("old-title", "# New Title\ncontent")
            assert app.current_note == "new-title"
            app.nav.update_header_ui.assert_called_with("new-title", is_editor=True)
            app.refresh_list.assert_called_once()

    def test_does_not_rename_when_title_same(self) -> None:
        app = _make_app()
        app.current_note = "same-title"
        app.sidebar.search_entry.has_focus.return_value = False

        with patch(
            "core.note_lifecycle.update_note_title",
            return_value=("same-title", False),
        ):
            lifecycle = NoteLifecycleManager(app)
            lifecycle._finish_save("same-title", "content")
            app._select_sidebar_row.assert_called_with("same-title")


class TestTextChanged:
    def test_ignores_during_loading(self) -> None:
        app = _make_app()
        app.is_loading = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_text_changed(MagicMock())
        app._reschedule.assert_not_called()

    def test_ignores_without_current_note(self) -> None:
        app = _make_app()
        app.current_note = None
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_text_changed(MagicMock())
        app._reschedule.assert_not_called()

    def test_schedules_all_timers(self) -> None:
        app = _make_app()
        app.current_note = "test-note"
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_text_changed(MagicMock())
        assert app._reschedule.call_count == 5
        expected_calls = [150, 100, 500, 2000, 1000]
        for call, delay in zip(app._reschedule.call_args_list, expected_calls):
            assert call[0][1] == delay

    def test_increments_mod_counter(self) -> None:
        app = _make_app()
        app.current_note = "test-note"
        app._buffer_mod_counter = 5
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_text_changed(MagicMock())
        assert app._buffer_mod_counter == 6


class TestUpdateSidebarAndStats:
    def test_early_return_without_current_note(self) -> None:
        app = _make_app()
        app.current_note = None
        lifecycle = NoteLifecycleManager(app)
        result = lifecycle._update_sidebar_and_stats()
        assert result is False

    def test_early_return_when_counter_not_changed(self) -> None:
        app = _make_app()
        app.current_note = "test-note"
        app._buffer_mod_counter = 3
        app._last_sidebar_update_counter = 3
        lifecycle = NoteLifecycleManager(app)
        result = lifecycle._update_sidebar_and_stats()
        assert result is False

    def test_updates_status_bar_when_visible(self) -> None:
        app = _make_app()
        app.current_note = "test-note"
        app._buffer_mod_counter = 1
        app._last_sidebar_update_counter = 0
        app.buffer.get_bounds.return_value = (MagicMock(), MagicMock())
        app.buffer.get_text.return_value = "hello world"
        app.editor.status_bar.get_visible.return_value = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle._update_sidebar_and_stats()
        app.editor.stats_label.set_label.assert_called_once()


class TestOnNoteSelected:
    def test_returns_early_without_row(self) -> None:
        app = _make_app()
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_note_selected(MagicMock(), None)
        app._flush_pending_save.assert_not_called()

    def test_returns_early_during_loading(self) -> None:
        app = _make_app()
        app.is_loading = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_note_selected(MagicMock(), _make_row("test-note"))
        app._flush_pending_save.assert_not_called()

    def test_returns_early_without_note_name(self) -> None:
        row = MagicMock()
        del row.note_name
        app = _make_app()
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_note_selected(MagicMock(), row)
        app._flush_pending_save.assert_not_called()

    def test_shows_unlock_for_locked_encrypted_note(self) -> None:
        app = _make_app()
        app.current_note = "same-note"
        app.notes_manager.is_encrypted.return_value = True
        app._is_session_locked = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_note_selected(MagicMock(), _make_row("same-note"))
        app._show_unlock_popover.assert_called_once()

    def test_opens_encrypted_locked_note_with_prompt(self) -> None:
        app = _make_app()
        app.current_note = "other-note"
        app.notes_manager.is_encrypted.return_value = True
        app._is_session_locked = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_note_selected(MagicMock(), _make_row("secret-note"))
        assert app.current_note == "secret-note"
        app._flush_pending_save.assert_called_once()
        app.editor.set_editable.assert_called_with(False)
        app._show_unlock_popover.assert_called_once()

    def test_loads_plain_note_content(self) -> None:
        app = _make_app()
        app.current_note = "other-note"
        app.notes_manager.read_plain.return_value = "# Hello\nWorld"
        app.buffer.get_text.return_value = "# Hello\nWorld"
        app._select_sidebar_row.return_value = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_note_selected(MagicMock(), _make_row("plain-note"))
        assert app.current_note == "plain-note"
        app._set_buffer_text.assert_called_with("# Hello\nWorld")
        app.content_stack.set_visible_child_name.assert_called_with("editor")
        assert app.is_loading is False

    def test_handles_empty_content_on_delete_check(self) -> None:
        app = _make_app()
        app.current_note = "other-note"
        app.notes_manager.read_plain.return_value = ""
        app._select_sidebar_row.return_value = True
        lifecycle = NoteLifecycleManager(app)
        lifecycle.on_note_selected(MagicMock(), _make_row("empty-note"))
        app._set_buffer_text.assert_called_with("")
        assert app.is_loading is False
