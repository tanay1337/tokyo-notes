"""Template picker popover widget."""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango


class TemplatePicker(Gtk.Popover):
    """Searchable list of templates for inserting into a note."""

    def __init__(
        self,
        templates: list[dict[str, str]],
        on_selected: Callable[[str], None],
        text_view: "Gtk.Widget | None" = None,
    ) -> None:
        super().__init__()
        self.add_css_class("template-picker")
        self.templates = templates
        self.on_selected = on_selected
        self._text_view = text_view

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(340, 360)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search templates…")
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
        self._populate(templates)

        self.connect("map", lambda _: GLib.idle_add(self.search_entry.grab_focus))
        self.connect("closed", self._on_closed)

    def _on_closed(self, popover: "TemplatePicker") -> None:
        """Return keyboard focus to the editor when the picker is dismissed."""
        if self._text_view is not None:
            GLib.idle_add(self._text_view.grab_focus)

    def _populate(self, templates: list[dict[str, str]]) -> None:
        while (child := self.list_box.get_first_child()):
            self.list_box.remove(child)
        for tmpl in templates:
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            name_label = Gtk.Label(label=tmpl["name"], xalign=0)
            name_label.add_css_class("sidebar-label")
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            row_box.append(name_label)

            if tmpl.get("description"):
                desc_label = Gtk.Label(label=tmpl["description"], xalign=0)
                desc_label.add_css_class("template-desc")
                desc_label.set_ellipsize(Pango.EllipsizeMode.END)
                row_box.append(desc_label)

            row.set_child(row_box)
            row.template_slug = tmpl["slug"]
            self.list_box.append(row)

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().lower()
        filtered = [
            t for t in self.templates
            if text in t["name"].lower() or text in t.get("description", "").lower()
        ]
        self._populate(filtered)

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row:
            self.on_selected(row.template_slug)
            self.popdown()
