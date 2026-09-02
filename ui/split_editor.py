"""Split editor widget — two editors side by side with per-pane save timers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from core.highlighter import MarkdownHighlighter
from core.utils import strip_anchors_for_save
from ui.editor import Editor

if TYPE_CHECKING:
    from main import TokyoNotes

logger = logging.getLogger(__name__)


class _PaneState:
    __slots__ = (
        "side",
        "note_name",
        "editor",
        "highlighter",
        "save_timer",
        "container",
        "name_label",
        "has_images",
    )

    def __init__(self, side: str) -> None:
        self.side = side
        self.note_name: str | None = None
        self.editor: Editor | None = None
        self.highlighter: MarkdownHighlighter | None = None
        self.save_timer: int = 0
        self.container: Gtk.Box | None = None
        self.name_label: Gtk.Label | None = None
        self.has_images: bool = False


class SplitEditor(Gtk.Box):
    """Wraps two Editor instances in a Gtk.Paned with per-pane debounced saves."""

    def __init__(self, app: TokyoNotes) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._app = app
        self.left = _PaneState("left")
        self.right = _PaneState("right")
        self._active_side = "left"

        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_wide_handle(True)
        self._paned.set_hexpand(True)
        self._paned.set_vexpand(True)

        self.left.container = self._build_pane(self.left)
        self.right.container = self._build_pane(self.right)

        self._paned.set_start_child(self.left.container)
        self._paned.set_end_child(self.right.container)
        self.append(self._paned)

        self.left.editor.text_view.grab_focus()

    def _build_pane(self, info: _PaneState) -> Gtk.Box:
        app = self._app

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.set_margin_start(8)
        header.set_margin_end(8)
        header.set_margin_top(6)
        header.set_margin_bottom(6)

        name_label = Gtk.Label(label="", xalign=0)
        name_label.set_hexpand(True)
        name_label.add_css_class("split-pane-title")
        header.append(name_label)

        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("split-close-btn")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.connect("clicked", lambda _: self._close_pane(info.side))
        header.append(close_btn)

        container.append(header)
        info.name_label = name_label

        editor = Editor(
            on_text_changed=lambda _buf: self._on_text_changed(info.side),
            on_cursor_moved=app.on_cursor_moved,
            on_paste_clipboard=app.actions.on_paste_clipboard,
            toolbar=None,
            get_notes_callback=app.notes_manager.get_notes,
        )
        editor._config_manager = app.cfg
        editor._get_current_note = lambda info=info: info.note_name
        container.append(editor)
        info.editor = editor

        highlighter = MarkdownHighlighter(
            editor.buffer, app.theme_manager, app.cfg.get("theme")
        )
        highlighter.highlight()
        if app.spell_checker:
            highlighter.set_spell_checker(
                app.spell_checker,
                enabled=app.cfg.get("spell_check_enabled", True),
            )
        info.highlighter = highlighter
        editor.highlighter = highlighter
        editor.buffer.connect(
            "mark-set",
            lambda buffer, location, mark: self._on_pane_mark_set(
                info, buffer, location, mark
            ),
        )

        gesture = Gtk.GestureClick.new()
        gesture.set_button(1)
        gesture.connect("pressed", app.on_click_pressed)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        editor.text_view.add_controller(gesture)

        scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL,
        )
        scroll_ctrl.connect("scroll", app._on_editor_scroll)
        scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        editor.text_view.add_controller(scroll_ctrl)

        focus_ctrl = Gtk.EventControllerFocus()
        focus_ctrl.connect("enter", lambda *_: self._on_focus(info.side))
        editor.text_view.add_controller(focus_ctrl)

        return container

    def _on_text_changed(self, side: str) -> None:
        app = self._app
        if app.is_loading:
            return
        info = self.left if side == "left" else self.right
        if info.note_name is None:
            return

        info.highlighter.invalidate_structure_cache()

        edit_range, syntax_range = info.editor.consume_edit_ranges()
        if edit_range is None:
            cursor = info.editor.buffer.get_iter_at_mark(
                info.editor.buffer.get_insert()
            )
            edit_range = (cursor.get_line(), cursor.get_line())

        app._buffer_mod_counter += 1
        app._reset_lock_timer_on_activity()
        if side == self._active_side:
            app._pending_highlight_range = app.lifecycle._merge_line_range(
                app._pending_highlight_range,
                edit_range,
            )
            if syntax_range is not None:
                app._pending_highlight_neighbours = True
            app._reschedule(
                "highlight_timeout_id",
                100,
                app.do_delayed_highlight,
            )

            if info.highlighter.spell_check_enabled:
                app._pending_spell_range = app.lifecycle._merge_line_range(
                    app._pending_spell_range,
                    edit_range,
                )
                app._reschedule(
                    "spell_check_timeout_id",
                    750,
                    app.lifecycle.do_delayed_spell_check,
                )

            media_dirty = info.editor.consume_media_syntax_dirty()
            image_reference_dirty = info.editor.consume_image_reference_dirty()
            has_image_syntax = media_dirty and app.lifecycle._has_image_syntax(
                info.editor.buffer
            )
            if has_image_syntax or image_reference_dirty:
                info.has_images = True
                app._has_images = True
                info.editor._warm_image_cache(
                    Path(app.notes_manager.notes_dir).resolve(),
                    app_width=app.cfg.get("embed_width", 0),
                )
            if media_dirty:
                info.editor._last_image_text_hash = ""
            if (
                media_dirty or image_reference_dirty
            ) and not info.editor._image_update_running:
                app._reschedule(
                    "image_timeout_id",
                    500,
                    app.do_delayed_images,
                )
        else:
            info.editor.consume_media_syntax_dirty()
            info.editor.consume_image_reference_dirty()

        if info.save_timer:
            GLib.source_remove(info.save_timer)
        info.save_timer = GLib.timeout_add(
            1000,
            self._do_save,
            side,
        )

    def _on_pane_mark_set(
        self,
        info: _PaneState,
        buffer: Gtk.TextBuffer,
        location: Gtk.TextIter,
        mark: Gtk.TextMark,
    ) -> None:
        """Route selection events only through the pane that owns them."""
        app = self._app
        if info.editor.text_view.has_focus() and self._active_side != info.side:
            self._on_focus(info.side)
        if self._active_side != info.side or app.buffer is not buffer:
            return
        app._on_mark_set(buffer, location, mark)

    def _do_save(self, side: str) -> bool:
        info = self.left if side == "left" else self.right
        info.save_timer = 0

        if not info.note_name or info.note_name.startswith(".template:"):
            return False

        app = self._app
        from core.services import save_note_content

        content = (
            strip_anchors_for_save(info.editor.buffer)
            if info.has_images
            else info.editor.get_source_text()
        )
        if not content.strip():
            return False

        save_note_content(
            note_name=info.note_name,
            content=content,
            is_encrypted=app.notes_manager.is_encrypted(info.note_name),
            derive_encryption_key=app._derive_encryption_key,
            notes_manager=app.notes_manager,
            session_password_bytes=app._session_password_bytes,
            on_done=lambda note_name=info.note_name: app.lifecycle._maybe_git_commit(
                note_name
            ),
        )
        return False

    def flush_saves(self, *, commit_history: bool = True) -> None:
        for info in (self.left, self.right):
            if info.save_timer:
                GLib.source_remove(info.save_timer)
                info.save_timer = 0
            if info.note_name and not info.note_name.startswith(".template:"):
                self._do_save_now(info, commit_history=commit_history)

    def _do_save_now(self, info: _PaneState, *, commit_history: bool = True) -> None:
        app = self._app
        from core.services import save_note_content

        content = (
            strip_anchors_for_save(info.editor.buffer)
            if info.has_images
            else info.editor.get_source_text()
        )
        if not content.strip():
            return

        save_note_content(
            note_name=info.note_name,
            content=content,
            is_encrypted=app.notes_manager.is_encrypted(info.note_name),
            derive_encryption_key=app._derive_encryption_key,
            notes_manager=app.notes_manager,
            session_password_bytes=app._session_password_bytes,
        )
        if commit_history:
            app.lifecycle._maybe_git_commit(info.note_name, immediate=True)

    def _on_focus(self, side: str) -> None:
        app = self._app
        app._save_current_cursor()

        # Marker visibility is a TextTag property on the active pane's buffer;
        # restore it before redirecting app.buffer to the other pane.
        app._set_invisible_markers_suspended(False)

        other = self.right if side == "left" else self.left
        if other.editor and other.editor.find_bar.is_visible():
            other.editor.find_bar.close()

        info = self.left if side == "left" else self.right
        self._active_side = side

        app.buffer = info.editor.buffer
        app.text_view = info.editor.text_view
        app.current_note = info.note_name
        app.editor = info.editor
        app.changed_handler_id = info.editor.changed_handler_id
        app.highlighter = info.highlighter
        app._has_selection = info.editor.buffer.get_has_selection()
        if app._has_selection:
            selection_bound = info.editor.buffer.get_iter_at_mark(
                info.editor.buffer.get_selection_bound()
            )
            app._selection_marker_line = selection_bound.get_line()
            app._set_invisible_markers_suspended(True)
        app._has_images = info.has_images
        if info.has_images and not info.editor._image_update_running:
            app._reschedule("image_timeout_id", 100, app.do_delayed_images)

    def _close_pane(self, side: str) -> None:
        app = self._app
        other = self.right if side == "left" else self.left

        if self.left.note_name:
            app._cursor_positions[self.left.note_name] = (
                self.left.editor.buffer.get_property("cursor-position")
            )
        if self.right.note_name:
            app._cursor_positions[self.right.note_name] = (
                self.right.editor.buffer.get_property("cursor-position")
            )
        self.flush_saves()

        app.content_stack.set_visible_child_name("editor")
        app.content_stack.remove(self)
        app.split_editor = None

        restored = app._single_editor_ref
        app.buffer = restored.buffer
        app.text_view = restored.text_view
        app.changed_handler_id = restored.changed_handler_id
        app.editor = restored
        app.editor._config_manager = app.cfg
        app.editor._get_current_note = lambda: app.current_note
        app.highlighter = MarkdownHighlighter(
            app.buffer, app.theme_manager, app.cfg.get("theme")
        )
        app.highlighter.highlight()
        if app.spell_checker:
            app.highlighter.set_spell_checker(
                app.spell_checker,
                enabled=app.cfg.get("spell_check_enabled", True),
            )
        app.editor.highlighter = app.highlighter

        if other.note_name and not other.note_name.startswith(".template:"):
            self._select_and_load_note(app, other.note_name, other.editor.buffer)

        app.nav.update_header_ui(
            app.current_note or "Tokyo Notes",
            is_editor=True,
        )
        app.sidebar.set_active_view("editor")
        app._set_backlinks_visible(True)

    @staticmethod
    def _restore_cursor_in_pane(
        info: _PaneState, note_name: str, app: TokyoNotes
    ) -> None:
        cursor_pos = app._cursor_positions.get(note_name)
        if cursor_pos is not None and cursor_pos <= info.editor.buffer.get_char_count():
            it = info.editor.buffer.get_iter_at_offset(cursor_pos)
            info.editor.buffer.place_cursor(it)

            # Use mark for stable scrolling
            mark = info.editor.buffer.create_mark(None, it, True)
            GLib.idle_add(
                lambda: (
                    info.editor.text_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0),
                    info.editor.buffer.delete_mark(mark),
                )
            )

    @staticmethod
    def _select_and_load_note(
        app: TokyoNotes, note_name: str, buffer: Gtk.TextBuffer
    ) -> None:
        app.current_note = note_name
        if app.notes_manager.is_encrypted(note_name) and not app._is_session_locked:
            try:
                app._load_encrypted_note(note_name)
            except Exception:
                logger.exception("Failed to load encrypted note after split close")
            return

        content = app.notes_manager.read_plain(note_name) or ""
        app._set_buffer_text(content)
        cursor_pos = app._cursor_positions.get(note_name)
        if cursor_pos is not None and cursor_pos <= app.buffer.get_char_count():
            it = app.buffer.get_iter_at_offset(cursor_pos)
            app.buffer.place_cursor(it)
            GLib.idle_add(
                lambda: app.text_view.scroll_to_iter(it, 0.0, False, 0.0, 0.0)
            )
        GLib.idle_add(app.text_view.grab_focus)
        if app.highlighter:
            app.highlighter.highlight()
            app._full_pass_complete = True
        app.editor.set_editable(True)

    def load_note_into_focused_pane(self, note_name: str) -> None:
        """Load a note into the currently focused pane (sidebar click in split mode)."""
        info = self.left if self._active_side == "left" else self.right
        self._load_pane(info, note_name)
        GLib.idle_add(info.editor.text_view.grab_focus)

    def load_notes(self, left_note: str, right_note: str) -> None:
        """Load notes into both panes."""
        self._load_pane(self.left, left_note)
        self._load_pane(self.right, right_note)
        self.left.editor.text_view.grab_focus()

    def _load_pane(self, info: _PaneState, note_name: str) -> None:
        app = self._app
        app._save_current_cursor()
        info.note_name = note_name
        info.name_label.set_label(note_name)
        app.current_note = note_name

        if app.notes_manager.is_encrypted(note_name) and app._is_session_locked:
            info.has_images = False
            info.editor.set_editable(False)
            return

        if app.notes_manager.is_encrypted(note_name):
            buf = info.editor.buffer
            loaded = True
            try:
                buf.handler_block(info.editor.changed_handler_id)
                app._load_encrypted_note_to_buffer(note_name, buf)
            except Exception:
                loaded = False
                logger.exception("Failed to load encrypted note in split pane")
                info.editor.set_editable(False)
            finally:
                buf.handler_unblock(info.editor.changed_handler_id)
            if not loaded:
                return
            content = buf.get_text(*buf.get_bounds(), True)
            info.has_images = "![" in content
            info.editor.reset_source_text(content)
            info.highlighter.invalidate_structure_cache()
            info.highlighter.highlight()
            info.editor.set_editable(True)
            self._restore_cursor_in_pane(info, note_name, app)
            return

        content = app.notes_manager.read_plain(note_name) or ""
        info.has_images = "![" in content
        info.editor.close_pickers()
        info.editor.clear_images()

        buf = info.editor.buffer
        handlers = []
        if hasattr(info.editor, "changed_handler_id"):
            handlers.append((buf, info.editor.changed_handler_id))
        if hasattr(info.editor, "cursor_handler_id"):
            handlers.append((buf, info.editor.cursor_handler_id))

        for b, h in handlers:
            b.handler_block(h)
        try:
            start, end = buf.get_bounds()
            buf.remove_all_tags(start, end)
            buf.set_text(content)
            info.editor.reset_source_text(content)
            info.highlighter.invalidate_structure_cache()
        finally:
            for b, h in reversed(handlers):
                b.handler_unblock(h)

        self._restore_cursor_in_pane(info, note_name, app)
        info.editor.set_editable(True)
        info.highlighter.highlight()
        app._full_pass_complete = True
