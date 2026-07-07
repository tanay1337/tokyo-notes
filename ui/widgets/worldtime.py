from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from core.translations import tr
from ui.widgets.base import WidgetBase

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

logger = logging.getLogger(__name__)

_DEFAULT_ZONES = ["Europe/London", "America/New_York", ""]
_UPDATE_MS = 60_000


class WorldTimeWidget(WidgetBase):
    widget_type = "worldtime"
    widget_title = "World Time"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._timer_id: int | None = None
        self._fetched = False
        self._build_ui()
        self.connect("map", lambda *a: self._on_map())

    def _build_ui(self) -> None:
        self._content.set_spacing(0)
        self._tz_widgets: list[dict[str, Any]] = []
        self._seps: list[Gtk.Separator] = []

        for i in range(3):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.set_halign(Gtk.Align.CENTER)
            box.set_margin_top(6)
            box.set_margin_bottom(4)

            city = Gtk.Label()
            city.add_css_class("dim-label")
            box.append(city)

            time_lbl = Gtk.Label()
            time_lbl.set_markup('<span size="30000" weight="bold">--</span>')
            box.append(time_lbl)

            date_lbl = Gtk.Label()
            date_lbl.add_css_class("dim-label")
            box.append(date_lbl)

            self._content.append(box)
            self._tz_widgets.append(
                {"city": city, "time": time_lbl, "date": date_lbl, "box": box}
            )

            if i < 2:
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.set_halign(Gtk.Align.CENTER)
                sep.set_size_request(160, -1)
                self._content.append(sep)
                self._seps.append(sep)

    def get_config_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        zones = self.settings.get("timezones", _DEFAULT_ZONES)
        self._cfg_entries: list[Gtk.Entry] = []
        for i in range(3):
            entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=tr("Zone {n}:").format(n=i + 1))
            entry = Gtk.Entry()
            entry.set_placeholder_text(_DEFAULT_ZONES[i] or "Asia/Tokyo")
            entry.set_text(zones[i] if i < len(zones) else "")
            entry.set_hexpand(True)
            entry_row.append(label)
            entry_row.append(entry)
            box.append(entry_row)
            self._cfg_entries.append(entry)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(sep)

        self._cfg_24h = Gtk.CheckButton(label=tr("Use 24-hour format"))
        self._cfg_24h.set_active(self.settings.get("use_24h", False))
        box.append(self._cfg_24h)

        return box

    def apply_config(self) -> None:
        zones = [e.get_text().strip() for e in self._cfg_entries]
        self.settings["timezones"] = zones
        self.settings["use_24h"] = self._cfg_24h.get_active()
        self._update_times()
        self._start_timer()

    def _on_map(self) -> None:
        if not self._fetched:
            self._fetched = True
            self._update_times()
            self._start_timer()

    def _start_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add(_UPDATE_MS, self._update_times)

    def _update_times(self) -> bool:
        if ZoneInfo is None:
            return False
        use_24h = self.settings.get("use_24h", False)
        zones = self.settings.get("timezones", _DEFAULT_ZONES)

        visibilities: list[bool] = []
        for i in range(3):
            zone_str = zones[i] if i < len(zones) else ""
            widgets = self._tz_widgets[i]
            visible = bool(zone_str)
            visibilities.append(visible)

            if not zone_str:
                widgets["box"].set_visible(False)
                continue
            try:
                tz = ZoneInfo(zone_str)
                now = datetime.now(tz)
            except Exception:
                city_name = zone_str.rsplit("/", 1)[-1].replace("_", " ")
                widgets["city"].set_text(city_name)
                widgets["time"].set_markup('<span size="30000" weight="bold">--</span>')
                widgets["date"].set_text("")
                widgets["box"].set_visible(True)
                continue

            city_name = zone_str.rsplit("/", 1)[-1].replace("_", " ")
            widgets["city"].set_text(city_name)

            if use_24h:
                time_str = now.strftime("%H:%M")
            else:
                time_str = now.strftime("%I:%M %p").lstrip("0")
            widgets["time"].set_markup(
                f'<span size="30000" weight="bold">{time_str}</span>'
            )

            widgets["date"].set_text(now.strftime("%A, %-d %b %Y"))
            widgets["box"].set_visible(True)

        for i in range(2):
            self._seps[i].set_visible(visibilities[i] and visibilities[i + 1])

        return True

    def update_periodic(self) -> None:
        self._update_times()
        self._start_timer()
