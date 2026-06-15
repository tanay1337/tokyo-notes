"""In-editor find and replace bar for Gtk.TextView."""

from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT_MS = 150


class FindBar(Gtk.Box):
    """Find and replace bar that works with a Gtk.TextBuffer."""

    def __init__(
        self, buffer: Gtk.TextBuffer, on_close: Callable[[], None] | None = None
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._buffer = buffer
        self._on_close = on_close
        self._visible = False

        self._find_results: list[tuple[int, int]] = []
        self._current_index: int = -1
        self._case_sensitive: bool = False
        self._replace_visible: bool = False
        self._search_timer: int = 0

        self._match_tag = Gtk.TextTag.new("find-match")
        self._match_tag.set_property("background", "#FFF3A8")
        self._current_tag = Gtk.TextTag.new("find-current-match")
        self._current_tag.set_property("background", "#FFB74D")
        tag_table = self._buffer.get_tag_table()
        tag_table.add(self._match_tag)
        tag_table.add(self._current_tag)

        self._buffer.connect("changed", self._on_buffer_changed)

        self._build_ui()
        self.set_visible(False)

    def _build_ui(self) -> None:
        self.add_css_class("find-bar-container")

        self._find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._find_row.add_css_class("find-bar")

        self._find_entry = Gtk.SearchEntry()
        self._find_entry.set_placeholder_text("Find...")
        self._find_entry.set_hexpand(True)
        self._find_entry.add_css_class("find-entry")
        self._find_entry.connect("search-changed", self._on_search_changed)
        self._find_entry.connect("activate", self._on_find_activated)
        self._find_entry.connect("next-match", self._on_next_match_signal)
        self._find_entry.connect("previous-match", self._on_previous_match_signal)
        self._find_entry.connect("stop-search", self._on_stop_search)
        self._find_row.append(self._find_entry)

        self._match_label = Gtk.Label(label="")
        self._match_label.add_css_class("match-count")
        self._find_row.append(self._match_label)

        self._prev_btn = Gtk.Button(label="\u25b2")
        self._prev_btn.add_css_class("find-bar-btn")
        self._prev_btn.set_tooltip_text("Previous match")
        self._prev_btn.connect("clicked", lambda _: self._navigate(-1))
        self._find_row.append(self._prev_btn)

        self._next_btn = Gtk.Button(label="\u25bc")
        self._next_btn.add_css_class("find-bar-btn")
        self._next_btn.set_tooltip_text("Next match")
        self._next_btn.connect("clicked", lambda _: self._navigate(1))
        self._find_row.append(self._next_btn)

        self._case_btn = Gtk.ToggleButton(label="Aa")
        self._case_btn.add_css_class("find-bar-btn")
        self._case_btn.set_tooltip_text("Case-sensitive")
        self._case_btn.connect("toggled", self._on_case_toggled)
        self._find_row.append(self._case_btn)

        self._expand_btn = Gtk.ToggleButton(label="+")
        self._expand_btn.add_css_class("find-bar-btn")
        self._expand_btn.set_tooltip_text("Toggle replace")
        self._expand_btn.connect("toggled", self._on_expand_toggled)
        self._find_row.append(self._expand_btn)

        self._close_btn = Gtk.Button(label="\u2715")
        self._close_btn.add_css_class("find-bar-btn")
        self._close_btn.set_tooltip_text("Close")
        self._close_btn.connect("clicked", lambda _: self.close())
        self._find_row.append(self._close_btn)

        self.append(self._find_row)

        self._replace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._replace_row.add_css_class("replace-bar")
        self._replace_row.set_visible(False)

        self._replace_entry = Gtk.SearchEntry()
        self._replace_entry.set_placeholder_text("Replace...")
        self._replace_entry.set_hexpand(True)
        self._replace_entry.add_css_class("replace-entry")
        self._replace_entry.connect("activate", lambda _: self.replace_current())
        self._replace_row.append(self._replace_entry)

        self._replace_btn = Gtk.Button(label="Replace")
        self._replace_btn.add_css_class("find-bar-btn")
        self._replace_btn.connect("clicked", lambda _: self.replace_current())
        self._replace_row.append(self._replace_btn)

        self._replace_all_btn = Gtk.Button(label="Replace All")
        self._replace_all_btn.add_css_class("find-bar-btn")
        self._replace_all_btn.connect("clicked", lambda _: self.replace_all())
        self._replace_row.append(self._replace_all_btn)

        self.append(self._replace_row)

        esc_ctrl = Gtk.EventControllerKey.new()
        esc_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(esc_ctrl)

    def _on_key_pressed(
        self,
        ctrl: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval == Gdk.KEY_F3:
            if state & Gdk.ModifierType.SHIFT_MASK:
                self._navigate(-1)
            else:
                self._navigate(1)
            return True
        return False

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        if self._search_timer:
            GLib.source_remove(self._search_timer)
        self._clear_highlights()
        self._search_timer = GLib.timeout_add(_SEARCH_TIMEOUT_MS, self._do_search)

    def _do_search(self, re_select: bool = True) -> bool:
        self._search_timer = 0
        term = self._find_entry.get_text()
        if not term:
            self._clear_highlights()
            self._find_results = []
            self._current_index = -1
            self._update_label()
            return GLib.SOURCE_REMOVE
        self._find_results = self._find_all(term)
        self._apply_highlights()
        if self._find_results:
            if re_select:
                self._current_index = 0
                self._select_current()
            else:
                n = len(self._find_results) - 1
                self._current_index = min(self._current_index, n)
        else:
            self._current_index = -1
        self._update_label()
        return GLib.SOURCE_REMOVE

    def _on_find_activated(self, entry: Gtk.SearchEntry) -> None:
        self._navigate(1)

    def _on_next_match_signal(self, entry: Gtk.SearchEntry) -> None:
        self._navigate(1)

    def _on_previous_match_signal(self, entry: Gtk.SearchEntry) -> None:
        self._navigate(-1)

    def _on_stop_search(self, entry: Gtk.SearchEntry) -> None:
        self.close()

    def _on_case_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._case_sensitive = btn.get_active()
        self._do_search()

    def _on_expand_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._replace_visible = btn.get_active()
        self._replace_row.set_visible(self._replace_visible)
        btn.set_label("\u2212" if btn.get_active() else "+")

    def _on_buffer_changed(self, buffer: Gtk.TextBuffer) -> None:
        if self._visible:
            if self._search_timer:
                GLib.source_remove(self._search_timer)
                self._search_timer = GLib.timeout_add(
                    _SEARCH_TIMEOUT_MS, lambda: self._do_search(re_select=False)
                )

    def _find_all(self, term: str) -> list[tuple[int, int]]:
        flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
        if self._case_sensitive:
            flags = Gtk.TextSearchFlags(0)
        results: list[tuple[int, int]] = []
        search_start = self._buffer.get_start_iter()
        while True:
            result = search_start.forward_search(term, flags, None)
            if result is None:
                break
            match_start, match_end = result
            results.append((match_start.get_offset(), match_end.get_offset()))
            search_start = match_end.copy()
        return results

    def _clear_highlights(self) -> None:
        start, end = self._buffer.get_bounds()
        self._buffer.remove_tag(self._match_tag, start, end)
        self._buffer.remove_tag(self._current_tag, start, end)

    def _apply_highlights(self) -> None:
        start, end = self._buffer.get_bounds()
        self._buffer.remove_tag(self._match_tag, start, end)
        for off_start, off_end in self._find_results:
            tag_start = self._buffer.get_iter_at_offset(off_start)
            tag_end = self._buffer.get_iter_at_offset(off_end)
            self._buffer.apply_tag(self._match_tag, tag_start, tag_end)

    def _select_current(self) -> None:
        start, end = self._buffer.get_bounds()
        self._buffer.remove_tag(self._current_tag, start, end)
        if not self._find_results or self._current_index < 0:
            return
        if self._current_index >= len(self._find_results):
            return
        off_start, off_end = self._find_results[self._current_index]
        sel_start = self._buffer.get_iter_at_offset(off_start)
        sel_end = self._buffer.get_iter_at_offset(off_end)
        self._buffer.apply_tag(self._current_tag, sel_start, sel_end)
        self._buffer.select_range(sel_start, sel_end)
        view = self._get_text_view()
        if view is not None:
            view.scroll_to_iter(sel_start, 0.0, False, 0.0, 0.0)

    def _update_label(self) -> None:
        total = len(self._find_results)
        current = self._current_index + 1 if self._current_index >= 0 else 0
        if total == 0:
            self._match_label.set_text("0/0")
        else:
            self._match_label.set_text(f"{current}/{total}")

    def _navigate(self, direction: int) -> None:
        if not self._find_results:
            return
        total = len(self._find_results)
        self._current_index = (self._current_index + direction) % total
        self._select_current()
        self._update_label()

    def _get_text_view(self) -> Gtk.TextView | None:
        parent = self.get_parent()
        while parent is not None:
            if isinstance(parent, Gtk.TextView):
                return parent
            if hasattr(parent, "text_view"):
                return parent.text_view
            parent = parent.get_parent()
        return None

    def open(self) -> None:
        self._visible = True
        self.set_visible(True)
        self._find_entry.grab_focus()
        selected_text = self._get_selected_text()
        if selected_text:
            self._find_entry.set_text(selected_text)
        self._do_search()

    def open_replace(self) -> None:
        self.open()
        self._expand_btn.set_active(True)

    def close(self) -> None:
        self._visible = False
        self._expand_btn.set_active(False)
        self._replace_row.set_visible(False)
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        self._clear_highlights()
        self._find_results = []
        self._current_index = -1
        self._find_entry.set_text("")
        self._replace_entry.set_text("")
        self._update_label()
        self.set_visible(False)
        if self._on_close:
            self._on_close()

    def is_visible(self) -> bool:
        return self._visible

    def focus_entry(self) -> None:
        self._find_entry.grab_focus()

    def is_replace_visible(self) -> bool:
        return self._replace_visible

    def _get_selected_text(self) -> str:
        if self._buffer.get_has_selection():
            start, end = self._buffer.get_selection_bounds()
            return self._buffer.get_text(start, end, True)
        return ""

    def replace_current(self) -> None:
        replacement = self._replace_entry.get_text()
        if self._current_index < 0 or self._current_index >= len(self._find_results):
            return
        off_start, off_end = self._find_results[self._current_index]
        self._buffer.begin_user_action()
        start = self._buffer.get_iter_at_offset(off_start)
        end = self._buffer.get_iter_at_offset(off_end)
        self._buffer.delete(start, end)
        self._buffer.insert(self._buffer.get_iter_at_offset(off_start), replacement)
        self._buffer.end_user_action()
        delta = len(replacement) - (off_end - off_start)
        self._find_results = [
            (s + delta if s > off_start else s, e + delta if e > off_end else e)
            for s, e in self._find_results
        ]
        self._apply_highlights()
        if self._find_results:
            self._current_index = min(self._current_index, len(self._find_results) - 1)
            self._select_current()
        else:
            self._current_index = -1
        self._update_label()

    def replace_all(self) -> None:
        replacement = self._replace_entry.get_text()
        if not self._find_results:
            return
        self._buffer.begin_user_action()
        offset = 0
        for off_start, off_end in self._find_results:
            adj_start = off_start + offset
            adj_end = off_end + offset
            start = self._buffer.get_iter_at_offset(adj_start)
            end = self._buffer.get_iter_at_offset(adj_end)
            self._buffer.delete(start, end)
            self._buffer.insert(self._buffer.get_iter_at_offset(adj_start), replacement)
            offset += len(replacement) - (off_end - off_start)
        self._buffer.end_user_action()
        self._find_results = []
        self._current_index = -1
        self._clear_highlights()
        self._update_label()
