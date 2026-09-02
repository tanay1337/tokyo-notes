"""Regression tests for split-editor edit coordination."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from core.note_lifecycle import NoteLifecycleManager
from ui.split_editor import SplitEditor


def _split_fixture() -> tuple[SimpleNamespace, MagicMock, SimpleNamespace]:
    app = MagicMock()
    app.is_loading = False
    app._buffer_mod_counter = 0
    app._pending_highlight_range = None
    app._pending_highlight_neighbours = False
    app._pending_spell_range = None
    app._has_images = False
    app.notes_manager.notes_dir = "/tmp/notes"
    app.lifecycle._merge_line_range.side_effect = NoteLifecycleManager._merge_line_range
    app.lifecycle._has_image_syntax.return_value = False

    editor = MagicMock()
    editor.consume_edit_ranges.return_value = ((3, 4), (3, 4))
    editor.consume_media_syntax_dirty.return_value = True
    editor.consume_image_reference_dirty.return_value = True
    editor._image_update_running = False
    highlighter = MagicMock()
    highlighter.spell_check_enabled = True
    info = SimpleNamespace(
        side="left",
        note_name="Sample Note",
        editor=editor,
        highlighter=highlighter,
        save_timer=0,
        has_images=False,
    )
    split = SimpleNamespace(
        _app=app,
        _active_side="left",
        left=info,
        right=MagicMock(),
        _do_save=MagicMock(),
    )
    return split, app, info


def test_active_pane_schedules_incremental_work_and_image_render() -> None:
    split, app, info = _split_fixture()

    with patch("ui.split_editor.GLib.timeout_add", return_value=91):
        SplitEditor._on_text_changed(split, "left")

    assert app._pending_highlight_range == (3, 4)
    assert app._pending_highlight_neighbours is True
    assert app._pending_spell_range == (3, 4)
    assert info.has_images is True
    assert app._has_images is True
    scheduled = [call.args[0] for call in app._reschedule.call_args_list]
    assert scheduled == [
        "highlight_timeout_id",
        "spell_check_timeout_id",
        "image_timeout_id",
    ]
    assert info.save_timer == 91


def test_plain_split_save_uses_cached_source() -> None:
    split, app, info = _split_fixture()
    info.has_images = False
    info.editor.get_source_text.return_value = "# Sample Note\n\nExample"
    app.notes_manager.is_encrypted.return_value = False

    with patch("core.services.save_note_content") as save_note:
        SplitEditor._do_save(split, "left")

    assert save_note.call_args.kwargs["content"] == "# Sample Note\n\nExample"
    info.editor.buffer.get_slice.assert_not_called()


def test_inactive_pane_mark_event_is_ignored() -> None:
    split, app, info = _split_fixture()
    split._active_side = "right"
    info.editor.text_view.has_focus.return_value = False
    buffer = MagicMock()
    app.buffer = MagicMock()

    SplitEditor._on_pane_mark_set(
        split,
        info,
        buffer,
        MagicMock(),
        MagicMock(),
    )

    app._on_mark_set.assert_not_called()


def test_focused_pane_becomes_active_before_forwarding_mark() -> None:
    split, app, info = _split_fixture()
    split._active_side = "right"
    info.editor.text_view.has_focus.return_value = True
    buffer = info.editor.buffer
    app.buffer = buffer
    split._on_focus = MagicMock(
        side_effect=lambda side: setattr(split, "_active_side", side)
    )
    location = MagicMock()
    mark = MagicMock()

    SplitEditor._on_pane_mark_set(split, info, buffer, location, mark)

    split._on_focus.assert_called_once_with("left")
    app._on_mark_set.assert_called_once_with(buffer, location, mark)


def test_focus_switch_transfers_suspended_marker_visibility() -> None:
    split, app, info = _split_fixture()
    split._active_side = "right"
    info.editor.buffer.get_has_selection.return_value = True
    selection_bound = MagicMock()
    selection_bound.get_line.return_value = 4
    info.editor.buffer.get_iter_at_mark.return_value = selection_bound

    SplitEditor._on_focus(split, "left")

    assert app._set_invisible_markers_suspended.call_args_list == [
        call(False),
        call(True),
    ]
    assert app.buffer is info.editor.buffer
    assert app._selection_marker_line == 4
