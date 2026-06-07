"""Markdown editor component with syntax highlighting and image support."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from ui.deadline_picker import DeadlinePicker
from ui.link_picker import LinkPicker
from ui.slash_picker import SlashPicker

logger = logging.getLogger(__name__)

# Patterns checked against the text typed so far on the current line.
# Each entry is (compiled_regex, kind_string). Order matters: task must
# come before plain list so the more-specific pattern matches first.
_CONTINUATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(\s*-\s*\[[ xX]\])(.*)$"), "task"),
    (re.compile(r"^(\s*[-*+])\s+"), "list"),
    (re.compile(r"^(\s*\d+\.)\s+"), "ordered"),
]


def resolve_image_path(notes_dir: Path, image_path: str) -> Path | None:
    """Resolve an image path only if it stays inside *notes_dir*."""
    notes_dir_resolved = notes_dir.resolve()
    full_path = (notes_dir / image_path).resolve()
    if not full_path.is_relative_to(notes_dir_resolved):
        return None
    return full_path


class Editor(Gtk.Box):
    """Composite editor widget: toolbar + TextView + status bar."""

    def __init__(
        self,
        on_text_changed: Callable[[Gtk.TextBuffer], Any],
        on_cursor_moved: Callable[[Any, Any], Any],
        on_paste_clipboard: Callable[[Gtk.TextView], Any],
        toolbar: Gtk.Box | None = None,
        get_notes_callback: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_notes_callback = get_notes_callback or (lambda: [])

        if toolbar is not None:
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
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
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
        self.image_widgets: list[Gtk.Widget] = []
        self.is_updating_images: bool = False
        self._picker_open: bool = False

    def _build_lock_overlay(self) -> Gtk.Box:
        overlay_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
        )
        overlay_box.add_css_class("lock-overlay")

        icon_path = Path(__file__).parent.parent / "assets" / "tokyo_notes_icon.svg"
        if icon_path.exists():
            icon = Gtk.Image.new_from_file(str(icon_path))
            icon.set_pixel_size(64)
            icon.add_css_class("lock-overlay-icon")
            overlay_box.append(icon)

        label = Gtk.Label(
            label="This note is private. Click the sidebar row"
            " and enter your password to unlock.",
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

    # Key handling -- list continuation + auto-pair

    def _auto_pair_delimiter(self, buffer: Gtk.TextBuffer, keyval: int) -> bool:
        """Wrap selection in delimiters or auto-close pair on delimiter key."""
        pairs = {
            Gdk.KEY_asterisk: ("*", "*"),
            Gdk.KEY_underscore: ("_", "_"),
            Gdk.KEY_grave: ("`", "`"),
            Gdk.KEY_asciitilde: ("~~", "~~"),
            Gdk.KEY_parenleft: ("(", ")"),
            Gdk.KEY_bracketleft: ("[", "]"),
            Gdk.KEY_quotedbl: ('"', '"'),
        }
        if keyval not in pairs:
            return False

        prefix, suffix = pairs[keyval]

        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            text = buffer.get_text(start, end, True)
            buffer.delete(start, end)
            buffer.insert_at_cursor(f"{prefix}{text}{suffix}")
            return True

        # Don't auto-close [ to avoid breaking the [[ wiki-link picker
        if keyval == Gdk.KEY_bracketleft:
            return False

        buffer.insert_at_cursor(f"{prefix}{suffix}")
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        cursor.backward_chars(len(suffix))
        buffer.place_cursor(cursor)
        return True

    def on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Continue list items, auto-pair/close delimiters, and handle Enter."""
        # When a picker popover is open, its entry widgets need to receive
        # keystrokes unimpeded.  This controller runs at CAPTURE phase, so it
        # sees events before the popover's children do — returning False here
        # lets the event propagate normally to whichever widget has focus.
        if self._picker_open:
            return False

        buffer = self.text_view.get_buffer()

        if keyval in (
            Gdk.KEY_asterisk,
            Gdk.KEY_underscore,
            Gdk.KEY_grave,
            Gdk.KEY_asciitilde,
            Gdk.KEY_parenleft,
            Gdk.KEY_bracketleft,
            Gdk.KEY_quotedbl,
        ):
            if self._auto_pair_delimiter(buffer, keyval):
                return True

        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False

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
                new_prefix = (
                    re.sub(
                        r"(\d+)",
                        lambda m: str(int(m.group(1)) + 1),
                        marker_only,
                        count=1,
                    )
                    + " "
                )
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
        """Trigger pickers on '@', '[[' , '{{', '/'."""
        if self._picker_open:
            return

        if text == "@":
            self._picker_open = True
            GLib.idle_add(self.show_deadline_picker)
        elif text == "[" and location.get_offset() > 0:
            prev_iter = buffer.get_iter_at_offset(location.get_offset() - 1)
            if prev_iter.get_char() == "[":
                self._picker_open = True
                GLib.idle_add(self.show_link_picker)
        elif text == "{" and location.get_offset() > 0:
            prev_iter = buffer.get_iter_at_offset(location.get_offset() - 1)
            if prev_iter.get_char() == "{":
                self._picker_open = True
                GLib.idle_add(self.show_variable_picker)
        elif text == "/":
            # Only trigger slash-command menu when preceded by whitespace or
            # at start of buffer, to avoid interfering with URLs and paths.
            if location.get_offset() == 0:
                self._picker_open = True
                GLib.idle_add(self.show_slash_picker)
            else:
                prev_char = buffer.get_text(
                    buffer.get_iter_at_offset(location.get_offset() - 1),
                    location,
                    False,
                )
                if prev_char and prev_char[0] in (" ", "\t", "\n", ""):
                    self._picker_open = True
                    GLib.idle_add(self.show_slash_picker)

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
        self._picker_open = True

        def on_selected(note_name: str) -> None:
            self._picker_open = False
            self.buffer.insert_at_cursor(f"{note_name}]]")

        picker = LinkPicker(self.get_notes_callback(), on_selected, self.text_view)
        picker.connect("closed", lambda *_: setattr(self, "_picker_open", False))
        self._popup_at_cursor(picker)

    def show_deadline_picker(self) -> None:
        """Show the deadline picker popover at the cursor."""
        self._picker_open = True
        picker = DeadlinePicker(self.on_deadline_selected)
        picker.connect("closed", lambda *_: setattr(self, "_picker_open", False))
        self._popup_at_cursor(picker)

    def on_deadline_selected(self, deadline: str) -> None:
        self.buffer.insert_at_cursor(deadline)

    def show_variable_picker(self) -> None:
        """Show the variable picker popover at the cursor."""
        self._picker_open = True
        from ui.variable_picker import VariablePicker

        def on_selected(variable: str) -> None:
            self._picker_open = False
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            try:
                result = self.buffer.get_iter_at_line(cursor.get_line())
                line_start = result[1] if isinstance(result, tuple) else result
            except (TypeError, IndexError):
                self.buffer.insert_at_cursor(variable)
                return
            line_text = self.buffer.get_text(line_start, cursor, False)
            if line_text.endswith("{{"):
                delete_start = cursor.copy()
                delete_start.backward_chars(2)
                self.buffer.delete(delete_start, cursor)
            self.buffer.insert_at_cursor(variable)

        picker = VariablePicker(on_selected, self.text_view)
        picker.connect("closed", lambda *_: setattr(self, "_picker_open", False))
        self._popup_at_cursor(picker)

    def _remove_last_slash(self) -> None:
        """Delete the / character that triggered the slash picker."""
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if cursor.get_offset() > 0:
            prev = cursor.copy()
            prev.backward_chars(1)
            if self.buffer.get_text(prev, cursor, False) == "/":
                self.buffer.delete(prev, cursor)

    def show_slash_picker(self) -> None:
        """Show the slash-command picker popover at the cursor."""
        self._picker_open = True
        self._pending_slash_action = None

        def on_selected(command_label: str, insert_text: str) -> None:
            self._picker_open = False
            self._remove_last_slash()
            if command_label == "Deadline":
                self.text_view._skip_focus_restore = True
                self._pending_slash_action = "deadline"
                return
            if command_label in ("Code Block", "Flashcard", "Divider"):
                self.buffer.insert_at_cursor(insert_text)
                if command_label in ("Code Block", "Flashcard"):
                    cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
                    lines = insert_text.count("\n")
                    if lines > 0:
                        cursor.backward_lines(lines)
                        cursor.forward_to_line_end()
                        self.buffer.place_cursor(cursor)
                return
            if insert_text.endswith("]]"):
                self.buffer.insert_at_cursor(insert_text)
            elif insert_text in (
                "*italic*",
                "**bold**",
                "~~text~~",
                "`code`",
                "[text](url)",
                "![alt](url)",
            ):
                self.buffer.insert_at_cursor(insert_text)
                cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
                midpoint = len(insert_text) // 2
                cursor.backward_chars(midpoint)
                self.buffer.place_cursor(cursor)
            else:
                self.buffer.insert_at_cursor(insert_text)

        picker = SlashPicker(on_selected, self.text_view)
        picker.connect("closed", self._on_slash_closed)
        picker.connect("closed", lambda *_: setattr(self, "_picker_open", False))
        self._popup_at_cursor(picker)

    def _on_slash_closed(self, picker: Gtk.Popover) -> None:
        GLib.idle_add(lambda: setattr(self.text_view, "_skip_focus_restore", False))
        if self._pending_slash_action == "deadline":
            self._pending_slash_action = None
            GLib.idle_add(self._trigger_deadline_after_slash)

    def _trigger_deadline_after_slash(self) -> None:
        self.buffer.insert_at_cursor("@")

    # Image rendering

    def update_images(self, notes_dir: Path) -> None:
        """Scan buffer for markdown image syntax and render inline widgets."""
        if self.is_updating_images:
            return
        self.is_updating_images = True
        try:
            # Delete old child anchors from the buffer.
            for anchor in reversed(self.image_anchors):
                result = self.buffer.get_iter_at_child_anchor(anchor)
                it = result[1] if isinstance(result, tuple) else result
                if it is None:
                    continue
                it2 = it.copy()
                it2.forward_char()
                self.buffer.delete(it, it2)
            self.image_widgets.clear()
            self.image_anchors.clear()

            image_re = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
            start, end = self.buffer.get_bounds()
            text = self.buffer.get_text(start, end, False)

            matches = list(image_re.finditer(text))
            for match in reversed(matches):
                img_path = match.group(2)
                offset = match.end()

                img_iter = self.buffer.get_iter_at_offset(offset)
                anchor = self.buffer.create_child_anchor(img_iter)
                self.image_anchors.append(anchor)

                img_widget = Gtk.Image()
                img_widget.set_hexpand(True)
                img_widget.set_size_request(-1, 150)
                img_widget.add_css_class("inline-image")

                full_path = resolve_image_path(notes_dir, img_path)
                if full_path is None:
                    continue
                if full_path.exists():
                    try:
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                            str(full_path), 400, -1, True
                        )
                        img_widget.set_from_pixbuf(pixbuf)
                    except Exception as exc:
                        logger.warning("Failed to load image %s: %s", img_path, exc)
                        broken_path = (
                            Path(__file__).parent.parent
                            / "assets"
                            / "editor"
                            / "broken-image.svg"
                        )
                        img_widget.set_from_file(str(broken_path))
                else:
                    broken_path = (
                        Path(__file__).parent.parent
                        / "assets"
                        / "editor"
                        / "broken-image.svg"
                    )
                    img_widget.set_from_file(str(broken_path))

                self.text_view.add_child_at_anchor(img_widget, anchor)
                self.image_widgets.append(img_widget)

            GLib.idle_add(self._finish_image_update)
        except Exception:
            self.is_updating_images = False
            raise

    def _finish_image_update(self) -> bool:
        self.is_updating_images = False
        return False
