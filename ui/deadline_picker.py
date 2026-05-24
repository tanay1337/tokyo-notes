"""Deadline picker popover widget."""
from __future__ import annotations

import datetime
import re
from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class DeadlinePicker(Gtk.Popover):
    """Calendar + optional time entry for picking a task deadline."""

    def __init__(self, callback: Callable[[str | None], None]) -> None:
        super().__init__()
        self.add_css_class("deadline-picker-popover")
        self.callback = callback

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        self.calendar = Gtk.Calendar()
        box.append(self.calendar)

        # Default to date-only — most deadlines are date-based, not time-based.
        self.time_entry = Gtk.Entry()
        self.time_entry.set_placeholder_text("HH:MM (optional)")
        self.time_entry.connect("changed", self._on_time_changed)
        box.append(self.time_entry)

        self._time_error = Gtk.Label(xalign=0)
        self._time_error.add_css_class("error-label")
        self._time_error.set_visible(False)
        box.append(self._time_error)

        # Action row: clear on the left, set on the right.
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", self.on_clear_clicked)
        btn_row.append(clear_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_row.append(spacer)

        set_btn = Gtk.Button(label="Set Deadline")
        set_btn.add_css_class("suggested-action")
        set_btn.connect("clicked", self.on_set_clicked)
        btn_row.append(set_btn)

        box.append(btn_row)
        self.set_child(box)

    def _on_time_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if not text:
            self._time_error.set_visible(False)
            return
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text):
            self._time_error.set_visible(False)
        else:
            self._time_error.set_label("Invalid time — use HH:MM format (e.g. 14:30)")
            self._time_error.set_visible(True)

    def on_set_clicked(self, btn: Gtk.Button) -> None:
        year = self.calendar.get_year()
        month = self.calendar.get_month() + 1  # GTK months are 0-indexed
        day = self.calendar.get_day()
        try:
            dt = datetime.date(year, month, day)
        except ValueError:
            self.callback(None)
            self.popdown()
            return
        date_str = dt.isoformat()
        time_str = self.time_entry.get_text().strip()
        if time_str and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str):
            return  # keep picker open until time is fixed or cleared
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str):
            self.callback(f"{date_str} {time_str}")
        else:
            self.callback(date_str)
        self.popdown()

    def on_clear_clicked(self, btn: Gtk.Button) -> None:
        """Remove the deadline entirely."""
        self.callback(None)
        self.popdown()
