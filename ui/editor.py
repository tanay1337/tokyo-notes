"""Markdown editor component with syntax highlighting and image support."""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from pathlib import Path
from time import monotonic
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from core.translations import tr
from core.utils import (
    _FM_EMBED_KEY_RE,
    MD_URL_BALANCED,
    parse_embed_hint,
    resolve_embed_width,
    urlopen_with_fallback,
)
from ui.callout_picker import CalloutPicker
from ui.deadline_picker import DeadlinePicker
from ui.find_bar import FindBar
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
    (re.compile(r"^(\s*(?:[ivxlcdmIVXLCDM]{2,}|[ivxIVX])\.)\s+"), "ordered_roman"),
    (re.compile(r"^(\s*[a-zA-Z]\.)\s+"), "ordered_alpha"),
    (re.compile(r"^(\s*>+)\s*"), "blockquote"),
]

# Ordered list schemes cycled by nesting level (Notion/Google Docs style).
_ORDERED_SCHEMES: list[str] = [
    "ordered",
    "ordered_alpha",
    "ordered_roman",
]

_ORDERED_START_MARKERS: dict[str, str] = {
    "ordered": "1.",
    "ordered_alpha": "a.",
    "ordered_roman": "i.",
}


def _increment_alpha(ch: str) -> str:
    """Increment a single-letter list marker (a→b, ..., y→z, z stays z)."""
    if ch == "z":
        return "z"
    if ch == "Z":
        return "Z"
    return chr(ord(ch) + 1)


def _increment_roman(s: str) -> str | None:
    """Increment a valid roman numeral (i→ii, iii→iv, iv→v, ix→x, etc.).

    Returns None if *s* is not a valid canonical roman numeral.
    """
    n = _roman_to_int(s)
    if n is None:
        return None
    return _int_to_roman(n + 1)


def _roman_to_int(s: str) -> int | None:
    """Convert a lowercase roman numeral to an integer, or None on invalid input."""
    if not s:
        return None
    i = 0
    result = 0
    for value, numeral in _ROMAN_VALUES:
        while i < len(s) and s[i : i + len(numeral)] == numeral:
            result += value
            i += len(numeral)
    if i != len(s):
        return None
    # Validate canonical form via round-trip
    if _int_to_roman(result) != s:
        return None
    return result


def _int_to_roman(n: int) -> str:
    """Convert an integer (1 ≤ n < 4000) to a lowercase roman numeral."""
    result = ""
    for value, numeral in _ROMAN_VALUES:
        while n >= value:
            result += numeral
            n -= value
    return result


_ROMAN_VALUES: list[tuple[int, str]] = [
    (1000, "m"),
    (900, "cm"),
    (500, "d"),
    (400, "cd"),
    (100, "c"),
    (90, "xc"),
    (50, "l"),
    (40, "xl"),
    (10, "x"),
    (9, "ix"),
    (5, "v"),
    (4, "iv"),
    (1, "i"),
]


def resolve_image_path(notes_dir: Path, image_path: str) -> Path | None:
    """Resolve an image/PDF path. Allows absolute and ~/ paths too."""
    if image_path.startswith("~/"):
        full_path = Path(image_path).expanduser().resolve()
    elif image_path.startswith("/"):
        full_path = Path(image_path).resolve()
    else:
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


def _find_pdf_tool(name: str) -> str | None:
    """Find a PDF tool binary, checking PyInstaller bundle and Homebrew paths."""
    path = shutil.which(name)
    if path:
        return path
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for sub in ("", "_internal"):
            candidate = Path(meipass) / sub / name
            if candidate.exists():
                return str(candidate)
    if platform.system() == "Darwin":
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = Path(prefix) / name
            if candidate.exists():
                return str(candidate)
    return None


def _get_list_info(
    line_text: str,
) -> tuple[re.Match, str, str, str, str] | None:
    """Match a list line and return (match, p_type, indent, marker, content)."""
    for pattern, p_type in _CONTINUATION_PATTERNS:
        match = pattern.match(line_text)
        if not match:
            continue
        marker_only = match.group(1)
        indent_text = re.match(r"^(\s*)", marker_only).group(1)
        marker_stripped = marker_only[len(indent_text) :]
        if p_type == "task":
            content = match.group(2).lstrip()
        else:
            content = line_text[len(marker_only) :].lstrip()
        return match, p_type, indent_text, marker_stripped, content
    return None


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

        self.find_bar = FindBar(self.buffer, on_close=self._on_find_bar_closed)
        self.append(self.find_bar)

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
        self._image_pixbuf_cache: OrderedDict[str, tuple[float, GdkPixbuf.Pixbuf]] = (
            OrderedDict()
        )
        self._image_cache_lock = threading.RLock()
        self._image_cache_max_bytes: int = 256 * 1024 * 1024  # 256 MB default budget
        self._image_cache_bytes: int = 0  # running decoded-pixel total
        self._last_image_text_hash: str = ""
        self._image_update_done_callback: Callable | None = None

        self._picker_open = False
        self._on_open_table: Callable[[int], None] | None = None
        self._on_insert_table: Callable[[], None] | None = None
        self._table_double_click_offset: int = -1
        self._setup_table_double_click()

        self._last_list_type: str | None = None
        self._last_list_prefix: str | None = None
        self._last_marker_at_level: dict[int, tuple[int, str, bool]] = {}

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
                    actions_section.append(tr("Add to Dictionary"), "spell.add-dict")
                    actions_section.append(tr("Ignore"), "spell.ignore")

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
        if not editable:
            self.find_bar.close()

    def show_find(self) -> None:
        """Open the find bar."""
        self.find_bar.open()

    def show_replace(self) -> None:
        """Open the find bar with replace row expanded."""
        self.find_bar.open_replace()

    def close_find(self) -> None:
        """Close the find bar."""
        self.find_bar.close()

    def _on_find_bar_closed(self) -> None:
        """Restore focus to the text view when find bar closes."""
        self.text_view.grab_focus()

    def _do_insert_continuation(self, prefix: str) -> bool:
        """Idle callback for inserting a list continuation prefix safely.

        This is a separate method so that the idle source can be traced to a
        GTK widget and does not hold a reference to a potentially
        destroyed buffer.
        """
        self.buffer.insert_at_cursor("\n" + prefix)
        return False

    def _on_tab_key(self, buffer: Gtk.TextBuffer) -> bool:
        """Indent the current list item by one nesting level."""
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        line_start = cursor.copy()
        line_start.set_line_offset(0)
        line_end = cursor.copy()
        line_end.forward_to_line_end()
        line_text = buffer.get_text(line_start, line_end, False)

        info = _get_list_info(line_text)
        if info is None:
            return False

        _match, p_type, indent_text, marker_stripped, content = info
        if p_type == "blockquote":
            return False
        current_level = len(indent_text) // 2
        new_level = current_level + 1
        new_indent = "  " * new_level

        if p_type in ("ordered", "ordered_alpha", "ordered_roman"):
            scheme = _ORDERED_SCHEMES[new_level % len(_ORDERED_SCHEMES)]
            new_marker = _ORDERED_START_MARKERS[scheme]
            self._last_list_type = scheme
        else:
            new_marker = marker_stripped
            self._last_list_type = p_type

        new_prefix = new_indent + new_marker + " "
        new_line = new_prefix + content

        if p_type in ("ordered", "ordered_alpha", "ordered_roman"):
            marker_text = new_marker.rstrip(".")
            if scheme == "ordered":
                value = int(marker_text)
            elif scheme == "ordered_alpha":
                value = ord(marker_text) - ord("a") + 1
            elif scheme == "ordered_roman":
                value = _roman_to_int(marker_text) or 1
            self._last_marker_at_level[new_level] = (value, scheme, False)

        old_line_num = line_start.get_line()
        old_offset = cursor.get_line_offset()
        buffer.begin_user_action()
        buffer.delete(line_start, line_end)

        def _line_iter(buf, ln: int):
            r = buf.get_iter_at_line(ln)
            return r[1] if isinstance(r, tuple) else r

        new_line_start = _line_iter(buffer, old_line_num)
        buffer.insert(new_line_start, new_line)
        new_cursor = _line_iter(buffer, old_line_num)
        new_offset = max(0, old_offset + len(new_indent) - len(indent_text))
        new_cursor.set_line_offset(min(new_offset, len(new_line)))
        buffer.place_cursor(new_cursor)
        buffer.end_user_action()

        self._last_list_prefix = new_prefix
        return True

    def _on_shift_tab_key(self, buffer: Gtk.TextBuffer) -> bool:
        """Outdent the current list item by one nesting level."""
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        line_start = cursor.copy()
        line_start.set_line_offset(0)
        line_end = cursor.copy()
        line_end.forward_to_line_end()
        line_text = buffer.get_text(line_start, line_end, False)

        info = _get_list_info(line_text)
        if info is None:
            return False

        _match, p_type, indent_text, marker_stripped, content = info
        if p_type == "blockquote":
            return False
        current_level = len(indent_text) // 2
        if current_level == 0:
            return False

        new_level = current_level - 1
        new_indent = "  " * new_level

        if p_type in ("ordered", "ordered_alpha", "ordered_roman"):
            stored = self._last_marker_at_level.get(new_level)
            if stored is not None:
                last_value, marker_type, is_upper = stored
                next_value = last_value + 1
                if marker_type == "ordered":
                    new_marker = str(next_value) + "."
                elif marker_type == "ordered_alpha":
                    letter_idx = (next_value - 1) % 26
                    new_marker = chr(ord("a") + letter_idx) + "."
                elif marker_type == "ordered_roman":
                    roman = _int_to_roman(next_value)
                    if is_upper:
                        roman = roman.upper()
                    new_marker = (roman + ".") if roman else "i."
                scheme = marker_type
            else:
                scheme = _ORDERED_SCHEMES[new_level % len(_ORDERED_SCHEMES)]
                new_marker = _ORDERED_START_MARKERS[scheme]
            self._last_list_type = scheme
        else:
            new_marker = marker_stripped
            self._last_list_type = p_type

        new_prefix = new_indent + new_marker + " "
        new_line = new_prefix + content

        if p_type in ("ordered", "ordered_alpha", "ordered_roman"):
            marker_text = new_marker.rstrip(".")
            if scheme == "ordered":
                value = int(marker_text)
            elif scheme == "ordered_alpha":
                value = ord(marker_text) - ord("a") + 1
            elif scheme == "ordered_roman":
                value = _roman_to_int(marker_text.lower()) or 1
            is_upper = marker_text[0].isupper() if marker_text else False
            self._last_marker_at_level[new_level] = (value, scheme, is_upper)

        old_line_num = line_start.get_line()
        old_offset = cursor.get_line_offset()
        buffer.begin_user_action()
        buffer.delete(line_start, line_end)

        def _line_iter(buf, ln: int):
            r = buf.get_iter_at_line(ln)
            return r[1] if isinstance(r, tuple) else r

        new_line_start = _line_iter(buffer, old_line_num)
        buffer.insert(new_line_start, new_line)
        new_cursor = _line_iter(buffer, old_line_num)
        new_offset = max(0, old_offset + len(new_indent) - len(indent_text))
        new_cursor.set_line_offset(min(new_offset, len(new_line)))
        buffer.place_cursor(new_cursor)
        buffer.end_user_action()

        self._last_list_prefix = new_prefix
        return True

    def _indent_selection(self, buffer: Gtk.TextBuffer) -> bool:
        """Add two-space indent to each line in the selection (or current line)."""
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            start_line = start.get_line()
            end_line = end.get_line()
        else:
            cursor_line = buffer.get_iter_at_mark(buffer.get_insert()).get_line()
            start_line = end_line = cursor_line

        buffer.begin_user_action()
        for line_num in range(start_line, end_line + 1):
            line_it = buffer.get_iter_at_line(line_num)
            if isinstance(line_it, tuple):
                line_it = line_it[1]
            buffer.insert(line_it, "  ")
        buffer.end_user_action()
        return True

    def _outdent_selection(self, buffer: Gtk.TextBuffer) -> bool:
        """Remove up to two leading spaces from selected lines (or current line)."""
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            start_line = start.get_line()
            end_line = end.get_line()
        else:
            cursor_line = buffer.get_iter_at_mark(buffer.get_insert()).get_line()
            start_line = end_line = cursor_line
        buffer.begin_user_action()
        for line_num in range(start_line, end_line + 1):
            line_it = buffer.get_iter_at_line(line_num)
            if isinstance(line_it, tuple):
                line_it = line_it[1]
            for _ in range(2):
                after = line_it.copy()
                after.forward_char()
                if buffer.get_text(line_it, after, False) == " ":
                    buffer.delete(line_it, after)
                else:
                    break
        buffer.end_user_action()
        return True

    # Key handling -- list continuation + auto-pair

    def _auto_pair_delimiter(self, buffer: Gtk.TextBuffer, keyval: int) -> bool:
        """Wrap selection in delimiters or auto-close pair on delimiter key."""
        pairs = {
            Gdk.KEY_asterisk: ("*", "*"),
            Gdk.KEY_underscore: ("_", "_"),
            Gdk.KEY_grave: ("`", "`"),
            Gdk.KEY_asciitilde: ("~~", "~~"),
            Gdk.KEY_equal: ("==", "=="),
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

        ctrl = state & Gdk.ModifierType.CONTROL_MASK

        if ctrl and not (state & Gdk.ModifierType.SHIFT_MASK) and keyval == Gdk.KEY_f:
            self.show_find()
            return True
        if ctrl and keyval == Gdk.KEY_h:
            self.show_replace()
            return True

        if self.find_bar.is_visible():
            if keyval == Gdk.KEY_Escape:
                self.close_find()
                return True
            if keyval == Gdk.KEY_F3:
                if state & Gdk.ModifierType.SHIFT_MASK:
                    self.find_bar._navigate(-1)
                else:
                    self.find_bar._navigate(1)
                return True

        buffer = self.text_view.get_buffer()

        # Ctrl+] / Ctrl+[ — indent/outdent selected lines
        # (before auto-pair to avoid bracketleft conflict)
        if ctrl and keyval == Gdk.KEY_bracketright:
            return self._indent_selection(buffer)
        if ctrl and keyval == Gdk.KEY_bracketleft:
            return self._outdent_selection(buffer)

        if keyval in (
            Gdk.KEY_asterisk,
            Gdk.KEY_underscore,
            Gdk.KEY_grave,
            Gdk.KEY_asciitilde,
            Gdk.KEY_equal,
            Gdk.KEY_parenleft,
            Gdk.KEY_bracketleft,
            Gdk.KEY_quotedbl,
        ):
            if self._auto_pair_delimiter(buffer, keyval):
                return True

        # <u> auto-close
        if keyval == Gdk.KEY_u and not ctrl:
            if buffer.get_has_selection():
                start, end = buffer.get_selection_bounds()
                text = buffer.get_text(start, end, True)
                buffer.delete(start, end)
                buffer.insert_at_cursor(f"<u>{text}</u>")
                return True
            cursor = buffer.get_iter_at_mark(buffer.get_insert())
            if cursor.get_offset() > 0:
                prev = cursor.copy()
                prev.backward_char()
                if prev.get_char() == "<":
                    buffer.insert_at_cursor("u></u>")
                    cursor = buffer.get_iter_at_mark(buffer.get_insert())
                    cursor.backward_chars(4)
                    buffer.place_cursor(cursor)
                    return True

        if keyval == Gdk.KEY_Tab:
            return self._on_tab_key(buffer)
        if keyval == Gdk.KEY_ISO_Left_Tab:
            return self._on_shift_tab_key(buffer)

        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False

        cursor_iter = buffer.get_iter_at_mark(buffer.get_insert())

        # Read only the text from the start of the line to the cursor so
        # that patterns match the already-typed prefix, not the rest of the line.
        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_text = buffer.get_text(line_start, cursor_iter, False)

        # If the user edited the line away from what we auto-inserted
        # (e.g. changed "b. " to "i. "), treat this as a fresh list.
        if self._last_list_prefix is not None and not line_text.startswith(
            self._last_list_prefix
        ):
            self._last_list_type = None
            self._last_list_prefix = None

        for pattern, p_type in _CONTINUATION_PATTERNS:
            # If continuing an existing list, skip patterns for a different type
            # so that e.g. a previous alpha list doesn't jump to roman.
            if (
                self._last_list_type is not None
                and p_type != self._last_list_type
                and self._last_list_type in ("ordered_alpha", "ordered_roman")
                and p_type in ("ordered_alpha", "ordered_roman")
            ):
                continue

            match = pattern.match(line_text)
            if not match:
                continue

            # group(1) is always the list marker (possibly with indent).
            marker_only = match.group(1)

            # Empty marker line -> break (root) or outdent (indented).
            # Use the full line text (not just the prefix before cursor)
            # so content after the marker isn't mistaken for emptiness.
            line_end_iter = line_start.copy()
            line_end_iter.forward_to_line_end()
            full_line = buffer.get_text(line_start, line_end_iter, False).strip()
            if full_line == marker_only.strip():
                if p_type == "blockquote":
                    indent_text = re.match(r"^(\s*)", marker_only).group(1)
                    depth = marker_only.strip().count(">")
                    if depth > 1:
                        new_marker = indent_text + ">" * (depth - 1) + " "
                        old_line_num = line_start.get_line()
                        line_end = line_start.copy()
                        line_end.forward_to_line_end()
                        buffer.begin_user_action()
                        buffer.delete(line_start, line_end)

                        def _line_iter(buf, ln: int):
                            r = buf.get_iter_at_line(ln)
                            return r[1] if isinstance(r, tuple) else r

                        new_line_start = _line_iter(buffer, old_line_num)
                        buffer.insert(new_line_start, new_marker)
                        after_insert = _line_iter(buffer, old_line_num)
                        after_insert.forward_to_line_end()
                        buffer.place_cursor(after_insert)
                        buffer.end_user_action()
                        self._last_list_type = p_type
                        self._last_list_prefix = new_marker
                        return True
                    else:
                        line_end = line_start.copy()
                        line_end.forward_to_line_end()
                        buffer.delete(line_start, line_end)
                        self._last_list_type = None
                        self._last_list_prefix = None
                        return False

                indent_text = re.match(r"^(\s*)", marker_only).group(1)
                current_level = len(indent_text) // 2

                if current_level > 0:
                    new_level = current_level - 1
                    new_indent = "  " * new_level

                    if p_type in (
                        "ordered",
                        "ordered_alpha",
                        "ordered_roman",
                    ):
                        stored = self._last_marker_at_level.get(new_level)
                        if stored is not None:
                            last_value, marker_type, is_upper = stored
                            next_value = last_value + 1
                            if marker_type == "ordered":
                                new_marker_stripped = str(next_value) + "."
                            elif marker_type == "ordered_alpha":
                                letter_idx = (next_value - 1) % 26
                                new_marker_stripped = chr(ord("a") + letter_idx) + "."
                            elif marker_type == "ordered_roman":
                                roman = _int_to_roman(next_value)
                                if is_upper:
                                    roman = roman.upper()
                                new_marker_stripped = (roman + ".") if roman else "i."
                            scheme = marker_type
                        else:
                            scheme = _ORDERED_SCHEMES[new_level % len(_ORDERED_SCHEMES)]
                            new_marker_stripped = _ORDERED_START_MARKERS[scheme]
                    else:
                        scheme = p_type
                        new_marker_stripped = marker_only.strip()

                    new_line = new_indent + new_marker_stripped + " "

                    old_line_num = line_start.get_line()
                    line_end = line_start.copy()
                    line_end.forward_to_line_end()
                    buffer.begin_user_action()
                    buffer.delete(line_start, line_end)

                    def _line_iter(buf, ln: int):
                        r = buf.get_iter_at_line(ln)
                        return r[1] if isinstance(r, tuple) else r

                    new_line_start = _line_iter(buffer, old_line_num)
                    buffer.insert(new_line_start, new_line)
                    after_insert = _line_iter(buffer, old_line_num)
                    after_insert.forward_to_line_end()
                    buffer.place_cursor(after_insert)
                    buffer.end_user_action()

                    if p_type in (
                        "ordered",
                        "ordered_alpha",
                        "ordered_roman",
                    ):
                        self._last_list_type = scheme
                    else:
                        self._last_list_type = p_type
                    self._last_list_prefix = new_line

                    if p_type in ("ordered", "ordered_alpha", "ordered_roman"):
                        marker_text = new_marker_stripped.rstrip(".")
                        if scheme == "ordered":
                            value = int(marker_text)
                        elif scheme == "ordered_alpha":
                            value = ord(marker_text) - ord("a") + 1
                        elif scheme == "ordered_roman":
                            value = _roman_to_int(marker_text.lower()) or 1
                        is_upper = marker_text[0].isupper() if marker_text else False
                        self._last_marker_at_level[new_level] = (
                            value,
                            scheme,
                            is_upper,
                        )

                    return True
                else:
                    line_end = line_start.copy()
                    line_end.forward_to_line_end()
                    buffer.delete(line_start, line_end)
                    self._last_list_type = None
                    self._last_list_prefix = None
                    self._last_marker_at_level.clear()
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
            elif p_type == "ordered_alpha":
                indent_text = marker_only[: -len(marker_only.lstrip())]
                letter = marker_only.strip().rstrip(".")
                new_prefix = indent_text + _increment_alpha(letter) + ". "
            elif p_type == "ordered_roman":
                indent_text = marker_only[: -len(marker_only.lstrip())]
                roman = marker_only.strip().rstrip(".").lower()
                next_roman = _increment_roman(roman)
                if next_roman is None:
                    continue  # not a valid roman numeral, try next pattern
                marker_stripped = marker_only.strip()
                if marker_stripped[0].isupper():
                    next_roman = next_roman.upper()
                new_prefix = indent_text + f"{next_roman}. "
            else:
                new_prefix = marker_only.rstrip() + " "

            if p_type in ("ordered", "ordered_alpha", "ordered_roman"):
                indent_text_m = re.match(r"^(\s*)", marker_only).group(1)
                level = len(indent_text_m) // 2
                marker_text = marker_only.strip().rstrip(".")
                if p_type == "ordered":
                    value = int(marker_text)
                elif p_type == "ordered_alpha":
                    value = ord(marker_text) - ord("a") + 1
                elif p_type == "ordered_roman":
                    value = _roman_to_int(marker_text.lower()) or 1
                is_upper = marker_text[0].isupper() if marker_text else False
                self._last_marker_at_level[level] = (value, p_type, is_upper)

            self._last_list_type = p_type
            self._last_list_prefix = new_prefix
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
        elif text == "!" and location.get_offset() >= 1:
            prev_iter = buffer.get_iter_at_offset(location.get_offset() - 1)
            if prev_iter.get_char() == "[":
                line_start = location.copy()
                line_start.set_line_offset(0)
                prefix = buffer.get_text(line_start, location, False)
                if prefix.lstrip().startswith(">"):
                    self._picker_open = True
                    GLib.idle_add(self.show_callout_picker)
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

        def on_selected(command_label: str, insert_text: str, slug: str) -> None:
            self._picker_open = False
            self._remove_last_slash()
            if slug == "deadline":
                self.text_view._skip_focus_restore = True
                self._pending_slash_action = "deadline"
                return
            if slug == "diagram":
                self.text_view._skip_focus_restore = True
                if callable(getattr(self, "_on_diagram_slash", None)):
                    GLib.idle_add(self._on_diagram_slash)
                return
            if slug == "table":
                self.text_view._skip_focus_restore = True
                if callable(self._on_insert_table):
                    self._on_insert_table()
                return
            if slug == "format":
                self.text_view._skip_focus_restore = True
                GLib.idle_add(self._run_cleanup)
                return
            if slug in ("code-block", "flashcard", "divider"):
                self.buffer.insert_at_cursor(insert_text)
                if slug in ("code-block", "flashcard"):
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

    def _run_cleanup(self) -> None:
        from core.md_clean import cleanup_document

        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        cleaned = cleanup_document(text)
        if cleaned != text:
            self.buffer.begin_user_action()
            start, end = self.buffer.get_bounds()
            self.buffer.delete(start, end)
            self.buffer.insert_at_cursor(cleaned)
            self.buffer.end_user_action()
            self._last_image_text_hash = ""
            if self.highlighter:
                self.highlighter.highlight()
        self.text_view._skip_focus_restore = False

    def _on_slash_closed(self, picker: Gtk.Popover) -> None:
        GLib.idle_add(lambda: setattr(self.text_view, "_skip_focus_restore", False))
        if self._pending_slash_action == "deadline":
            self._pending_slash_action = None
            GLib.idle_add(self._trigger_deadline_after_slash)

    def _trigger_deadline_after_slash(self) -> None:
        self.buffer.insert_at_cursor("@")

    def _remove_last_callout_trigger(self) -> None:
        """Delete the [! that triggered the callout picker."""
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if cursor.get_offset() >= 2:
            start = cursor.copy()
            start.backward_chars(2)
            if self.buffer.get_text(start, cursor, False) == "[!":
                self.buffer.delete(start, cursor)

    def show_callout_picker(self) -> None:
        """Show the callout-type picker popover at the cursor."""
        self._picker_open = True

        def on_selected(canon: str, label: str) -> None:
            self._picker_open = False
            self._remove_last_callout_trigger()
            self.buffer.insert_at_cursor(f"[!{canon}] ")

        picker = CalloutPicker(on_selected, self.text_view)
        picker.connect("closed", lambda *_: setattr(self, "_picker_open", False))
        self._popup_at_cursor(picker)

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
                    insert_pos, f"\n![{tr('Dropped Image')}](.images/{filename})\n"
                )
                inserted = True
            except Exception as exc:
                logger.warning("Failed to drop image %s: %s", uri, exc)
        return inserted

    # Diagram embed

    def _build_diagram_embed(
        self, diagram_id: str, embed_width: int
    ) -> Gtk.Widget | None:
        """Build a clickable preview widget for a diagram reference."""
        dm = getattr(self, "_diagram_manager", None)
        pixbuf: GdkPixbuf.Pixbuf | None = None
        max_w = embed_width
        max_h = int(embed_width * 0.6)
        if dm is not None:
            diagram = dm.load(diagram_id)
            if diagram is not None:
                from ui.diagram_view import render_diagram_preview

                ctx = self.text_view.get_style_context()
                ok_bg, bg = ctx.lookup_color("editor_bg")
                if not ok_bg:
                    ok_bg, bg = ctx.lookup_color("theme_base_color")
                ok_fg, fg = ctx.lookup_color("theme_fg_color")

                pixbuf = render_diagram_preview(
                    diagram,
                    max_width=max_w * 2,
                    max_height=max_h * 2,
                    bg_color=bg if ok_bg else None,
                    text_color=fg if ok_fg else None,
                )

        if pixbuf is not None:
            img = Gtk.Picture.new_for_pixbuf(pixbuf)
            img.set_halign(Gtk.Align.START)
            pw = pixbuf.get_width()
            ph = pixbuf.get_height()
            display_w = min(pw, max_w)
            display_h = int(ph * display_w / pw) if pw > 0 else ph
            img.set_size_request(display_w, display_h)
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

    # PDF embed

    @staticmethod
    def _guess_document_ext(url: str) -> str:
        path = urllib.parse.urlparse(url).path
        ext = os.path.splitext(path)[1].lower()
        return ext if ext == ".pdf" else ".pdf"

    def _remote_document_cache_path(self, url: str, notes_dir: Path) -> Path:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return notes_dir / ".documents" / f"remote_{url_hash}.pdf"

    def _download_remote_document(self, url: str, cache_path: Path) -> None:
        def _download() -> None:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "TokyoNotes/1.0"}
                )
                with urlopen_with_fallback(req) as resp:
                    data = resp.read()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
                GLib.idle_add(self._on_remote_image_downloaded)
            except Exception:
                logger.exception("Failed to download remote document: %s", url)

        thread = threading.Thread(target=_download, daemon=True)
        thread.start()

    def _download_remote_image(self, url: str, cache_path: Path) -> None:
        def _download() -> None:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "TokyoNotes/1.0"}
                )
                with urlopen_with_fallback(req) as resp:
                    data = resp.read()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
                GLib.idle_add(self._on_remote_image_downloaded)
            except Exception:
                logger.exception("Failed to download remote image: %s", url)

        thread = threading.Thread(target=_download, daemon=True)
        thread.start()

    def _render_pdf_via_poppler(
        self, pdf_path: Path, max_w: int, max_h: int, page: int = 0
    ) -> GdkPixbuf.Pixbuf | None:
        try:
            gi.require_version("Poppler", "0.18")
            from gi.repository import Poppler
        except (ImportError, ValueError):
            return None
        try:
            document = Poppler.Document.new_from_file(pdf_path.resolve().as_uri(), None)
            p = document.get_page(page)
            if p is None:
                return None

            pw, ph = p.get_size()
            scale = min(max_w / pw, max_h / ph, 2.0)
            iw = int(pw * scale)
            ih = int(ph * scale)

            import cairo

            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, iw, ih)
            cr = cairo.Context(surface)
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
            cr.scale(scale, scale)
            try:
                p.render_for_printing(cr)
            except AttributeError:
                p.render(cr)

            from io import BytesIO

            buf = BytesIO()
            surface.write_to_png(buf)
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(buf.getvalue())
            loader.close()
            return loader.get_pixbuf()
        except Exception as exc:
            logger.debug("Poppler render failed for %s: %s", pdf_path, exc)
            return None

    def _render_pdf_via_pdftoppm(
        self, pdf_path: Path, max_w: int, max_h: int, page: int = 0
    ) -> GdkPixbuf.Pixbuf | None:
        pdftoppm_path = _find_pdf_tool("pdftoppm")
        if pdftoppm_path is None:
            return None

        pgn = page + 1
        tmpdir = Path.home() / ".local" / "share" / "tokyo-notes" / "tmp"

        strategies: list[dict] = [
            # A: file with scaling — omits -scale-to-y so pdftoppm
            # preserves aspect ratio automatically.
            {
                "label": "A:file",
                "extra_args": ["-scale-to-x", str(max_w)],
                "output_fn": lambda workdir: _pick_output(workdir, "page"),
            },
            # B: same but with -singlefile so no page-number suffix.
            {
                "label": "B:single",
                "extra_args": [
                    "-singlefile",
                    "-scale-to-x",
                    str(max_w),
                ],
                "output_fn": lambda workdir: workdir / "page.png",
            },
            # C: PNG to stdout (no scaling).
            {"label": "C:stdout", "extra_args": [], "output_fn": None},
        ]

        def _run(cmd: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(cmd, capture_output=True, timeout=30, check=False)

        def _log_fail(
            label: str, cmd: list[str], cp: subprocess.CompletedProcess
        ) -> None:
            stderr = cp.stderr.decode("utf-8", errors="replace")[:300]
            logger.warning(
                "pdftoppm [%s] exit=%d stdout=%d for %s page %d:\n%s",
                label,
                cp.returncode,
                len(cp.stdout),
                pdf_path,
                page,
                stderr,
            )

        def _pick_output(outdir: Path, stem: str) -> Path | None:
            for f in sorted(outdir.iterdir()):
                if f.name.startswith(stem) and f.suffix == ".png":
                    return f
            return None

        for strat in strategies:
            try:
                if strat["output_fn"] is not None:
                    # Strategy A / B — write to temp dir, then pick up the file.
                    tmpdir.mkdir(parents=True, exist_ok=True)
                    workdir = Path(tempfile.mkdtemp(dir=str(tmpdir)))
                    prefix = workdir / "page"
                    cmd = [
                        pdftoppm_path,
                        "-png",
                        *strat["extra_args"],
                        "-f",
                        str(pgn),
                        "-l",
                        str(pgn),
                        str(pdf_path),
                        str(prefix),
                    ]
                    cp = _run(cmd)
                    if cp.returncode == 0:
                        out = strat["output_fn"](workdir)
                        if out is not None and out.exists():
                            try:
                                return GdkPixbuf.Pixbuf.new_from_file(str(out))
                            except Exception as exc:
                                logger.warning(
                                    "pdftoppm [%s] invalid PNG for %s: %s",
                                    strat["label"],
                                    pdf_path,
                                    exc,
                                )
                    _log_fail(strat["label"], cmd, cp)
                    shutil.rmtree(workdir, ignore_errors=True)
                else:
                    # Strategy C — stdout.
                    cmd = [
                        pdftoppm_path,
                        "-png",
                        "-f",
                        str(pgn),
                        "-l",
                        str(pgn),
                        str(pdf_path),
                        "-",
                    ]
                    cp = _run(cmd)
                    if cp.returncode == 0 and cp.stdout:
                        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                        try:
                            loader.write(cp.stdout)
                            loader.close()
                            return loader.get_pixbuf()
                        except GLib.Error:
                            logger.warning(
                                "pdftoppm [C:stdout] invalid PNG for %s",
                                pdf_path,
                            )
                    else:
                        _log_fail("C:stdout", cmd, cp)
            except Exception as exc:
                logger.debug(
                    "pdftoppm [%s] exception for %s: %s",
                    strat["label"],
                    pdf_path,
                    exc,
                )

        return None

    def _render_pdf_pixbuf(
        self, pdf_path: Path, max_w: int, max_h: int, page: int = 0
    ) -> GdkPixbuf.Pixbuf | None:
        try:
            mtime = pdf_path.stat().st_mtime
        except OSError:
            return None
        key = f"pdf:{pdf_path.resolve()}:{max_w}x{max_h}:p{page}"
        with self._image_cache_lock:
            cached = self._image_pixbuf_cache.get(key)
            if cached is not None and cached[0] == mtime:
                self._image_pixbuf_cache.move_to_end(key)
                return cached[1]

        pixbuf = self._render_pdf_via_poppler(pdf_path, max_w, max_h, page)
        if pixbuf is None:
            pixbuf = self._render_pdf_via_pdftoppm(pdf_path, max_w, max_h, page)
        if pixbuf is not None:
            with self._image_cache_lock:
                self._image_pixbuf_cache[key] = (mtime, pixbuf)
        return pixbuf

    def _get_pdf_page_count(self, pdf_path: Path) -> int:
        try:
            gi.require_version("Poppler", "0.18")
            from gi.repository import Poppler

            doc = Poppler.Document.new_from_file(pdf_path.resolve().as_uri(), None)
            return doc.get_n_pages()
        except (ImportError, ValueError, Exception):
            pass
        pdfinfo_path = _find_pdf_tool("pdfinfo")
        if pdfinfo_path is not None:
            try:
                result = subprocess.run(
                    [pdfinfo_path, str(pdf_path)],
                    capture_output=True,
                    timeout=10,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    logger.warning(
                        "pdfinfo exited %d for %s:\n%s",
                        result.returncode,
                        pdf_path,
                        result.stderr[:500],
                    )
                else:
                    for line in result.stdout.splitlines():
                        if line.startswith("Pages:"):
                            return int(line.split(":")[1].strip())
            except Exception:
                pass
        return 1

    def _set_pdf_page(
        self,
        picture: Gtk.Picture,
        pdf_path: Path,
        load_res: int,
        page: int,
    ) -> None:
        pixbuf = self._render_pdf_pixbuf(pdf_path, load_res, load_res, page)
        if pixbuf is not None:
            picture.set_pixbuf(pixbuf)
            pw = pixbuf.get_width()
            ph = pixbuf.get_height()
            display_w = min(pw, load_res // 2)
            display_h = int(ph * display_w / pw) if pw > 0 else ph
            picture.set_size_request(display_w, display_h)

    def _build_pdf_placeholder(self, path_or_url: str) -> Gtk.Widget:
        if path_or_url.startswith(("http://", "https://")):
            display = os.path.basename(urllib.parse.urlparse(path_or_url).path)
        else:
            display = os.path.basename(path_or_url)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("diagram-embed")
        box.set_halign(Gtk.Align.START)
        label = Gtk.Label(label=tr("PDF: {name}").format(name=display))
        label.set_hexpand(True)
        box.append(label)
        open_btn = Gtk.Button(label=tr("Open"))
        open_btn.add_css_class("pill")
        open_btn.add_css_class("suggested-action")
        open_btn.connect("clicked", lambda *_: self._open_pdf_external(path_or_url))
        box.append(open_btn)
        return box

    def _build_pdf_embed(
        self, img_path: str, notes_dir: Path, embed_width: int
    ) -> Gtk.Widget | None:
        clean = img_path.split("#")[0].split("?")[0]
        pdf_path: Path | None = None

        if img_path.startswith(("http://", "https://")):
            cache_path = self._remote_document_cache_path(img_path, notes_dir)
            if cache_path.exists():
                pdf_path = cache_path
            else:
                self._download_remote_document(img_path, cache_path)
                return self._build_pdf_placeholder(img_path)
        elif clean:
            resolved = resolve_image_path(notes_dir, clean)
            if resolved is not None and resolved.exists():
                pdf_path = resolved

        if pdf_path is None:
            return self._build_pdf_placeholder(img_path)

        load_res = embed_width * 2
        n_pages = self._get_pdf_page_count(pdf_path)
        if n_pages == 0:
            return self._build_pdf_placeholder(str(pdf_path))

        # Try to render first page; fall back to placeholder on failure
        pixbuf = self._render_pdf_pixbuf(pdf_path, load_res, load_res, 0)
        if pixbuf is None:
            return self._build_pdf_placeholder(str(pdf_path))

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.set_halign(Gtk.Align.START)

        picture = Gtk.Picture.new_for_pixbuf(pixbuf)
        picture.set_halign(Gtk.Align.START)
        pw = pixbuf.get_width()
        ph = pixbuf.get_height()
        display_w = min(pw, load_res // 2)
        display_h = int(ph * display_w / pw) if pw > 0 else ph
        picture.set_size_request(display_w, display_h)
        outer.append(picture)

        state = {"page": 0}

        if n_pages > 1:
            bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            bottom.set_halign(Gtk.Align.CENTER)

            page_label = Gtk.Label(
                label=tr("Page {n} / {total}").format(n=1, total=n_pages)
            )
            page_label.add_css_class("dim-label")
            bottom.append(page_label)
            outer.append(bottom)

            accum = [0.0]
            last_turn = [0.0]

            def _on_scroll(
                ctrl: Gtk.EventControllerScroll, _dx: float, dy: float
            ) -> bool:
                accum[0] += dy
                now = monotonic()
                if abs(accum[0]) >= 3.0 and now - last_turn[0] >= 0.25:
                    step = 1 if accum[0] > 0 else -1
                    new_page = max(0, min(n_pages - 1, state["page"] + step))
                    if new_page != state["page"]:
                        state["page"] = new_page
                        self._set_pdf_page(picture, pdf_path, load_res, new_page)
                        page_label.set_text(
                            tr("Page {n} / {total}").format(
                                n=new_page + 1, total=n_pages
                            )
                        )
                        last_turn[0] = now
                    accum[0] = 0.0
                return True

            scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.VERTICAL
            )
            scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            scroll.connect("scroll", _on_scroll)
            outer.add_controller(scroll)

        gesture = Gtk.GestureClick.new()
        gesture.connect(
            "pressed",
            lambda g, np, _x, _y: (
                self._open_pdf_external(str(pdf_path)) if np >= 2 else None
            ),
        )
        outer.add_controller(gesture)
        tooltip = (
            tr("Scroll to change page, double-click to open PDF")
            if n_pages > 1
            else tr("Double-click to open PDF")
        )
        outer.set_tooltip_text(tooltip)

        return outer

    def _open_pdf_external(self, path_or_url: str) -> None:
        try:
            if path_or_url.startswith(("http://", "https://")):
                uri = path_or_url
            else:
                uri = Path(path_or_url).as_uri()
            root = self.text_view.get_root()
            if root:
                Gtk.show_uri(root, uri, Gdk.CURRENT_TIME)
        except Exception as exc:
            logger.warning("Failed to open PDF: %s", exc)

    # Image rendering

    def _evict_image_cache_if_needed(self, new_bytes: int) -> None:
        """Evict the least-recently-used pixbuf entries until there is room
        for ``new_bytes`` of new decoded pixel data.

        The budget is self._image_cache_max_bytes (default 256 MB).  Each
        entry's byte cost is width × height × 4 (RGBA).
        """
        self._image_cache_bytes += new_bytes
        with self._image_cache_lock:
            while (
                self._image_cache_bytes > self._image_cache_max_bytes
                and self._image_pixbuf_cache
            ):
                _key, (_mtime, _pb) = self._image_pixbuf_cache.popitem(last=False)
                evicted = _pb.get_width() * _pb.get_height() * 4
                self._image_cache_bytes = max(0, self._image_cache_bytes - evicted)

    def _warm_image_cache(self, notes_dir: Path) -> None:
        """Pre-decode images referenced in the current buffer in a daemon thread.

        Scans the buffer for ``![...](...)`` references, identifies which paths
        are not yet in the pixbuf cache, and schedules them for decode on the
        main thread. By the time ``update_images()`` fires (2-second debounce),
        the cache is warm and the main-thread render costs only widget
        attachment, not decode.
        """
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        from core.utils import MD_URL_BALANCED

        image_re = re.compile(r"!\[([^\]]*)\]\((" + MD_URL_BALANCED + r")\)")
        paths_to_warm: list[Path] = []
        editor_width = self.text_view.get_allocated_width() or 800
        load_res = min(int(editor_width * 0.47), 700) * 2

        for match in image_re.finditer(text):
            img_path = match.group(2)
            if img_path.startswith(("http://", "https://")):
                continue
            clean = img_path.split("#")[0].split("?")[0]
            if clean.lower().endswith(".pdf"):
                continue
            full_path = resolve_image_path(notes_dir, img_path)
            if full_path is None or not full_path.exists():
                continue
            key = f"{full_path.resolve()}:{load_res}x{load_res}"
            with self._image_cache_lock:
                if key not in self._image_pixbuf_cache:
                    paths_to_warm.append(full_path)

        if not paths_to_warm:
            return

        def _schedule() -> None:
            for path in paths_to_warm:
                GLib.idle_add(self._load_image_pixbuf, path, load_res, load_res)

        threading.Thread(target=_schedule, daemon=True).start()

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
        with self._image_cache_lock:
            cached = self._image_pixbuf_cache.get(key)
            if cached is not None and cached[0] == mtime:
                self._image_pixbuf_cache.move_to_end(key)
                return cached[1]
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(full_path), max_w, max_h, True
            )
            with self._image_cache_lock:
                self._image_pixbuf_cache[key] = (mtime, pixbuf)
                self._image_pixbuf_cache.move_to_end(key)
            decoded_bytes = pixbuf.get_width() * pixbuf.get_height() * 4
            self._evict_image_cache_if_needed(decoded_bytes)
            return pixbuf
        except Exception as exc:
            logger.warning("Failed to load image %s: %s", full_path, exc)
            with self._image_cache_lock:
                self._image_pixbuf_cache.pop(key, None)
            return None

    def _pixbuf_from_broken(self) -> GdkPixbuf.Pixbuf | None:
        broken_path = (
            Path(__file__).parent.parent / "assets" / "editor" / "broken-image.svg"
        )
        if not broken_path.exists():
            return None
        key = str(broken_path.resolve())
        with self._image_cache_lock:
            cached = self._image_pixbuf_cache.get(key)
            if cached is not None:
                return cached[1]
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(broken_path))
            with self._image_cache_lock:
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

    def _on_remote_image_downloaded(self) -> bool:
        self._image_update_pending = True
        self._finish_image_update()
        return False

    def update_images(
        self,
        notes_dir: Path,
        done_callback: Callable | None = None,
        app_width: int = 0,
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

        try:
            image_re = re.compile(r"!\[([^\]]*)\]\((" + MD_URL_BALANCED + r")\)")

            start, end = self.buffer.get_bounds()
            text = self.buffer.get_text(start, end, True)
            matches = list(image_re.finditer(text))
            fmt = _FM_EMBED_KEY_RE.search(text)
            fm_sig = fmt.group(1) if fmt else ""
            signature = f"{app_width}|{fm_sig}|" + "|".join(m.group(0) for m in matches)
            if signature == self._last_image_text_hash:
                self._image_update_running = False
                if done_callback:
                    done_callback()
                return
            self._last_image_text_hash = signature

            doc_hint = fmt.group(1) if fmt else None
            doc_width: int | None = None
            if doc_hint is not None:
                try:
                    doc_width = int(doc_hint)
                except ValueError:
                    SIZE_KEYWORDS = {"small": 300, "medium": 600}
                    doc_width = SIZE_KEYWORDS.get(doc_hint)

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
                    alt_text = match.group(1)
                    img_path = match.group(2)

                    clean_alt, w_hint, h_hint = parse_embed_hint(alt_text)
                    embed_w = resolve_embed_width(
                        w_hint, doc_width, app_width, editor_width
                    )

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
                        embed = self._build_diagram_embed(diagram.id, embed_w)
                        if embed:
                            self.text_view.add_child_at_anchor(embed, anchor)
                            self.image_widgets.append(embed)
                        continue

                    # PDF embed
                    clean_path = img_path.split("#")[0].split("?")[0]
                    if clean_path.lower().endswith(".pdf"):
                        embed = self._build_pdf_embed(img_path, notes_dir, embed_w)
                        if embed:
                            self.text_view.add_child_at_anchor(embed, anchor)
                            self.image_widgets.append(embed)
                        continue

                    pixbuf = None
                    load_res = embed_w * 2
                    if img_path.startswith(("http://", "https://")):
                        cache_path = self._remote_image_cache_path(img_path, notes_dir)
                        if cache_path.exists():
                            pixbuf = self._load_image_pixbuf(
                                cache_path, load_res, load_res
                            )
                        else:
                            self._download_remote_image(img_path, cache_path)
                    else:
                        full_path = resolve_image_path(notes_dir, img_path)
                        if full_path is not None and full_path.exists():
                            pixbuf = self._load_image_pixbuf(
                                full_path, load_res, load_res
                            )

                    if pixbuf is None:
                        pixbuf = self._pixbuf_from_broken()

                    if pixbuf is not None:
                        img_widget = Gtk.Picture.new_for_pixbuf(pixbuf)
                        img_widget.set_halign(Gtk.Align.START)
                        pw = pixbuf.get_width()
                        ph = pixbuf.get_height()
                        display_w = min(pw, embed_w)
                        display_h = int(ph * display_w / pw) if pw > 0 else ph
                        img_widget.set_size_request(display_w, display_h)
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

    # Table double-click

    def _setup_table_double_click(self) -> None:
        gesture = Gtk.GestureClick.new()
        gesture.set_button(0)
        gesture.set_touch_only(False)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", self._on_table_double_click)
        self.text_view.add_controller(gesture)

    def _on_table_double_click(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        if n_press < 2:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        bx, by = self.text_view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, int(x), int(y)
        )
        result = self.text_view.get_iter_at_position(bx, by)
        if not result or not result[0]:
            return
        pos_iter = result[1]
        offset = pos_iter.get_offset()
        if callable(self._on_open_table):
            self._table_double_click_offset = offset
            self._on_open_table(offset)
