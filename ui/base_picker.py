"""Base class for searchable picker popovers."""

from __future__ import annotations

from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from core.utils import clear_listbox


class SearchablePicker(Gtk.Popover):
    """Base class for searchable list popovers.

    Subclasses override:
    - _items: list of items to display
    - _make_row(): build a row widget for an item
    - _item_text(): return searchable text for an item
    - _row_value(): return the value to pass to on_selected
    """

    def __init__(
        self,
        items: list[Any],
        on_selected: Callable[[Any], None],
        text_view: Gtk.Widget | None = None,
        placeholder: str = "Search…",
        width: int = 320,
        height: int = 320,
    ) -> None:
        super().__init__()
        self._items = items
        self.on_selected = on_selected
        self._text_view = text_view

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(width, height)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(placeholder)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("activate", lambda _: self._activate_selected())

        nav = Gtk.EventControllerKey()
        nav.connect("key-pressed", self._on_nav_key)
        self.search_entry.add_controller(nav)
        box.append(self.search_entry)

        self.list_box = Gtk.ListBox()
        self.list_box.connect("row-activated", self.on_row_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_box)
        scrolled.set_vexpand(True)
        box.append(scrolled)

        self.set_child(box)
        self.set_autohide(True)
        self._populate(items)

        self.connect("map", lambda _: GLib.idle_add(self.search_entry.grab_focus))
        self.connect("closed", self._on_closed)

    def _on_closed(self, popover: SearchablePicker) -> None:
        """Return keyboard focus to the editor when the picker is dismissed."""
        if self._text_view is not None:
            skip = getattr(self._text_view, "_skip_focus_restore", False)
            if not skip:
                GLib.idle_add(self._text_view.grab_focus)

    def _populate(self, items: list[Any]) -> None:
        clear_listbox(self.list_box)
        for item in items:
            row = self._make_row(item)
            self.list_box.append(row)

    def _make_row(self, item: Any) -> Gtk.ListBoxRow:
        """Build a single row for *item*. Override in subclasses."""
        row = Gtk.ListBoxRow()
        label = Gtk.Label(label=str(item), xalign=0)
        row.set_child(label)
        return row

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().lower()
        filtered = [
            item for item in self._items if text in self._item_text(item).lower()
        ]
        self._populate(filtered)

    def _item_text(self, item: Any) -> str:
        """Return the searchable text for *item*. Override in subclasses."""
        return str(item)

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row:
            self.on_selected(self._row_value(row))
            self.popdown()

    def _on_nav_key(self, controller, keyval, keycode, state) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._select_relative(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._select_relative(-1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._activate_selected()
            return True
        if keyval == Gdk.KEY_Escape:
            self.popdown()
            return True
        return False

    def _select_relative(self, direction: int) -> None:
        selected = self.list_box.get_selected_row()
        if selected is not None:
            idx = selected.get_index() + direction
        else:
            idx = 0 if direction > 0 else self.list_box.get_rows_count() - 1
        row = self.list_box.get_row_at_index(idx)
        if row is not None:
            self.list_box.select_row(row)

    def _activate_selected(self) -> None:
        row = self.list_box.get_selected_row()
        if row is not None:
            self.on_row_activated(self.list_box, row)
            return
        first = self.list_box.get_row_at_index(0)
        if first is not None:
            self.on_row_activated(self.list_box, first)

    def _row_value(self, row: Gtk.ListBoxRow) -> Any:
        """Return the value to pass to on_selected for *row*. Override in subclasses."""
        return row
