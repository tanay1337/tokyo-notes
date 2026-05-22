"""Template picker popover widget."""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from ui.base_picker import SearchablePicker


class TemplatePicker(SearchablePicker):
    """Searchable list of templates for inserting into a note."""

    def __init__(
        self,
        templates: list[dict[str, str]],
        on_selected: Callable[[str], None],
        text_view: "Gtk.Widget | None" = None,
    ) -> None:
        super().__init__(
            items=templates,
            on_selected=on_selected,
            text_view=text_view,
            placeholder="Search templates…",
            width=340,
            height=360,
        )
        self.add_css_class("template-picker")

    def _make_row(self, tmpl: dict[str, str]) -> Gtk.ListBoxRow:
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
        return row

    def _item_text(self, item: dict[str, str]) -> str:
        return item["name"] + " " + item.get("description", "")

    def _row_value(self, row: Gtk.ListBoxRow) -> str:
        return row.template_slug
