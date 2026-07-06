from __future__ import annotations

import json
import logging
import threading
import urllib.request
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from core.translations import tr
from core.utils import urlopen_with_fallback
from ui.widgets.base import WidgetBase

logger = logging.getLogger(__name__)


def _resolve_path(data: Any, path: str) -> Any:
    raw = path.strip()
    if raw.startswith("json."):
        raw = raw[5:]
    elif raw.startswith("$."):
        raw = raw[2:]
    parts = raw.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


class APIWidget(WidgetBase):
    widget_type = "api"
    widget_title = "API Data"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._timer_id: int | None = None
        self._fetched = False

        self._grid = Gtk.Grid()
        self._grid.set_column_spacing(8)
        self._grid.set_row_spacing(4)
        self._grid.set_margin_top(8)
        self._grid.set_margin_bottom(8)
        self._grid.set_margin_start(8)
        self._grid.set_margin_end(8)
        self._content.append(self._grid)

        self._status_label = Gtk.Label(label=tr("Configure URL in widget settings"))
        self._status_label.set_halign(Gtk.Align.CENTER)
        self._status_label.set_margin_top(12)
        self._status_label.set_margin_bottom(12)
        self._content.append(self._status_label)

        self.connect("map", lambda *a: self._on_map())

    def _on_map(self) -> None:
        if not self._fetched:
            self._fetched = True
            GLib.idle_add(self._fetch_data)

    def get_config_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        url_label = Gtk.Label(label=tr("URL:"))
        self._cfg_url_entry = Gtk.Entry()
        self._cfg_url_entry.set_text(self.settings.get("url", ""))
        self._cfg_url_entry.set_hexpand(True)
        url_row.append(url_label)
        url_row.append(self._cfg_url_entry)
        box.append(url_row)

        fields_label = Gtk.Label(
            label=tr("Fields (label:json.path, one per line):"), xalign=0
        )
        box.append(fields_label)

        fields_text = "\n".join(
            f"{f['label']}:{f['path']}" for f in self.settings.get("fields", [])
        )
        self._cfg_fields_buffer = Gtk.TextBuffer()
        self._cfg_fields_buffer.set_text(fields_text)
        text_view = Gtk.TextView(buffer=self._cfg_fields_buffer)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.set_size_request(-1, 100)
        fields_scrolled = Gtk.ScrolledWindow()
        fields_scrolled.set_child(text_view)
        fields_scrolled.set_min_content_height(100)
        box.append(fields_scrolled)

        interval_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        interval_label = Gtk.Label(label=tr("Refresh (min):"))
        self._cfg_interval_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
        self._cfg_interval_spin.set_value(self.settings.get("interval_min", 5))
        interval_row.append(interval_label)
        interval_row.append(self._cfg_interval_spin)
        box.append(interval_row)

        return box

    def apply_config(self) -> None:
        self.settings["url"] = self._cfg_url_entry.get_text().strip()
        self.settings["interval_min"] = int(self._cfg_interval_spin.get_value())
        fields = []
        buf = self._cfg_fields_buffer
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        for line in text.strip().split("\n"):
            line = line.strip()
            if ":" in line:
                label, path = line.split(":", 1)
                fields.append({"label": label.strip(), "path": path.strip()})
        self.settings["fields"] = fields
        self._fetch_data()

    def _fetch_data(self) -> None:
        url = self.settings.get("url", "")
        fields = self.settings.get("fields", [])

        self._status_label.set_visible(False)

        while (child := self._grid.get_first_child()) is not None:
            self._grid.remove(child)

        if not url:
            self._status_label.set_label(tr("No URL configured"))
            self._status_label.set_visible(True)
            return

        if not fields:
            self._status_label.set_label(tr("No fields configured"))
            self._status_label.set_visible(True)
            return

        threading.Thread(
            target=self._fetch_data_worker, args=(url, fields), daemon=True
        ).start()

    def _fetch_data_worker(self, url: str, fields: list[dict[str, str]]) -> None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TokyoNotes/1.0"})
            with urlopen_with_fallback(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            GLib.idle_add(lambda: self._populate_api_data(data, fields))
        except urllib.error.HTTPError as e:
            code = e.code
            GLib.idle_add(lambda: self._show_api_error(f"HTTP {code}"))
        except Exception as e:
            logger.warning("API fetch failed: %s", e)
            GLib.idle_add(lambda: self._show_api_error(tr("Failed to fetch")))

    def _populate_api_data(self, data: Any, fields: list[dict[str, str]]) -> None:
        for i, field in enumerate(fields):
            label = field.get("label", "")
            path = field.get("path", "")
            value = _resolve_path(data, path)
            if value is None:
                value = "—"

            lbl_w = Gtk.Label(label=label, xalign=1)
            lbl_w.add_css_class("dim-label")
            val_w = Gtk.Label(label=str(value), xalign=0, selectable=True)
            val_w.set_wrap(True)

            self._grid.attach(lbl_w, 0, i, 1, 1)
            self._grid.attach(val_w, 1, i, 1, 1)

        self._grid.show()

    def _show_api_error(self, message: str) -> None:
        self._status_label.set_label(message)
        self._status_label.set_visible(True)

    def update_periodic(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._fetch_data()
        interval = max(self.settings.get("interval_min", 5), 1) * 60 * 1000
        self._timer_id = GLib.timeout_add(interval, self._fetch_data)
