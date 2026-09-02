"""Regression tests for frame-bounded editor highlighting work."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from core.note_lifecycle import NoteLifecycleManager
from main import TokyoNotes


def _incremental_highlight_app() -> SimpleNamespace:
    cursor = MagicMock()
    cursor.get_line.return_value = 4
    buffer = MagicMock()
    buffer.get_has_selection.return_value = False
    buffer.get_line_count.return_value = 10
    buffer.get_iter_at_mark.return_value = cursor

    find_bar = SimpleNamespace(_visible=False, _find_results=[])
    lifecycle = MagicMock()
    lifecycle._merge_line_range.side_effect = NoteLifecycleManager._merge_line_range
    return SimpleNamespace(
        highlight_timeout_id=99,
        highlighter=MagicMock(),
        content_stack=MagicMock(
            get_visible_child_name=MagicMock(return_value="editor")
        ),
        _full_pass_complete=True,
        editor=SimpleNamespace(_image_update_running=False, find_bar=find_bar),
        _has_selection=False,
        buffer=buffer,
        _pending_highlight_range=(3, 3),
        _pending_highlight_neighbours=True,
        changed_handler_id=17,
        last_cursor_line=-1,
        lifecycle=lifecycle,
        _reschedule=MagicMock(),
        do_delayed_highlight=MagicMock(),
        _sidebar_search_text="",
        _apply_search_highlights=MagicMock(),
    )


def test_incremental_highlight_processes_one_line_per_callback() -> None:
    app = _incremental_highlight_app()

    TokyoNotes.do_delayed_highlight(app)

    app.highlighter.highlight_line_range.assert_called_once_with(2, 2, cursor_line=4)
    assert app._pending_highlight_range == (3, 4)
    assert app._pending_highlight_neighbours is False
    app._reschedule.assert_called_once_with(
        "highlight_timeout_id", 5, app.do_delayed_highlight
    )

    app.highlighter.highlight_line_range.reset_mock()
    app._reschedule.reset_mock()
    TokyoNotes.do_delayed_highlight(app)

    app.highlighter.highlight_line_range.assert_called_once_with(3, 3, cursor_line=4)
    assert app._pending_highlight_range == (4, 4)
    app._reschedule.assert_called_once()

    app.highlighter.highlight_line_range.reset_mock()
    app._reschedule.reset_mock()
    TokyoNotes.do_delayed_highlight(app)

    app.highlighter.highlight_line_range.assert_called_once_with(4, 4, cursor_line=4)
    assert app._pending_highlight_range is None
    app._reschedule.assert_not_called()


def test_marker_restore_only_schedules_old_and_new_cursor_lines() -> None:
    cursor = MagicMock()
    cursor.get_line.return_value = 1
    buffer = MagicMock()
    buffer.get_iter_at_mark.return_value = cursor
    buffer.get_line_count.return_value = 3
    app = SimpleNamespace(
        highlighter=MagicMock(),
        current_note="Note",
        buffer=buffer,
        changed_handler_id=23,
        _pending_marker_restore_id=0,
        _selection_marker_line=0,
        _pending_highlight_range=None,
        _pending_highlight_neighbours=False,
        _safe_source_remove=MagicMock(),
        _restore_marker_lines=MagicMock(),
    )

    with patch("main.GLib.timeout_add", return_value=71) as timeout_add:
        TokyoNotes._schedule_marker_restore(app)

    app.highlighter.restore_marker_range.assert_not_called()
    timeout_add.assert_called_once_with(5, app._restore_marker_lines, "Note", (0, 1), 1)
    assert app._pending_marker_restore_id == 71
    assert app._selection_marker_line == -1

    with patch("main.GLib.timeout_add", return_value=72) as timeout_add:
        TokyoNotes._restore_marker_lines(app, "Note", (0, 1), 1)

    app.highlighter.restore_marker_range.assert_called_once_with(0, 1, 1)
    assert timeout_add.call_args_list == [
        call(5, app._restore_marker_lines, "Note", (1,), 1)
    ]


def test_marker_restore_skips_lines_owned_by_pending_highlight() -> None:
    cursor = MagicMock()
    cursor.get_line.return_value = 2
    buffer = MagicMock()
    buffer.get_iter_at_mark.return_value = cursor
    buffer.get_line_count.return_value = 6
    app = SimpleNamespace(
        highlighter=MagicMock(),
        current_note="Note",
        buffer=buffer,
        changed_handler_id=23,
        _pending_marker_restore_id=0,
        _selection_marker_line=1,
        _pending_highlight_range=(1, 3),
        _pending_highlight_neighbours=False,
        _safe_source_remove=MagicMock(),
        _restore_marker_lines=MagicMock(),
    )

    with patch("main.GLib.timeout_add", return_value=81) as timeout_add:
        TokyoNotes._schedule_marker_restore(app)

    app.highlighter.restore_marker_range.assert_not_called()
    timeout_add.assert_called_once_with(5, app._restore_marker_lines, "Note", (1, 2), 2)

    with patch("main.GLib.timeout_add", return_value=82) as timeout_add:
        TokyoNotes._restore_marker_lines(app, "Note", (1, 2), 2)

    app.highlighter.restore_marker_range.assert_not_called()
    timeout_add.assert_called_once_with(5, app._restore_marker_lines, "Note", (2,), 2)


def test_selection_suspends_visibility_without_removing_marker_ranges() -> None:
    invisible = MagicMock()
    tag_table = MagicMock()
    tag_table.lookup.return_value = invisible
    app = SimpleNamespace(
        buffer=MagicMock(),
        _invisible_markers_suspended=False,
    )
    app.buffer.get_tag_table.return_value = tag_table

    TokyoNotes._set_invisible_markers_suspended(app, True)
    TokyoNotes._set_invisible_markers_suspended(app, False)

    assert invisible.set_property.call_args_list == [
        call("invisible", False),
        call("invisible", True),
    ]


def test_buffer_replacement_resumes_marker_visibility() -> None:
    buffer = MagicMock()
    editor = MagicMock()
    app = SimpleNamespace(
        changed_handler_id=0,
        buffer=buffer,
        editor=editor,
        highlighter=MagicMock(),
        _has_selection=True,
        _selection_marker_line=4,
        _set_invisible_markers_suspended=MagicMock(),
    )
    buffer.get_bounds.return_value = (MagicMock(), MagicMock())

    TokyoNotes._set_buffer_text(app, "replacement")

    app._set_invisible_markers_suspended.assert_called_once_with(False)
    assert app._has_selection is False
    assert app._selection_marker_line == -1
    buffer.set_text.assert_called_once_with("replacement")


def test_format_action_reconciles_missed_selection_collapse() -> None:
    insert_mark = MagicMock()
    buffer = MagicMock()
    buffer.get_has_selection.return_value = False
    buffer.get_insert.return_value = insert_mark
    app = SimpleNamespace(
        buffer=buffer,
        _has_selection=True,
        _on_mark_set=MagicMock(),
        _focus_text_view=MagicMock(),
    )

    TokyoNotes._finish_format_action(app)

    app._on_mark_set.assert_called_once_with(buffer, None, insert_mark)
    app._focus_text_view.assert_called_once_with()
