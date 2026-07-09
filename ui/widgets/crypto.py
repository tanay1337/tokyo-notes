"""Crypto price tracker widget powered by the free CoinGecko API."""

from __future__ import annotations

import json
import logging
import math
import threading
import urllib.request
from typing import Any

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from core.translations import tr
from core.utils import urlopen_with_fallback
from ui.widgets.base import WidgetBase

logger = logging.getLogger(__name__)

_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&ids={ids}&order=market_cap_desc"
    "&sparkline=true&price_change_percentage=24h"
)
_DEFAULT_COINS = ["bitcoin", "ethereum", "solana"]
_UPDATE_MS = 120_000


def _format_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.2f}"
    elif price >= 0.01:
        return f"${price:.4f}"
    else:
        s = f"{price:.10f}"
        frac = s.split(".")[1]
        z = 0
        for c in frac:
            if c == "0":
                z += 1
            else:
                break
        decimals = z + 4
        return f"${price:.{decimals}f}"


def _format_change(pct: float) -> str:
    arrow = chr(0x25B2) if pct >= 0 else chr(0x25BC)
    return f"{arrow} {abs(pct):.2f}%"


class CoinSparkline(Gtk.DrawingArea):
    """Mini sparkline chart drawn with Cairo for 7-day price trend."""

    def __init__(self, width: int = 72, height: int = 22) -> None:
        super().__init__()
        self._prices: list[float] = []
        self._up = True
        self._green: Gdk.RGBA | None = None
        self._red: Gdk.RGBA | None = None
        self.set_size_request(width, height)
        self.set_draw_func(self._on_draw)
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("sparkline")

    def set_data(self, prices: list[float]) -> None:
        self._prices = prices
        if len(prices) >= 2:
            self._up = prices[-1] >= prices[0]
        self.queue_draw()

    def _resolve_colors(self) -> tuple[Gdk.RGBA, Gdk.RGBA]:
        ctx = self.get_style_context()
        if self._green is None:
            g = Gdk.RGBA()
            ok, g = ctx.lookup_color("success_color")
            if not ok:
                g.parse("#40a02b")
            self._green = g
        if self._red is None:
            r = Gdk.RGBA()
            ok, r = ctx.lookup_color("error_color")
            if not ok:
                r.parse("#d20f39")
            self._red = r
        return self._green, self._red

    def _on_draw(
        self, area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int
    ) -> None:
        if len(self._prices) < 2:
            return
        green, red = self._resolve_colors()
        color = green if self._up else red
        n = len(self._prices)
        lo = min(self._prices)
        hi = max(self._prices)
        if hi == lo:
            return
        pad = 2.0
        plot_w = width - 2 * pad
        plot_h = height - 2 * pad
        points: list[tuple[float, float]] = []
        for i, p in enumerate(self._prices):
            x = pad + (i / (n - 1)) * plot_w
            y = pad + (1 - (p - lo) / (hi - lo)) * plot_h
            points.append((x, y))

        # Fill below the line
        if len(points) >= 2:
            cr.move_to(points[0][0], height)
            for x, y in points:
                cr.line_to(x, y)
            cr.line_to(points[-1][0], height)
            cr.close_path()
            cr.set_source_rgba(color.red, color.green, color.blue, 0.1)
            cr.fill()

        # Line
        cr.set_line_width(1.5)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.move_to(points[0][0], points[0][1])
        for x, y in points[1:]:
            cr.line_to(x, y)
        cr.set_source_rgba(color.red, color.green, color.blue, 0.85)
        cr.stroke()

        # Start/end dots
        for dot in (points[0], points[-1]):
            cr.arc(dot[0], dot[1], 1.5, 0, 2 * math.pi)
            cr.set_source_rgba(color.red, color.green, color.blue, 0.5)
            cr.fill()


class CoinGeckoWidget(WidgetBase):
    widget_type = "crypto"
    widget_title = "CoinGecko"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._timer_id: int | None = None
        self._fetched = False
        self._build_ui()
        self.connect("map", lambda *a: self._on_map())

    def _build_ui(self) -> None:
        # Coin rows container
        self._rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._rows_box.set_margin_start(8)
        self._rows_box.set_margin_end(8)
        self._rows_box.set_margin_top(6)
        self._rows_box.set_margin_bottom(6)
        self._content.append(self._rows_box)

        # Status label
        self._status_label = Gtk.Label(label=tr("Loading\u2026"))
        self._status_label.set_halign(Gtk.Align.CENTER)
        self._status_label.set_margin_top(8)
        self._status_label.set_margin_bottom(8)
        self._status_label.add_css_class("dim-label")
        self._content.append(self._status_label)

    def get_config_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        fields_label = Gtk.Label(
            label=tr("Coin IDs (one per line, e.g. bitcoin):"), xalign=0
        )
        box.append(fields_label)

        coins_text = "\n".join(self.settings.get("coins", _DEFAULT_COINS))
        self._cfg_coins_buffer = Gtk.TextBuffer()
        self._cfg_coins_buffer.set_text(coins_text)
        text_view = Gtk.TextView(buffer=self._cfg_coins_buffer)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.set_size_request(-1, 80)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(text_view)
        scrolled.set_min_content_height(80)
        box.append(scrolled)

        interval_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        interval_row.set_margin_top(6)
        interval_label = Gtk.Label(label=tr("Refresh (min):"))
        self._cfg_interval_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
        self._cfg_interval_spin.set_value(self.settings.get("interval_min", 2))
        interval_row.append(interval_label)
        interval_row.append(self._cfg_interval_spin)
        box.append(interval_row)

        return box

    def apply_config(self) -> None:
        buf = self._cfg_coins_buffer
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        coins = [line.strip().lower() for line in text.split("\n") if line.strip()]
        self.settings["coins"] = coins or _DEFAULT_COINS
        self.settings["interval_min"] = int(self._cfg_interval_spin.get_value())
        self._fetch_data()

    def _on_map(self) -> None:
        if not self._fetched:
            self._fetched = True
            GLib.idle_add(self._fetch_data)

    def _fetch_data(self) -> None:
        coins = self.settings.get("coins", _DEFAULT_COINS)
        if not coins:
            self._status_label.set_label(tr("No coins configured"))
            self._status_label.set_visible(True)
            return
        ids = ",".join(coins)
        threading.Thread(target=self._fetch_worker, args=(ids,), daemon=True).start()

    def _fetch_worker(self, ids: str) -> None:
        url = _MARKETS_URL.format(ids=ids)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TokyoNotes/1.0"})
            with urlopen_with_fallback(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            GLib.idle_add(lambda: self._populate(data))
        except Exception as e:
            logger.warning("CoinGecko fetch failed: %s", e)
            GLib.idle_add(self._show_error)

    def _populate(self, data: list[dict[str, Any]]) -> None:
        self._status_label.set_visible(False)
        self._rows_box.hide()

        while child := self._rows_box.get_first_child():
            self._rows_box.remove(child)

        fetched = False
        for coin in data:
            row = self._build_coin_row(coin)
            if row is not None:
                self._rows_box.append(row)
                fetched = True

        if fetched:
            self._rows_box.show()
            self._start_timer()
        else:
            self._status_label.set_label(tr("No data returned"))
            self._status_label.set_visible(True)

    def _build_coin_row(self, coin: dict[str, Any]) -> Gtk.Box | None:
        symbol = coin.get("symbol", "").upper()
        price = coin.get("current_price")
        pct = coin.get("price_change_percentage_24h")
        sparkline = coin.get("sparkline_in_7d", {}).get("price")

        if price is None:
            return None

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.FILL)

        badge = Gtk.Label(label=symbol)
        badge.add_css_class("coin-badge")
        row.append(badge)

        price_str = _format_price(price)
        price_lbl = Gtk.Label(label=price_str)
        price_lbl.set_xalign(1)
        price_lbl.set_hexpand(True)
        row.append(price_lbl)

        if pct is not None:
            change_str = _format_change(pct)
            change_lbl = Gtk.Label(label=change_str)
            change_lbl.set_xalign(1)
            if pct >= 0:
                change_lbl.add_css_class("coin-up")
            else:
                change_lbl.add_css_class("coin-down")
            row.append(change_lbl)

        if sparkline and len(sparkline) >= 2:
            sp = CoinSparkline(width=64, height=22)
            sp.set_data(sparkline)
            row.append(sp)

        return row

    def _show_error(self) -> None:
        self._status_label.set_label(tr("Failed to load prices"))
        self._status_label.set_visible(True)

    def stop_periodic(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _start_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        interval = max(self.settings.get("interval_min", 5), 1) * 60 * 1000
        self._timer_id = GLib.timeout_add(interval, self._on_timer)

    def _on_timer(self) -> bool:
        self._fetch_data()
        return True

    def update_periodic(self) -> None:
        self._fetch_data()
        self._start_timer()
