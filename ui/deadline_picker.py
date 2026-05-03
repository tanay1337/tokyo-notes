"""Deadline picker popover widget."""
from __future__ import annotations

import datetime
from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

class DeadlinePicker(Gtk.Popover):
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self.add_css_class("deadline-picker-popover")
        self.callback = callback
        
        box: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        
        self.calendar: Gtk.Calendar = Gtk.Calendar()
        box.append(self.calendar)
        
        self.time_entry: Gtk.Entry = Gtk.Entry()
        self.time_entry.set_placeholder_text("HH:MM")
        self.time_entry.set_text(datetime.datetime.now().strftime("%H:%M"))
        box.append(self.time_entry)
        
        btn: Gtk.Button = Gtk.Button(label="Set Deadline")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", self.on_set_clicked)
        box.append(btn)
        
        self.set_child(box)

    def on_set_clicked(self, btn: Gtk.Button) -> None:
        """Sets the deadline and closes popover."""
        year = self.calendar.get_year()
        month = self.calendar.get_month() + 1 # GTK Calendar month is 0-indexed
        day = self.calendar.get_day()
        date_str = f"{year}-{month:02d}-{day:02d}"
        time_str = self.time_entry.get_text()
        self.callback(f"{date_str} {time_str}")
        self.popdown()
