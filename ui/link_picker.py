"""Link picker popover widget."""
from __future__ import annotations

from typing import Any, Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

class LinkPicker(Gtk.Popover):
    def __init__(self, notes: list[str], on_selected: Callable[[str], None]) -> None:
        super().__init__()
        self.add_css_class("link-picker")
        self.notes: list[str] = notes
        self.on_selected: Callable[[str], None] = on_selected
        
        box: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(200, 300)
        
        self.search_entry: Gtk.SearchEntry = Gtk.SearchEntry()
        self.search_entry.connect("search-changed", self.on_search_changed)
        box.append(self.search_entry)
        
        self.list_box: Gtk.ListBox = Gtk.ListBox()
        self.list_box.connect("row-activated", self.on_row_activated)
        
        scrolled: Gtk.ScrolledWindow = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_box)
        scrolled.set_vexpand(True)
        box.append(scrolled)
        
        self.set_child(box)
        self.populate_list(notes)

    def populate_list(self, notes: list[str]) -> None:
        """Populates the list box with given notes."""
        while (child := self.list_box.get_first_child()):
            self.list_box.remove(child)
        for note in notes:
            row: Gtk.ListBoxRow = Gtk.ListBoxRow()
            label: Gtk.Label = Gtk.Label(label=note, xalign=0)
            label.add_css_class("sidebar-label")
            row.set_child(label)
            row.note_name = note
            self.list_box.append(row)

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Filters the notes list by search query."""
        text = entry.get_text().lower()
        filtered = [n for n in self.notes if text in n.lower()]
        self.populate_list(filtered)

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Handles row activation and triggers selection callback."""
        if row:
            self.on_selected(row.note_name)
            self.popdown()
