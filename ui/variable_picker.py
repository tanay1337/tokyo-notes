"""Variable picker popover widget for template variables."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from core.translations import tr
from ui.base_picker import SearchablePicker

_VARIABLES = [
    ("{{today}}", tr("Today's date"), "2026-05-21"),
    ("{{now}}", tr("Date and time"), "2026-05-21 14:30"),
    ("{{time}}", tr("Current time"), "14:30"),
    ("{{weekday}}", tr("Day of week"), "Wednesday"),
]


class VariablePicker(SearchablePicker):
    """Searchable list of template variables for insertion."""

    def __init__(
        self,
        on_selected: Callable[[str], None],
        text_view: Gtk.Widget | None = None,
    ) -> None:
        super().__init__(
            items=_VARIABLES,
            on_selected=on_selected,
            text_view=text_view,
            placeholder=tr("Search variables"),
            width=300,
            height=240,
        )
        self.add_css_class("variable-picker")

    def _make_row(self, var: tuple[str, str, str]) -> Gtk.ListBoxRow:
        name, desc, example = var
        row = Gtk.ListBoxRow()
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        name_label = Gtk.Label(label=name, xalign=0)
        name_label.add_css_class("variable-name")
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        row_box.append(name_label)

        desc_label = Gtk.Label(label=f"{desc} — e.g. {example}", xalign=0)
        desc_label.add_css_class("variable-desc")
        desc_label.set_ellipsize(Pango.EllipsizeMode.END)
        row_box.append(desc_label)

        row.set_child(row_box)
        row.variable_text = name
        return row

    def _item_text(self, item: tuple[str, str, str]) -> str:
        return item[0] + " " + item[1]

    def _row_value(self, row: Gtk.ListBoxRow) -> str:
        return row.variable_text
