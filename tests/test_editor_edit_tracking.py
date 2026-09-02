"""Unit tests for editor-side edit tracking without constructing a window."""

import threading
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.editor import Editor


def _editor(source: str = "abc") -> SimpleNamespace:
    return SimpleNamespace(
        _source_text=source,
        _edit_line_range=None,
        _syntax_line_range=None,
        _media_syntax_dirty=False,
        _image_reference_dirty=False,
        _merge_line_range=Editor._merge_line_range,
        _picker_open=True,
        image_anchors=[],
    )


def test_plain_insert_updates_cache_without_marking_syntax() -> None:
    editor = _editor()
    buffer = MagicMock()
    buffer.get_text.return_value = "abc"
    location = MagicMock()
    location.get_line.return_value = 4
    location.get_line_offset.return_value = 1
    location.get_offset.return_value = 1

    Editor.on_insert_text(editor, buffer, location, "x", 1)

    assert editor._source_text == "axbc"
    assert editor._edit_line_range == (4, 4)
    assert editor._syntax_line_range is None


def test_markdown_insert_tracks_all_pasted_lines_as_syntax() -> None:
    editor = _editor()
    buffer = MagicMock()
    buffer.get_text.return_value = "abc"
    location = MagicMock()
    location.get_line.return_value = 4
    location.get_line_offset.return_value = 1
    location.get_offset.return_value = 1

    Editor.on_insert_text(editor, buffer, location, "\n# heading", 10)

    assert editor._edit_line_range == (4, 5)
    assert editor._syntax_line_range == (4, 5)


def test_pasted_image_reference_is_tracked_even_with_trailing_newline() -> None:
    editor = _editor()
    buffer = MagicMock()
    buffer.get_text.return_value = "abc"
    location = MagicMock()
    location.get_line.return_value = 4
    location.get_line_offset.return_value = 1
    location.get_offset.return_value = 1

    Editor.on_insert_text(editor, buffer, location, "![alt](photo.png)\n", 18)

    assert editor._image_reference_dirty is True


def test_delete_updates_cache_and_always_marks_syntax() -> None:
    editor = _editor("notebook")
    buffer = MagicMock()
    buffer.get_text.return_value = "notebook"
    start = MagicMock()
    start.get_line.return_value = 2
    start.get_offset.return_value = 5
    end = MagicMock()
    end.get_offset.return_value = 9

    Editor._on_delete_range_for_media(editor, buffer, start, end)

    assert editor._source_text == "noteb"
    assert editor._edit_line_range == (2, 2)
    assert editor._syntax_line_range == (2, 2)


def test_image_cache_warming_decodes_off_the_main_loop(tmp_path) -> None:
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"placeholder")
    buffer = MagicMock()
    buffer.get_bounds.return_value = (MagicMock(), MagicMock())
    buffer.get_text.return_value = "![alt|small](photo.png)"
    editor = SimpleNamespace(
        buffer=buffer,
        text_view=MagicMock(),
        _image_cache_lock=threading.RLock(),
        _image_pixbuf_cache=OrderedDict(),
        _image_cache_inflight=set(),
        _load_image_pixbuf=MagicMock(),
    )
    editor.text_view.get_allocated_width.return_value = 800

    with (
        patch("ui.editor.threading.Thread") as thread_cls,
        patch("ui.editor.GLib.idle_add") as idle_add,
    ):
        Editor._warm_image_cache(editor, tmp_path)
        decode = thread_cls.call_args.kwargs["target"]
        decode()

    thread_cls.assert_called_once_with(target=decode, daemon=True)
    thread_cls.return_value.start.assert_called_once_with()
    idle_add.assert_not_called()
    editor._load_image_pixbuf.assert_called_once_with(image_path, 600, 600)
    assert editor._image_cache_inflight == set()


def test_image_cache_warming_ignores_directory_references(tmp_path) -> None:
    directory = tmp_path / ".images"
    directory.mkdir()
    buffer = MagicMock()
    buffer.get_bounds.return_value = (MagicMock(), MagicMock())
    buffer.get_text.return_value = "![not an image](.images)"
    editor = SimpleNamespace(
        buffer=buffer,
        text_view=MagicMock(),
        _image_cache_lock=threading.RLock(),
        _image_pixbuf_cache=OrderedDict(),
        _image_cache_inflight=set(),
        _load_image_pixbuf=MagicMock(),
    )
    editor.text_view.get_allocated_width.return_value = 800

    with patch("ui.editor.threading.Thread") as thread_cls:
        Editor._warm_image_cache(editor, tmp_path)

    thread_cls.assert_not_called()
    editor._load_image_pixbuf.assert_not_called()
