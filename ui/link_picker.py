"""Link picker popover widget."""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango


class LinkPicker(Gtk.Popover):
    """Searchable list of notes for inserting wiki-style links."""

    def __init__(
        self,
        notes: list[str],
        on_selected: Callable[[str], None],
        text_view: "Gtk.Widget | None" = None,
    ) -> None:
        super().__init__()
        self.add_css_class("link-picker")
        self.notes = notes
        self.on_selected = on_selected
        self._text_view = text_view

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        # Wider than before so long note names are readable.
        box.set_size_request(320, 320)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search notes…")
        self.search_entry.connect("search-changed", self.on_search_changed)
        box.append(self.search_entry)

        self.list_box = Gtk.ListBox()
        self.list_box.connect("row-activated", self.on_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_box)
        scrolled.set_vexpand(True)
        box.append(scrolled)

        self.set_child(box)
        self.set_autohide(True)   # Escape closes the popover natively
        self._populate(notes)

        # Auto-focus the search entry so the user can type immediately.
        self.connect("map", lambda _: GLib.idle_add(self.search_entry.grab_focus))
        # Return focus to the text_view when the picker closes so the
        # keyboard shortcut controller doesn't lose its target.
        self.connect("closed", self._on_closed)

    def _on_closed(self, popover: "LinkPicker") -> None:
        """Return keyboard focus to the editor when the picker is dismissed."""
        if self._text_view is not None:
            GLib.idle_add(self._text_view.grab_focus)

    def _populate(self, notes: list[str]) -> None:
        while (child := self.list_box.get_first_child()):
            self.list_box.remove(child)
        for note in notes:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=note, xalign=0)
            label.add_css_class("sidebar-label")
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.set_child(label)
            row.note_name = note
            self.list_box.append(row)

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().lower()
        self._populate([n for n in self.notes if text in n.lower()])

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row:
            self.on_selected(row.note_name)
            self.popdown()
