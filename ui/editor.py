"""Markdown editor component with syntax highlighting and image support."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

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

        # ---- Toolbar ----
        self.toolbar = toolbar
        self.append(self.toolbar)

        # ---- Text view ----
        scrolled_editor = Gtk.ScrolledWindow()
        scrolled_editor.set_vexpand(True)

        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_left_margin(30)
        self.text_view.set_right_margin(80)
        self.text_view.set_top_margin(40)
        self.text_view.set_bottom_margin(40)
        # Restore comfortable line spacing — equivalent to ~1.5× line height.
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
        self.append(scrolled_editor)

        # ---- Status bar ----
        self.status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.status_bar.add_css_class("status-bar")
        self.status_bar.set_visible(False)

        self.stats_label = Gtk.Label(label="Words: 0 | Chars: 0 | Read: 0m")
        self.stats_label.add_css_class("stats-label")
        self.status_bar.append(self.stats_label)
        self.append(self.status_bar)

        self.image_anchors: list[Gtk.TextChildAnchor] = []
        self.is_updating_images: bool = False

    # ------------------------------------------------------------------ #
    # Key handling — list continuation
    # ------------------------------------------------------------------ #

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

            # Empty marker line → break the list by removing the marker.
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

            GLib.idle_add(lambda p=new_prefix: buffer.insert_at_cursor("\n" + p))
            return True  # suppress the default newline

        return False

    # ------------------------------------------------------------------ #
    # Insert-text — picker shortcuts
    # ------------------------------------------------------------------ #

    def on_insert_text(
        self,
        buffer: Gtk.TextBuffer,
        location: Gtk.TextIter,
        text: str,
        length: int,
    ) -> None:
        """Trigger deadline or link picker on '@' or '[['."""
        if text == "@":
            GLib.idle_add(self.show_deadline_picker)
        elif text == "[" and location.get_offset() > 0:
            # insert-text fires before insertion; check the character sitting
            # just before the insertion point to detect the second '['.
            prev_iter = buffer.get_iter_at_offset(location.get_offset() - 1)
            if prev_iter.get_char() == "[":
                GLib.idle_add(self.show_link_picker)

    # ------------------------------------------------------------------ #
    # Picker helpers
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Image rendering
    # ------------------------------------------------------------------ #

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
                stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(contents))
                texture = Gdk.Texture.new_from_stream(stream)
                img_widget.set_from_paintable(texture)
                img_widget.set_pixel_size(500)
                label_widget.set_label("")
                label_widget.set_visible(False)
        except Exception:
            logger.exception("Failed to load remote image")
            img_widget.set_from_icon_name("image-missing-symbolic")
            label_widget.set_label("Failed to load")

    def update_images(self, note_dir: Path) -> None:
        """Replace all image markdown with embedded GTK widgets."""
        if self.is_updating_images:
            return

        self.is_updating_images = True
        self.buffer.handler_block(self.changed_handler_id)

        try:
            # Remove existing anchor characters using regex rather than a
            # character-by-character Python loop.
            start, end = self.buffer.get_bounds()
            text = self.buffer.get_text(start, end, True)
            for m in reversed(list(_ANCHOR_CHAR_RE.finditer(text))):
                it_start = self.buffer.get_iter_at_offset(m.start())
                it_end = it_start.copy()
                it_end.forward_char()
                self.buffer.delete(it_start, it_end)
            self.image_anchors.clear()

            # Re-read text after clearing anchors.
            start, end = self.buffer.get_bounds()
            text = self.buffer.get_text(start, end, True)
            matches = list(re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text))

            # Iterate in reverse so earlier offsets stay valid.
            for match in reversed(matches):
                url = match.group(2)
                anchor_iter = self.buffer.get_iter_at_offset(match.end())
                anchor = self.buffer.create_child_anchor(anchor_iter)
                self.image_anchors.append(anchor)

                if url.startswith(("http://", "https://")):
                    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                    widget.add_css_class("image-container")
                    img = Gtk.Image.new_from_icon_name("image-loading-symbolic")
                    img.set_pixel_size(64)
                    widget.append(img)
                    lbl = Gtk.Label(label="Loading...")
                    lbl.add_css_class("image-caption")
                    widget.append(lbl)
                    widget.set_size_request(400, -1)
                    Gio.File.new_for_uri(url).load_contents_async(
                        None, self._on_remote_image_loaded, img, lbl
                    )
                else:
                    local_path = Path(url)
                    if not local_path.is_absolute():
                        local_path = (note_dir / url).resolve()
                    if local_path.exists() and local_path.is_file():
                        try:
                            widget = Gtk.Image.new_from_file(str(local_path))
                            widget.set_pixel_size(500)
                            widget.set_margin_top(10)
                            widget.set_margin_bottom(10)
                        except Exception:
                            logger.exception("Failed to load local image: %s", local_path)
                            widget = Gtk.Label(label=f"Error: {url}")
                            widget.add_css_class("image-error")
                    else:
                        widget = Gtk.Label(label=f"Not Found: {url}")
                        widget.add_css_class("image-error")

                self.text_view.add_child_at_anchor(widget, anchor)
        finally:
            self.buffer.handler_unblock(self.changed_handler_id)
            self.is_updating_images = False
