from __future__ import annotations

import json
import logging
import math
import ssl
import threading
import urllib.parse
import urllib.request
from typing import Any

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from core.translations import tr
from ui.widgets.base import WidgetBase

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=en&format=json"
_FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast?"
    "latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
    "&timezone=auto"
)

_WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

_PATTERNS: dict[str, list[int]] = {
    "sun": [0x24, 0x42, 0x81, 0x18, 0x18, 0x81, 0x42, 0x24],
    "cloud": [0x18, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x3C, 0x00],
    "fog": [0x00, 0x7E, 0x00, 0x00, 0x7E, 0x00, 0x7E, 0x00],
    "rain": [0x18, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x2A, 0x14],
    "snow": [0x18, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x14, 0x24],
    "storm": [0x18, 0x3C, 0x7E, 0xFF, 0xFF, 0x08, 0x18, 0x08],
}

_WMO_TO_PATTERN: list[tuple[int, int, str]] = [
    (0, 0, "sun"),
    (1, 3, "cloud"),
    (45, 48, "fog"),
    (51, 67, "rain"),
    (71, 77, "snow"),
    (80, 82, "rain"),
    (85, 86, "snow"),
    (95, 99, "storm"),
]


def _wmo_to_pattern(code: int) -> str:
    for lo, hi, name in _WMO_TO_PATTERN:
        if lo <= code <= hi:
            return name
    return "cloud"


class WeatherIcon(Gtk.DrawingArea):
    def __init__(self, size: int = 60) -> None:
        super().__init__()
        self._pattern = _PATTERNS["sun"]
        self._accent: Gdk.RGBA | None = None
        self._hot: Gdk.RGBA | None = None
        self._is_hot = False
        self.set_size_request(size, size)
        self.set_draw_func(self._on_draw)

    def set_pattern(self, name: str, hot: bool = False) -> None:
        self._pattern = _PATTERNS.get(name, _PATTERNS["sun"])
        self._is_hot = hot
        self.queue_draw()

    def _resolve_color(self, area: Gtk.DrawingArea) -> Gdk.RGBA:
        if self._is_hot and self._hot:
            return self._hot
        if self._accent:
            return self._accent
        ctx = area.get_style_context()
        ok, c = ctx.lookup_color("accent_color")
        if not ok:
            c = Gdk.RGBA()
            c.parse("#7aa2f7")
        self._accent = c
        hot = Gdk.RGBA()
        hot.parse("#ff9e64")
        self._hot = hot
        return c if not self._is_hot else hot

    def _on_draw(
        self, area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int
    ) -> None:
        color = self._resolve_color(area)
        n = min(width, height)
        if n < 40:
            cell = n / 8.0
            dot_r = cell * 0.38
        else:
            cell = n / 10.0
            dot_r = cell * 0.32
        ox = (width - 8 * cell) / 2.0
        oy = (height - 8 * cell) / 2.0

        cr.set_source_rgba(color.red, color.green, color.blue, 0.85)
        for row in range(8):
            for col in range(8):
                if self._pattern[row] >> (7 - col) & 1:
                    cx = ox + col * cell + cell / 2
                    cy = oy + row * cell + cell / 2
                    cr.arc(cx, cy, dot_r, 0, 2 * math.pi)
                    cr.fill()


class WeatherWidget(WidgetBase):
    widget_type = "weather"
    widget_title = "Weather"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._timer_id: int | None = None
        self._fetched = False
        self._build_ui()
        self.connect("map", lambda *a: self._on_map())

    def _build_ui(self) -> None:
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_halign(Gtk.Align.CENTER)
        top.set_margin_top(6)
        top.set_margin_bottom(4)

        self._icon = WeatherIcon(size=28)
        top.append(self._icon)

        self._temp_label = Gtk.Label()
        self._temp_label.set_markup('<span size="22000" weight="bold">--</span>')
        top.append(self._temp_label)

        self._cond_label = Gtk.Label(label="")
        self._cond_label.add_css_class("dim-label")
        top.append(self._cond_label)

        self._content.append(top)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(6)
        sep.set_halign(Gtk.Align.CENTER)
        sep.set_size_request(160, -1)
        self._content.append(sep)

        detail_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        detail_row.set_halign(Gtk.Align.CENTER)
        detail_row.set_margin_bottom(4)

        self._detail_items: list[dict] = []
        keys = ["feels", "humidity", "wind"]
        for i, key in enumerate(keys):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            lbl = Gtk.Label()
            lbl.add_css_class("dim-label")
            val = Gtk.Label()
            box.append(lbl)
            box.append(val)
            detail_row.append(box)
            self._detail_items.append({"label": lbl, "value": val, "key": key})
            if i < len(keys) - 1:
                dot = Gtk.Label(label="·")
                dot.add_css_class("dim-label")
                dot.set_margin_start(5)
                dot.set_margin_end(5)
                detail_row.append(dot)

        self._content.append(detail_row)

    def get_config_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        city_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        city_label = Gtk.Label(label=tr("City:"))
        self._cfg_city_entry = Gtk.Entry()
        self._cfg_city_entry.set_text(self.settings.get("city", "London"))
        self._cfg_city_entry.set_hexpand(True)
        city_row.append(city_label)
        city_row.append(self._cfg_city_entry)
        box.append(city_row)

        return box

    def _on_map(self) -> None:
        if not self._fetched:
            self._fetched = True
            GLib.idle_add(self._fetch_weather)

    def apply_config(self) -> None:
        self.settings["city"] = self._cfg_city_entry.get_text().strip() or "London"
        self._fetch_weather()

    def _fetch_weather(self) -> None:
        city = self.settings.get("city", "London")
        threading.Thread(
            target=self._fetch_weather_worker, args=(city,), daemon=True
        ).start()

    def _fetch_weather_worker(self, city: str) -> None:
        try:
            lat, lon = self._geocode_impl(city)
        except Exception:
            GLib.idle_add(lambda: self._cond_label.set_text(tr("City not found")))
            return
        try:
            url = _FORECAST_URL.format(lat=lat, lon=lon)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(url, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            GLib.idle_add(lambda: self._apply_weather(data))
        except Exception as e:
            logger.warning("Weather fetch failed: %s", e)
            GLib.idle_add(
                lambda: self._cond_label.set_text(tr("Failed to load weather"))
            )

    def _geocode_impl(self, city: str) -> tuple[float, float]:
        url = _GEOCODING_URL.format(city=urllib.parse.quote(city))
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(url, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        results = data.get("results", [])
        if not results:
            raise ValueError("City not found")
        return results[0]["latitude"], results[0]["longitude"]

    def _apply_weather(self, data: dict[str, Any]) -> None:
        current = data.get("current", {})
        temp = current.get("temperature_2m")
        feels_like = current.get("apparent_temperature")
        humidity = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        weather_code = current.get("weather_code", 0)

        condition = _WMO_CODES.get(weather_code, "Unknown")
        pattern = _wmo_to_pattern(weather_code)
        is_hot = temp is not None and temp >= 35

        temp_str = f"{temp:.0f}°C" if temp is not None else "—"
        feels_str = f"{feels_like:.0f}°C" if feels_like is not None else "—"
        humidity_str = f"{humidity}%" if humidity is not None else "—"
        wind_str = f"{wind} km/h" if wind is not None else "—"

        self._icon.set_pattern(pattern, hot=is_hot)
        self._temp_label.set_markup(
            f'<span size="30000" weight="bold">{temp_str}</span>'
        )
        self._cond_label.set_text(condition)

        labels = {
            "feels": (tr("Feels"), feels_str),
            "humidity": (tr("Humidity"), humidity_str),
            "wind": (tr("Wind"), wind_str),
        }
        for item in self._detail_items:
            label, value = labels.get(item["key"], ("", ""))
            item["label"].set_text(label)
            item["value"].set_text(value)

    def update_periodic(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._fetch_weather()
        interval = max(self.settings.get("interval_min", 30), 5) * 60 * 1000
        self._timer_id = GLib.timeout_add(interval, self._fetch_weather)
