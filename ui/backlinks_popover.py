"""Backlinks popover widget — shows notes that link to the current note."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from core.translations import tr


class BacklinksPopover(Gtk.Popover):
    """Popover showing a list of notes that reference the current note."""

    def __init__(
        self,
        backlinks: list[str],
        on_note_clicked: Callable[[str], None],
        text_view: Gtk.Widget | None = None,
    ) -> None:
        super().__init__()
        self.on_note_clicked = on_note_clicked
        self._text_view = text_view

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(280, -1)

        if backlinks:
            header = Gtk.Label(label=tr("Backlinks"))
            header.add_css_class("backlinks-header")
            header.set_xalign(0)
            box.append(header)

            self.list_box = Gtk.ListBox()
            self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            self.list_box.connect("row-activated", self.on_row_activated)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_child(self.list_box)
            scrolled.set_vexpand(True)
            scrolled.set_max_content_height(300)
            scrolled.set_propagate_natural_height(True)
            box.append(scrolled)

            for note in backlinks:
                row = Gtk.ListBoxRow()
                label = Gtk.Label(label=note, xalign=0)
                label.add_css_class("sidebar-label")
                label.set_ellipsize(Pango.EllipsizeMode.END)
                row.set_child(label)
                row.note_name = note
                self.list_box.append(row)
        else:
            label = Gtk.Label(label=tr("No backlinks"))
            label.add_css_class("dim-label")
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            box.append(label)

        self.set_child(box)
        self.set_autohide(True)
        self.connect("closed", self._on_closed)

    def _on_closed(self, popover: BacklinksPopover) -> None:
        if self._text_view is not None:
            self._text_view.grab_focus()
        self.unparent()

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row:
            self.on_note_clicked(row.note_name)
            self.popdown()
