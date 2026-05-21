"""Markdown editor component with syntax highlighting and image support."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from ui.deadline_picker import DeadlinePicker
from ui.link_picker import LinkPicker

logger = logging.getLogger(__name__)

# Patterns checked against the text typed so far on the current line.
# Each entry is (compiled_regex, kind_string). Order matters: task must
# come before plain list so the more-specific pattern matches first.
_CONTINUATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(\s*-\s*\[[ xX]\])(.*)$"), "task"),
    (re.compile(r"^(\s*[-*+])\s+"),            "list"),
    (re.compile(r"^(\s*\d+\.)\s+"),            "ordered"),
]

# Matches the Unicode object-replacement character inserted by GTK for
# child anchors (embedded image widgets).
_ANCHOR_CHAR_RE: re.Pattern = re.compile("\ufffc")


class Editor(Gtk.Box):
    """Composite editor widget: toolbar + TextView + status bar."""

    def __init__(
        self,
        on_text_changed: Callable[[Gtk.TextBuffer], Any],
        on_cursor_moved: Callable[[Any, Any], Any],
        on_paste_clipboard: Callable[[Gtk.TextView], Any],
        toolbar: Gtk.Box,
        get_notes_callback: Callable[[], list[str]],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_notes_callback = get_notes_callback

        self.toolbar = toolbar
        self.append(self.toolbar)

        scrolled_editor = Gtk.ScrolledWindow()
        scrolled_editor.set_vexpand(True)

        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_left_margin(30)
        self.text_view.set_right_margin(80)
        self.text_view.set_top_margin(40)
        self.text_view.set_bottom_margin(40)
        self.text_view.set_pixels_above_lines(3)
        self.text_view.set_pixels_below_lines(3)
        self.text_view.set_pixels_inside_wrap(2)
        self.text_view.set_can_focus(True)
        self.text_view.set_receives_default(True)
        self.text_view.connect("paste-clipboard", on_paste_clipboard)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.text_view.add_controller(key_ctrl)
        self.controller = key_ctrl

        self.buffer: Gtk.TextBuffer = self.text_view.get_buffer()
        self.buffer.connect("insert-text", self.on_insert_text)
        self.changed_handler_id = self.buffer.connect("changed", on_text_changed)
        self.buffer.connect("notify::cursor-position", on_cursor_moved)

        scrolled_editor.set_child(self.text_view)

        self.editor_overlay = Gtk.Overlay()
        self.editor_overlay.set_child(scrolled_editor)

        self._lock_overlay = self._build_lock_overlay()
        self.editor_overlay.add_overlay(self._lock_overlay)

        self.append(self.editor_overlay)

        self.status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.status_bar.add_css_class("status-bar")
        self.status_bar.set_visible(False)

        self.stats_label = Gtk.Label(label="Words: 0 | Chars: 0 | Read: 0m")
        self.stats_label.add_css_class("stats-label")
        self.status_bar.append(self.stats_label)
        self.append(self.status_bar)

        self.image_anchors: list[Gtk.TextChildAnchor] = []
        self.is_updating_images: bool = False

    def _build_lock_overlay(self) -> Gtk.Box:
        overlay_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        overlay_box.add_css_class("lock-overlay")

        icon_path = Path(__file__).parent.parent / "assets" / "tokyo_notes_icon.svg"
        if icon_path.exists():
            icon = Gtk.Image.new_from_file(str(icon_path))
            icon.set_pixel_size(64)
            icon.add_css_class("lock-overlay-icon")
            overlay_box.append(icon)

        label = Gtk.Label(
            label="Click on this note in the sidebar to unlock.",
            xalign=0.5,
        )
        label.add_css_class("lock-overlay-label")
        overlay_box.append(label)

        overlay_box.set_visible(False)
        return overlay_box

    def set_editable(self, editable: bool) -> None:
        self.text_view.set_editable(editable)
        self.text_view.set_can_focus(editable)
        self.text_view.set_receives_default(editable)
        if hasattr(self, "toolbar"):
            self.toolbar.set_sensitive(editable)
        if hasattr(self, "_lock_overlay"):
            self._lock_overlay.set_visible(not editable)

    def _do_insert_continuation(self, prefix: str) -> bool:
        """Idle callback for inserting a list continuation prefix safely.

        This is a separate method so that the idle source can be traced to a
        GTK widget and does not hold a reference to a potentially
        destroyed buffer.
        """
        self.buffer.insert_at_cursor("\n" + prefix)
        return False

    # Key handling -- list continuation

    def on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Continue list items and task lists when Enter is pressed."""
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False

        buffer = self.text_view.get_buffer()
        cursor_iter = buffer.get_iter_at_mark(buffer.get_insert())

        # Read only the text from the start of the line to the cursor so
        # that patterns match the already-typed prefix, not the rest of the line.
        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_text = buffer.get_text(line_start, cursor_iter, False)

        for pattern, p_type in _CONTINUATION_PATTERNS:
            match = pattern.match(line_text)
            if not match:
                continue

            # group(1) is always the list marker (possibly with indent).
            marker_only = match.group(1)

            # Empty marker line -> break the list by removing the marker.
            if line_text.strip() == marker_only.strip():
                line_end = line_start.copy()
                line_end.forward_to_line_end()
                buffer.delete(line_start, line_end)
                return False

            if p_type == "task":
                # Reset checked state on continuation.
                new_prefix = re.sub(r"\[[xX ]\]", "[ ]", marker_only) + " "
            elif p_type == "ordered":
                new_prefix = re.sub(
                    r"(\d+)",
                    lambda m: str(int(m.group(1)) + 1),
                    marker_only,
                    count=1,
                ) + " "
            else:
                new_prefix = marker_only.rstrip() + " "

            GLib.idle_add(self._do_insert_continuation, new_prefix)
            return True  # suppress the default newline

        return False

    # Insert-text -- picker shortcuts

    def on_insert_text(
        self,
        buffer: Gtk.TextBuffer,
        location: Gtk.TextIter,
        text: str,
        length: int,
    ) -> None:
        """Trigger deadline, link, or variable picker on '@', '[[' , or '{{'."""
        if text == "@":
            GLib.idle_add(self.show_deadline_picker)
        elif text == "[" and location.get_offset() > 0:
            prev_iter = buffer.get_iter_at_offset(location.get_offset() - 1)
            if prev_iter.get_char() == "[":
                GLib.idle_add(self.show_link_picker)
        elif text == "{" and location.get_offset() > 0:
            prev_iter = buffer.get_iter_at_offset(location.get_offset() - 1)
            if prev_iter.get_char() == "{":
                GLib.idle_add(self.show_variable_picker)

    # Picker helpers

    def _popup_at_cursor(self, popover: Gtk.Popover) -> None:
        """Position *popover* at the current cursor location and show it.

        Uses get_cursor_locations() rather than get_iter_location() because
        the latter can crash with "byte index off the end of the line" when
        the cursor iter's internal byte offset doesn't align with GTK's
        B-tree representation (e.g. right after multi-byte or [[]] insertion).
        get_cursor_locations() is the safe API for this purpose.
        """
        strong, _weak = self.text_view.get_cursor_locations(None)
        # strong is in buffer coordinates; convert to widget window coordinates.
        bx, by = self.text_view.buffer_to_window_coords(
            Gtk.TextWindowType.TEXT, strong.x, strong.y
        )
        rect = Gdk.Rectangle()
        rect.x = bx
        rect.y = by
        rect.width = strong.width if strong.width > 0 else 1
        rect.height = strong.height if strong.height > 0 else 16
        popover.set_parent(self.text_view)
        popover.set_pointing_to(rect)
        popover.popup()

    def show_link_picker(self) -> None:
        """Show the wiki-link picker popover at the cursor."""
        def on_selected(note_name: str) -> None:
            self.buffer.insert_at_cursor(f"{note_name}]]")

        picker = LinkPicker(self.get_notes_callback(), on_selected, self.text_view)
        self._popup_at_cursor(picker)

    def show_deadline_picker(self) -> None:
        """Show the deadline picker popover at the cursor."""
        self._popup_at_cursor(DeadlinePicker(self.on_deadline_selected))

    def on_deadline_selected(self, deadline: str) -> None:
        self.buffer.insert_at_cursor(deadline)

    def show_variable_picker(self) -> None:
        """Show the variable picker popover at the cursor."""
        from ui.variable_picker import VariablePicker

        def on_selected(variable: str) -> None:
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            success, line_start = self.buffer.get_iter_at_line(cursor.get_line())
            if success:
                line_text = self.buffer.get_text(line_start, cursor, False)
                if line_text.endswith("{{"):
                    delete_start = cursor.copy()
                    delete_start.backward_chars(2)
                    self.buffer.delete(delete_start, cursor)
            self.buffer.insert_at_cursor(variable)

        picker = VariablePicker(on_selected, self.text_view)
        self._popup_at_cursor(picker)

    # Image rendering

    def _on_remote_image_loaded(
        self,
        file: Gio.File,
        result: Gio.AsyncResult,
        img_widget: Gtk.Image,
        label_widget: Gtk.Label,
    ) -> None:
        """Async callback: update the placeholder once the remote image arrives."""
        try:
            success, contents, _etag = file.load_contents_finish(result)
            if success:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(contents)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf:
                    img_widget.set_from_pixbuf(pixbuf)
                    label_widget.set_visible(False)
        except Exception as exc:
            logger.exception("Failed to load remote image: %s", exc)

    # Remaining methods omitted for brevity
