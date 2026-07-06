"""Widget type picker popover — searchable list for adding dashboard widgets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from core.translations import tr
from ui.base_picker import SearchablePicker

if TYPE_CHECKING:
    from ui.widgets.base import WidgetBase


_HELP_WIDGETS: dict[str, str] = {
    "tasks": tr("Your task checklist grouped by deadline with filters and quick-add."),
    "weather": tr("Current weather and forecast from Open-Meteo."),
    "rss": tr("Latest headlines from your favourite RSS/Atom feeds."),
    "api": tr("Display live data from any JSON API."),
    "worldtime": tr("Current time for multiple timezones around the world."),
}


class WidgetPicker(SearchablePicker):
    """Searchable list of available widget types for adding to the dashboard."""

    def __init__(
        self,
        widget_types: dict[str, type[WidgetBase]],
        on_selected: Callable[[str], None],
        text_view: Gtk.Widget | None = None,
    ) -> None:
        items = sorted(
            widget_types.items(),
            key=lambda x: x[1].widget_title,
        )
        super().__init__(
            items=items,
            on_selected=on_selected,
            text_view=text_view,
            placeholder=tr("Search widgets"),
            width=300,
            height=320,
        )

    def _make_row(self, item: tuple[str, type[WidgetBase]]) -> Gtk.ListBoxRow:
        wtype, cls = item
        row = Gtk.ListBoxRow()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        title = Gtk.Label(label=cls.widget_title, xalign=0)
        title.add_css_class("sidebar-label")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(title)

        desc = Gtk.Label(label=_HELP_WIDGETS.get(wtype, ""), xalign=0)
        desc.add_css_class("sidebar-snippet")
        desc.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(desc)

        row.set_child(box)
        row._wtype = wtype
        return row

    def _item_text(self, item: tuple[str, type[WidgetBase]]) -> str:
        return item[1].widget_title

    def _row_value(self, row: Gtk.ListBoxRow) -> str:
        return row._wtype
