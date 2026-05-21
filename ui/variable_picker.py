"""Variable picker popover widget for template variables."""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

_VARIABLES = [
    ("{{today}}", "Today's date", "2026-05-21"),
    ("{{now}}", "Date and time", "2026-05-21 14:30"),
    ("{{time}}", "Current time", "14:30"),
    ("{{weekday}}", "Day of week", "Wednesday"),
]


class VariablePicker(Gtk.Popover):
    """Searchable list of template variables for insertion."""

    def __init__(
        self,
        on_selected: Callable[[str], None],
        text_view: "Gtk.Widget | None" = None,
    ) -> None:
        super().__init__()
        self.add_css_class("variable-picker")
        self.on_selected = on_selected
        self._text_view = text_view

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(300, 240)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search variables…")
        self.search_entry.connect("search-changed", self.on_search_changed)
        box.append(self.search_entry)

        self.list_box = Gtk.ListBox()
        self.list_box.connect("row-activated", self.on_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_box)
        scrolled.set_vexpand(True)
        box.append(scrolled)

        self.set_child(box)
        self.set_autohide(True)
        self._populate(_VARIABLES)

        self.connect("map", lambda _: GLib.idle_add(self.search_entry.grab_focus))
        self.connect("closed", self._on_closed)

    def _on_closed(self, popover: "VariablePicker") -> None:
        if self._text_view is not None:
            GLib.idle_add(self._text_view.grab_focus)

    def _populate(self, variables: list[tuple[str, str, str]]) -> None:
        while (child := self.list_box.get_first_child()):
            self.list_box.remove(child)
        for var, desc, example in variables:
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            name_label = Gtk.Label(label=var, xalign=0)
            name_label.add_css_class("variable-name")
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            row_box.append(name_label)

            desc_label = Gtk.Label(label=f"{desc} — e.g. {example}", xalign=0)
            desc_label.add_css_class("variable-desc")
            desc_label.set_ellipsize(Pango.EllipsizeMode.END)
            row_box.append(desc_label)

            row.set_child(row_box)
            row.variable_text = var
            self.list_box.append(row)

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().lower()
        filtered = [
            v for v in _VARIABLES
            if text in v[0].lower() or text in v[1].lower()
        ]
        self._populate(filtered)

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row:
            self.on_selected(row.variable_text)
            self.popdown()
