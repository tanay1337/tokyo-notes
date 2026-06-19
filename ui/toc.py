"""Table of Contents sidebar widget — theme-aware heading navigator."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from core.translations import tr

_HEADING_RE = re.compile(r"^(#+)\s+(.+)$", re.MULTILINE)

if TYPE_CHECKING:
    from main import TokyoNotes


_HLEVEL_INDENT: dict[int, int] = {1: 0, 2: 12, 3: 24, 4: 36, 5: 48, 6: 60}


class TocHeadingRow(Gtk.ListBoxRow):
    """A single heading entry in the table of contents."""

    def __init__(self, line_num: int, level: int, text: str) -> None:
        super().__init__()
        self.line_num = line_num
        self.level = level

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(_HLEVEL_INDENT.get(level, 0))

        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_tooltip_text(text)
        label.add_css_class(f"toc-h{level}")
        box.append(label)

        self.set_child(box)
        self.add_css_class("toc-heading-row")


class TocSidebar(Gtk.Box):
    """Right-side panel showing a live table of contents for the editor."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("toc-sidebar")

        self._app: TokyoNotes | None = None
        self._headings: list[TocHeadingRow] = []
        self._update_idle_id: int = 0
        self._cursor_idle_id: int = 0
        self._active_row: TocHeadingRow | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        header_label = Gtk.Label(label=tr("Table of Contents"))
        header_label.add_css_class("toc-header")
        header_label.set_halign(Gtk.Align.START)
        header_label.set_margin_start(16)
        header_label.set_margin_top(14)
        header_label.set_margin_bottom(6)
        self.append(header_label)

        self._empty_label = Gtk.Label(label=tr("No headings found"))
        self._empty_label.add_css_class("toc-empty")
        self._empty_label.set_margin_top(24)
        self._empty_label.set_halign(Gtk.Align.CENTER)
        self._empty_label.set_visible(False)
        self.append(self._empty_label)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.connect("row-activated", self._on_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self._list_box)
        self.append(scrolled)

    def set_app(self, app: TokyoNotes) -> None:
        """Store the application reference and connect buffer signals."""
        self._app = app
        buffer = self._app.buffer
        buffer.connect("changed", self._on_buffer_changed)
        buffer.connect("notify::cursor-position", self._on_cursor_moved)
        GLib.idle_add(self._rebuild)

    def _on_buffer_changed(self, _buffer: object) -> None:
        if self._update_idle_id:
            GLib.source_remove(self._update_idle_id)
        self._update_idle_id = GLib.timeout_add(300, self._rebuild)

    def _on_cursor_moved(self, _buffer: object, _pspec: object) -> None:
        if self._cursor_idle_id:
            GLib.source_remove(self._cursor_idle_id)
        self._cursor_idle_id = GLib.timeout_add(50, self._update_active)

    def _rebuild(self) -> bool:
        self._update_idle_id = 0
        if not self._app:
            return False

        content = self._app.buffer.get_text(
            self._app.buffer.get_start_iter(),
            self._app.buffer.get_end_iter(),
            include_hidden_chars=True,
        )

        self._headings.clear()
        self._list_box.remove_all()

        for match in _HEADING_RE.finditer(content):
            line_start = content[: match.start()].count("\n")
            level = len(match.group(1))
            text = match.group(2).strip()
            row = TocHeadingRow(line_start, level, text)
            self._headings.append(row)
            self._list_box.append(row)

        has_headings = len(self._headings) > 0
        self._empty_label.set_visible(not has_headings)
        self._list_box.set_visible(has_headings)

        self._update_active()
        return False

    def _update_active(self) -> bool:
        self._cursor_idle_id = 0
        if not self._app or not self._headings:
            return False

        cursor_iter = self._app.buffer.get_iter_at_mark(self._app.buffer.get_insert())
        cursor_line = cursor_iter.get_line()

        active: TocHeadingRow | None = None
        for row in self._headings:
            if row.line_num <= cursor_line:
                active = row
            else:
                break

        if active is not self._active_row:
            if self._active_row:
                self._active_row.remove_css_class("active")
            if active:
                active.add_css_class("active")
            self._active_row = active

        return False

    def _on_row_activated(self, _list_box: Gtk.ListBox, row: TocHeadingRow) -> None:
        """Scroll the editor to the heading line on click."""
        if not self._app:
            return

        result = self._app.buffer.get_iter_at_line(row.line_num)
        it = result[1] if isinstance(result, tuple) else result
        self._app.buffer.place_cursor(it)
        mark = self._app.buffer.create_mark(None, it, True)
        self._app.text_view.scroll_to_mark(mark, 0.0, True, 0.3, 0.3)
        self._app.text_view.grab_focus()
