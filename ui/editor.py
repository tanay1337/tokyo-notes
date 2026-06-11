"""Markdown editor component with syntax highlighting and image support."""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import re
import shutil
import threading
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from core.translations import tr
from core.utils import MD_URL_BALANCED
from ui.deadline_picker import DeadlinePicker
from ui.link_picker import LinkPicker
from ui.slash_picker import SlashPicker

logger = logging.getLogger(__name__)

_DIAGRAM_ID_RE = re.compile(r"^[0-9a-f]{12}$")


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
    logger.debug(
        "resolve_image_path: notes_dir=%s image_path=%r -> full=%s relative=%s",
        notes_dir_resolved,
        image_path,
        full_path,
        full_path.is_relative_to(notes_dir_resolved),
    )
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

        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_image_drop)
        self.text_view.add_controller(drop_target)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self.on_key_pressed)
        self.text_view.add_controller(key_ctrl)
        self.controller = key_ctrl

        spell_actions = Gio.SimpleActionGroup()
        replace_action = Gio.SimpleAction.new("replace", GLib.VariantType("s"))
        replace_action.connect("activate", self._on_spell_replace)
        spell_actions.add_action(replace_action)
        add_dict_action = Gio.SimpleAction.new("add-dict", None)
        add_dict_action.connect("activate", self._on_spell_add_dict)
        spell_actions.add_action(add_dict_action)
        ignore_action = Gio.SimpleAction.new("ignore", None)
        ignore_action.connect("activate", self._on_spell_ignore)
        spell_actions.add_action(ignore_action)
        self.insert_action_group("spell", spell_actions)

        self._spell_offset: int = 0
        self._spell_end_offset: int = 0
        self._spell_word: str = ""
        self._last_suggest_word: str = ""
        self._last_suggestions: list[str] = []
        self.highlighter: Any = None
        self._spell_updating: bool = False
        self._menu_update_pending: int = 0
        self._suggest_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        self.buffer: Gtk.TextBuffer = self.text_view.get_buffer()
        self.buffer.connect("insert-text", self.on_insert_text)
        self.changed_handler_id = self.buffer.connect("changed", on_text_changed)
        self.cursor_handler_id = self.buffer.connect(
            "notify::cursor-position", on_cursor_moved
        )
        self.buffer.connect("notify::cursor-position", self._on_spell_cursor_moved)

        scrolled_editor.set_child(self.text_view)

        self.editor_overlay = Gtk.Overlay()
        self.editor_overlay.set_child(scrolled_editor)

        self._lock_overlay = self._build_lock_overlay()
        self.editor_overlay.add_overlay(self._lock_overlay)

        self.append(self.editor_overlay)

        self.status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.status_bar.add_css_class("status-bar")
        self.status_bar.set_visible(False)

        self.stats_label = Gtk.Label(label=tr("Words: 0 | Chars: 0 | Read: 0m"))
        self.stats_label.add_css_class("stats-label")
        self.status_bar.append(self.stats_label)
        self.append(self.status_bar)

        self.image_anchors: list[Gtk.TextChildAnchor] = []
        self.image_widgets: list[Gtk.Widget] = []
        self._image_update_running: bool = False
        self._image_update_pending: bool = False
        self._pending_notes_dir: Path = Path()
        self._notes_dir: Path = Path()
        self._image_pixbuf_cache: dict[str, tuple[float, GdkPixbuf.Pixbuf]] = {}
        self._last_image_text_hash: str = ""
        self._image_update_done_callback: Callable | None = None

        self._picker_open = False

    def _on_spell_cursor_moved(
        self,
        buffer: Gtk.TextBuffer,
        pspec: GLib.ParamSpec | None = None,
    ) -> None:
        """Debounced handler — schedules a deferred spell menu update."""
        if self._menu_update_pending:
            GLib.source_remove(self._menu_update_pending)
        self._menu_update_pending = GLib.timeout_add(
            100, self._update_spell_extra_menu, buffer
        )

    @staticmethod
    def _should_suggest(word: str) -> bool:
        """Return False if *word* is unlikely to be a real-language word."""
        if len(word) > 20:
            return False
        if any(ch.isdigit() for ch in word):
            return False
        return True

    def _update_spell_extra_menu(self, buffer: Gtk.TextBuffer) -> bool:
        """Update the extra context menu with spell suggestions for the cursor word."""
        self._menu_update_pending = 0
        if self._spell_updating:
            return GLib.SOURCE_REMOVE
        self._spell_updating = True
        try:
            has_suggestions = False
            word = ""
            if (
                self.highlighter is not None
                and self.highlighter.spell_check_enabled
                and self.highlighter.spell_checker is not None
            ):
                cursor = buffer.get_iter_at_mark(buffer.get_insert())

                start_it = cursor.copy()
                if not start_it.starts_word():
                    start_it.backward_word_start()
                end_it = cursor.copy()
                if not end_it.ends_word():
                    end_it.forward_word_end()

                if start_it.get_offset() < end_it.get_offset():
                    word = buffer.get_text(start_it, end_it, True).strip()
                    if len(word) > 1:
                        tag_iter = start_it.copy()
                        is_misspelled = False
                        while tag_iter.compare(end_it) < 0:
                            tags = tag_iter.get_tags()
                            if any(
                                t.get_property("name") == "misspelled" for t in tags
                            ):
                                is_misspelled = True
                                break
                            tag_iter.forward_char()

                        if is_misspelled:
                            self._spell_offset = start_it.get_offset()
                            self._spell_end_offset = end_it.get_offset()
                            self._spell_word = word
                            has_suggestions = True

            menu = self.text_view.get_extra_menu()
            if menu is None and has_suggestions:
                menu = Gio.Menu()
                self.text_view.set_extra_menu(menu)

            if menu is not None:
                while menu.get_n_items() > 0:
                    menu.remove(0)

                if has_suggestions:
                    if word == self._last_suggest_word:
                        suggestions = self._last_suggestions
                    elif not self._should_suggest(word):
                        self._last_suggest_word = word
                        self._last_suggestions = []
                        suggestions = []
                    else:
                        self._last_suggest_word = word
                        self._last_suggestions = []
                        suggestions = []
                        self._suggest_executor.submit(self._suggest_worker, word)

                    suggestions_section = Gio.Menu()
                    for sug in suggestions:
                        item = Gio.MenuItem.new(sug, "spell.replace")
                        item.set_attribute_value("target", GLib.Variant("s", sug))
                        suggestions_section.append_item(item)

                    actions_section = Gio.Menu()
                    actions_section.append("Add to Dictionary", "spell.add-dict")
                    actions_section.append("Ignore", "spell.ignore")

                    menu.append_section(None, suggestions_section)
                    menu.append_section(None, actions_section)
        except Exception:
            logger.warning("Spell extra menu update failed", exc_info=True)
        finally:
            self._spell_updating = False
        return GLib.SOURCE_REMOVE

    def _suggest_worker(self, word: str) -> None:
        """Run spell suggest() in a background thread."""
        try:
            sp = self.highlighter.spell_checker
            suggestions = sp.suggest(word) if sp else []
        except Exception:
            suggestions = []
        GLib.idle_add(self._suggest_ready, word, suggestions)

    def _suggest_ready(self, word: str, suggestions: list[str]) -> bool:
        """Callback on main thread when background suggest completes."""
        if word != self._last_suggest_word:
            return GLib.SOURCE_REMOVE
        self._last_suggestions = suggestions
        self._on_spell_cursor_moved(self.buffer, None)
        return GLib.SOURCE_REMOVE

    def invalidate_spell_cache(self) -> None:
        """Clear cached suggestions (call when spell config changes)."""
        self._last_suggest_word = ""
        self._last_suggestions = []

    def _on_spell_replace(
        self, action: Gio.SimpleAction, variant: GLib.Variant | None
    ) -> None:
        if variant is None:
            return
        sug = variant.get_string()
        start = self.buffer.get_iter_at_offset(self._spell_offset)
        end = self.buffer.get_iter_at_offset(self._spell_end_offset)
        self.buffer.begin_user_action()
        self.buffer.delete(start, end)
        self.buffer.insert(self.buffer.get_iter_at_offset(self._spell_offset), sug, -1)
        self.buffer.end_user_action()
        if self.highlighter:
            self.highlighter.highlight()
        self._on_spell_cursor_moved(self.buffer, None)

    def _on_spell_add_dict(
        self, action: Gio.SimpleAction, variant: GLib.Variant | None
    ) -> None:
        if self.highlighter and self.highlighter.spell_checker:
            self.highlighter.spell_checker.add_to_user_dict(self._spell_word)
            self.highlighter.highlight()
        self._on_spell_cursor_moved(self.buffer, None)

    def _on_spell_ignore(
        self, action: Gio.SimpleAction, variant: GLib.Variant | None
    ) -> None:
        if self.highlighter and self.highlighter.spell_checker:
            self.highlighter.spell_checker.ignore_word(self._spell_word)
            self.highlighter.highlight()
        self._on_spell_cursor_moved(self.buffer, None)

    def clear_images(self) -> None:
        """Properly remove all image widgets and clear anchor/widget lists."""
        for widget in self.image_widgets:
            parent = widget.get_parent()
            if parent == self.text_view:
                try:
                    self.text_view.remove(widget)
                except Exception:
                    widget.unparent()
            elif parent:
                widget.unparent()
        self.image_widgets.clear()
        self.image_anchors.clear()

    def close_pickers(self) -> None:
        """Close any open picker popovers."""
        self._picker_open = False
        # We need to find all children that are Popovers and pop them down.
        # In GTK 4, popovers are often not direct children in the same way.
        # But pickers in this app are parented to text_view.
        child = self.text_view.get_first_child()
        while child:
            if isinstance(child, Gtk.Popover):
                child.popdown()
            child = child.get_next_sibling()

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
            label=tr(
                "This note is private. Click the sidebar row"
                " and enter your password to unlock."
            ),
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
        picker = DeadlinePicker(self.on_deadline_selected, has_deadline=False)
        picker.connect("closed", lambda *_: setattr(self, "_picker_open", False))
        self._popup_at_cursor(picker)

    def on_deadline_selected(self, deadline: str | None) -> None:
        if deadline is not None:
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
            if command_label == "Diagram":
                self.text_view._skip_focus_restore = True
                if callable(getattr(self, "_on_diagram_slash", None)):
                    GLib.idle_add(self._on_diagram_slash)
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

    # Drag-and-drop image support

    def _on_image_drop(
        self, drop_target: Gtk.DropTarget, value: Gdk.FileList, x: float, y: float
    ) -> bool:
        """Handle dropped image files from the file manager."""
        if not self._notes_dir or not self._notes_dir.exists():
            return False

        uris: list[str] = [f.get_uri() for f in value.get_files()]
        return self._do_drop_uris(uris)

    def _do_drop_uris(self, uris: list[str]) -> bool:
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
        images_dir = self._notes_dir / ".images"
        images_dir.mkdir(exist_ok=True)

        inserted = False
        for uri in uris:
            raw = uri.removeprefix("file://")
            filepath = Path(raw)
            if filepath.suffix.lower() not in image_extensions:
                continue
            try:
                filename = f"dropped_{uuid.uuid4()}{filepath.suffix}"
                dest = images_dir / filename
                shutil.copy2(str(filepath), str(dest))
                insert_pos = self.buffer.get_iter_at_mark(self.buffer.get_insert())
                self.buffer.insert(
                    insert_pos, f"\n![Dropped Image](.images/{filename})\n"
                )
                inserted = True
            except Exception as exc:
                logger.warning("Failed to drop image %s: %s", uri, exc)
        return inserted

    # Diagram embed

    def _build_diagram_embed(self, diagram_id: str) -> Gtk.Widget | None:
        """Build a clickable preview widget for a diagram reference."""
        dm = getattr(self, "_diagram_manager", None)
        pixbuf: GdkPixbuf.Pixbuf | None = None
        if dm is not None:
            diagram = dm.load(diagram_id)
            if diagram is not None:
                from ui.diagram_view import render_diagram_preview

                ctx = self.text_view.get_style_context()
                ok_bg, bg = ctx.lookup_color("editor_bg")
                if not ok_bg:
                    ok_bg, bg = ctx.lookup_color("theme_base_color")
                ok_fg, fg = ctx.lookup_color("theme_fg_color")

                editor_width = self.text_view.get_allocated_width()
                max_w = 400
                max_h = 300
                if editor_width > 0:
                    max_w = max(400, min(int(editor_width * 0.47), 700))
                    max_h = max(300, int(max_w * 0.6))

                pixbuf = render_diagram_preview(
                    diagram,
                    max_width=max_w,
                    max_height=max_h,
                    bg_color=bg if ok_bg else None,
                    text_color=fg if ok_fg else None,
                )

        if pixbuf is not None:
            img = Gtk.Picture.new_for_pixbuf(pixbuf)
            img.set_halign(Gtk.Align.START)
            img.set_size_request(pixbuf.get_width(), pixbuf.get_height())
            img.set_cursor(Gdk.Cursor.new_from_name("pointer"))
            gesture = Gtk.GestureClick.new()
            gesture.connect("pressed", lambda *_: self._open_diagram(diagram_id))
            img.add_controller(gesture)
            img.set_tooltip_text(tr("Click to edit diagram"))
            return img

        # Fallback: placeholder label
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("diagram-embed")
        box.set_halign(Gtk.Align.START)
        label = Gtk.Label(label=tr("Diagram: {id}").format(id=diagram_id))
        label.set_hexpand(True)
        box.append(label)
        open_btn = Gtk.Button(label=tr("Open"))
        open_btn.add_css_class("pill")
        open_btn.add_css_class("suggested-action")
        open_btn.connect("clicked", lambda *_: self._open_diagram(diagram_id))
        box.append(open_btn)
        return box

    def _open_diagram(self, diagram_id: str) -> None:
        callback = getattr(self, "_on_open_diagram", None)
        if callable(callback):
            callback(diagram_id)

    # Image rendering

    def _load_image_pixbuf(
        self, full_path: Path, max_w: int = 800, max_h: int = 800
    ) -> GdkPixbuf.Pixbuf | None:
        """Load a pixbuf from *full_path*, using the in-memory cache."""
        try:
            mtime = full_path.stat().st_mtime
        except OSError:
            logger.warning("IMAGE: stat failed for %s", full_path)
            return None
        key = f"{full_path.resolve()}:{max_w}x{max_h}"
        cached = self._image_pixbuf_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(full_path), max_w, max_h, True
            )
            self._image_pixbuf_cache[key] = (mtime, pixbuf)
            return pixbuf
        except Exception as exc:
            logger.warning("Failed to load image %s: %s", full_path, exc)
            self._image_pixbuf_cache.pop(key, None)
            return None

    def _pixbuf_from_broken(self) -> GdkPixbuf.Pixbuf | None:
        broken_path = (
            Path(__file__).parent.parent / "assets" / "editor" / "broken-image.svg"
        )
        if not broken_path.exists():
            return None
        key = str(broken_path.resolve())
        cached = self._image_pixbuf_cache.get(key)
        if cached is not None:
            return cached[1]
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(broken_path))
            self._image_pixbuf_cache[key] = (0.0, pixbuf)
            return pixbuf
        except Exception as exc:
            logger.warning("Failed to load broken-image.svg: %s", exc)
            return None

    # --- Remote URL image helpers ---

    @staticmethod
    def _guess_image_ext(url: str) -> str:
        path = urllib.parse.urlparse(url).path
        ext = os.path.splitext(path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"):
            return ext
        return ".png"

    def _remote_image_cache_path(self, url: str, notes_dir: Path) -> Path:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        ext = self._guess_image_ext(url)
        return notes_dir / ".images" / f"remote_{url_hash}{ext}"

    def _download_remote_image(self, url: str, cache_path: Path) -> None:
        def _download() -> None:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "TokyoNotes/1.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
                GLib.idle_add(self._on_remote_image_downloaded)
            except Exception:
                logger.exception("Failed to download remote image: %s", url)

        thread = threading.Thread(target=_download, daemon=True)
        thread.start()

    def _on_remote_image_downloaded(self) -> bool:
        self._image_update_pending = True
        self._finish_image_update()
        return False

    def update_images(
        self, notes_dir: Path, done_callback: Callable | None = None
    ) -> None:
        """Scan buffer for markdown image syntax and render inline widgets."""
        self._notes_dir = notes_dir
        self._pending_notes_dir = notes_dir
        self._image_update_done_callback = done_callback
        if self._image_update_running:
            self._image_update_pending = True
            return
        self._image_update_running = True
        self._image_update_pending = False

        editor_width = self.text_view.get_allocated_width()
        if editor_width > 0:
            max_load = int(editor_width * 0.47)
            max_load = min(max_load, 700)
        else:
            max_load = 500

        try:
            image_re = re.compile(r"!\[([^\]]*)\]\((" + MD_URL_BALANCED + r")\)")

            self.buffer.handler_block(self.changed_handler_id)
            if hasattr(self, "cursor_handler_id"):
                self.buffer.handler_block(self.cursor_handler_id)
            self.buffer.begin_user_action()
            try:
                for anchor in reversed(self.image_anchors):
                    if anchor.get_deleted():
                        continue
                    result = self.buffer.get_iter_at_child_anchor(anchor)
                    it = result[1] if isinstance(result, tuple) else result
                    if it is None:
                        continue
                    it2 = it.copy()
                    it2.forward_char()  # skip \uFFFC
                    # Also delete the \n we inserted after the anchor
                    if it2.get_char() == "\n":
                        it2.forward_char()
                    self.buffer.delete(it, it2)

                self.clear_images()

                start, end = self.buffer.get_bounds()
                text = self.buffer.get_text(start, end, True)
                matches = list(image_re.finditer(text))

                for match in reversed(matches):
                    img_path = match.group(2)

                    self.buffer.insert(
                        self.buffer.get_iter_at_offset(match.start()), "\n"
                    )
                    anchor = self.buffer.create_child_anchor(
                        self.buffer.get_iter_at_offset(match.start())
                    )
                    self.image_anchors.append(anchor)

                    dm = getattr(self, "_diagram_manager", None)
                    diagram = None
                    if dm is not None and img_path and _DIAGRAM_ID_RE.match(img_path):
                        diagram = dm.load(img_path)
                    if diagram is not None:
                        embed = self._build_diagram_embed(diagram.id)
                        if embed:
                            self.text_view.add_child_at_anchor(embed, anchor)
                            self.image_widgets.append(embed)
                        continue

                    pixbuf = None
                    if img_path.startswith(("http://", "https://")):
                        cache_path = self._remote_image_cache_path(img_path, notes_dir)
                        if cache_path.exists():
                            pixbuf = self._load_image_pixbuf(
                                cache_path, max_load, max_load
                            )
                        else:
                            self._download_remote_image(img_path, cache_path)
                    else:
                        full_path = resolve_image_path(notes_dir, img_path)
                        if full_path is not None and full_path.exists():
                            pixbuf = self._load_image_pixbuf(
                                full_path, max_load, max_load
                            )

                    if pixbuf is None:
                        pixbuf = self._pixbuf_from_broken()

                    if pixbuf is not None:
                        img_widget = Gtk.Picture.new_for_pixbuf(pixbuf)
                        img_widget.set_halign(Gtk.Align.START)
                        img_widget.set_size_request(
                            pixbuf.get_width(), pixbuf.get_height()
                        )
                    else:
                        img_widget = Gtk.Picture()
                        img_widget.set_halign(Gtk.Align.START)

                    self.text_view.add_child_at_anchor(img_widget, anchor)
                    self.image_widgets.append(img_widget)
            finally:
                self.buffer.end_user_action()
                if hasattr(self, "cursor_handler_id"):
                    self.buffer.handler_unblock(self.cursor_handler_id)
                self.buffer.handler_unblock(self.changed_handler_id)

            GLib.idle_add(self._finish_image_update)
        except Exception:
            logger.exception("update_images: unhandled error")
            self._image_update_running = False

    def _finish_image_update(self) -> bool:
        self._image_update_running = False
        cb = self._image_update_done_callback
        self._image_update_done_callback = None
        if cb:
            cb()
        if self._image_update_pending:
            self._image_update_pending = False
            nd = self._pending_notes_dir
            self._pending_notes_dir = Path()
            self.update_images(nd)
        return False
