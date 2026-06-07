"""Link picker popover widget."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from core.translations import tr
from ui.base_picker import SearchablePicker


class LinkPicker(SearchablePicker):
    """Searchable list of notes for inserting wiki-style links."""

    def __init__(
        self,
        notes: list[str],
        on_selected: Callable[[str], None],
        text_view: Gtk.Widget | None = None,
    ) -> None:
        super().__init__(
            items=notes,
            on_selected=on_selected,
            text_view=text_view,
            placeholder=tr("Search notes"),
            width=320,
            height=320,
        )

    def _make_row(self, note: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        label = Gtk.Label(label=note, xalign=0)
        label.add_css_class("sidebar-label")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        row.set_child(label)
        row.note_name = note
        return row

    def _item_text(self, item: str) -> str:
        return item

    def _row_value(self, row: Gtk.ListBoxRow) -> str:
        return row.note_name
