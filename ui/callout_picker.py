"""Callout type picker popover — autocomplete for [!TYPE] syntax."""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from core.utils import _CALLOUT_TYPE_META
from ui.base_picker import SearchablePicker

_CALLOUT_COLORS: dict[str, str] = {
    "note": "#7aa2f7",
    "abstract": "#2ac3de",
    "info": "#7aa2f7",
    "todo": "#7aa2f7",
    "tip": "#2ac3de",
    "success": "#9ece6a",
    "question": "#e0af68",
    "warning": "#ff9e64",
    "failure": "#f7768e",
    "danger": "#f7768e",
    "bug": "#f7768e",
    "example": "#bb9af7",
    "quote": "#565f89",
}


def _get_callout_items() -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    for canon, (label, aliases) in _CALLOUT_TYPE_META.items():
        aliases_str = ", ".join(aliases) if aliases else ""
        items.append((canon, label, aliases_str))
    return items


class CalloutPicker(SearchablePicker):
    """Searchable list of callout types — triggered by [! on a blockquote line."""

    def __init__(
        self,
        on_selected: Callable[[str, str], None],
        text_view: Gtk.Widget | None = None,
    ) -> None:
        self._on_selected_raw = on_selected
        self._items_data = _get_callout_items()
        super().__init__(
            items=self._items_data,
            on_selected=lambda row: None,
            text_view=text_view,
            placeholder="Search callout type",
            width=300,
            height=320,
        )
        self.add_css_class("callout-picker")

    def _make_row(self, item: tuple[str, str, str]) -> Gtk.ListBoxRow:
        canon, label, aliases_str = item
        row = Gtk.ListBoxRow()
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_box.set_hexpand(True)

        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        color = _CALLOUT_COLORS.get(canon, "#7aa2f7")

        dot = Gtk.Label()
        dot.set_markup(f'<span foreground="{color}" size="large">●</span>')
        name_box.append(dot)

        name_label = Gtk.Label(label=label, xalign=0)
        name_label.add_css_class("callout-type-name")
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_box.append(name_label)

        syntax_label = Gtk.Label(xalign=0)
        syntax_label.set_markup(
            f'<span foreground="{color}" font_family="monospace">[!{canon}]</span>'
        )
        syntax_label.add_css_class("callout-type-syntax")
        name_box.append(syntax_label)

        text_box.append(name_box)

        if aliases_str:
            alias_label = Gtk.Label(label=aliases_str, xalign=0)
            alias_label.add_css_class("callout-type-aliases")
            alias_label.set_ellipsize(Pango.EllipsizeMode.END)
            text_box.append(alias_label)

        row_box.append(text_box)
        row.set_child(row_box)
        row._callout_data = item
        return row

    def _item_text(self, item: tuple[str, str, str]) -> str:
        canon, label, aliases_str = item
        return f"{label} {canon} {aliases_str}"

    def _row_value(self, row: Gtk.ListBoxRow) -> tuple[str, str, str]:
        return row._callout_data

    def on_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row:
            canon, label, _aliases = row._callout_data
            self._on_selected_raw(canon, label)
            self.popdown()
