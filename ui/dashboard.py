import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

import datetime
import re

class Dashboard(Gtk.Box):
    def __init__(self, on_item_selected, on_checkbox_toggled, on_deadline_click, 
                 on_empty, refresh_callback, default_filter="today"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("dashboard-view")
        self.refresh_callback = refresh_callback
        self.on_checkbox_toggled = on_checkbox_toggled
        self.on_deadline_click = on_deadline_click
        self.on_empty = on_empty
        
        # Filter Bar
        self.filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.filter_box.add_css_class("toolbar")
        self.filter_box.set_halign(Gtk.Align.CENTER)
        
        self.buttons = {}
        for label in ["Today", "Week", "All"]:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", self.on_filter_clicked, label.lower())
            self.filter_box.append(btn)
            self.buttons[label.lower()] = btn
            
        self.append(self.filter_box)
        
        # Initialize active button
        self.active_filter = default_filter
        self.update_active_filter(default_filter)
        
        scrolled_dashboard = Gtk.ScrolledWindow()
        scrolled_dashboard.set_vexpand(True)
        scrolled_dashboard.set_hexpand(True)
        
        self.dashboard_list = Gtk.ListBox()
        self.dashboard_list.connect("row-selected", on_item_selected)
        scrolled_dashboard.set_child(self.dashboard_list)
        
        self.append(scrolled_dashboard)

    def on_filter_clicked(self, btn, filter_type):
        self.update_active_filter(filter_type)
        self.refresh_callback(filter_type)

    def update_active_filter(self, active_type):
        self.active_filter = active_type
        for f_type, btn in self.buttons.items():
            if f_type == active_type:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def populate(self, checkboxes: list, filter_type: str):
        """Clear and repopulate the dashboard list for the given filter."""
        while (child := self.dashboard_list.get_first_child()):
            self.dashboard_list.remove(child)

        filtered = self._filter(checkboxes, filter_type)

        if not filtered:
            # Show empty state — caller provides the widget via a callback
            self.on_empty(filter_type)
            return 0

        if filter_type in ("week", "all"):
            self._populate_grouped(filtered, include_misc=(filter_type == "all"))
        else:
            self._populate_flat(filtered)

        return len(filtered)

    def _filter(self, checkboxes, filter_type):
        today = datetime.date.today()
        today_str = today.isoformat()
        next_week_str = (today + datetime.timedelta(days=7)).isoformat()
        unchecked = [cb for cb in checkboxes if not cb['checked']]
        if filter_type == "today":
            return [cb for cb in unchecked if (cb.get('deadline') or '').startswith(today_str)]
        if filter_type == "week":
            return [cb for cb in unchecked if cb.get('deadline') and cb['deadline'] <= next_week_str]
        return unchecked

    def _populate_flat(self, items):
        for cb in sorted(items, key=lambda x: x.get('deadline', '')):
            self.dashboard_list.append(self._make_row(cb))

    def _populate_grouped(self, items, include_misc=False):
        items_with = sorted(
            [cb for cb in items if cb.get('deadline')],
            key=lambda x: x['deadline']
        )
        items_without = [cb for cb in items if not cb.get('deadline')]
        current_date = None
        for cb in items_with:
            date_str = cb['deadline'].split(' ')[0]
            if date_str != current_date:
                current_date = date_str
                self.dashboard_list.append(self._make_date_header(date_str))
            self.dashboard_list.append(self._make_row(cb))
        if include_misc and items_without:
            self.dashboard_list.append(self._make_date_header(None, label="Miscellaneous"))
            for cb in items_without:
                self.dashboard_list.append(self._make_row(cb))

    def _make_date_header(self, date_str, label=None):
        if label is None:
            try:
                dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                label = dt.strftime("%A, %B %d")
            except ValueError:
                label = date_str
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("day-header")
        row = Gtk.ListBoxRow()
        row.set_child(lbl)
        row.set_selectable(False)
        return row

    def _make_row(self, cb):
        """Build a single task row. Checkbox-toggle and deadline-click callbacks
        are emitted as signals so the caller (main.py) handles business logic."""
        row = Gtk.ListBoxRow()
        row.add_css_class("calendar-row")
        row.checkbox_data = cb
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        time_str = cb['deadline'].split(' ')[1] if cb.get('deadline') and ' ' in cb['deadline'] else "All Day"
        time_label = Gtk.Label(label=time_str)
        time_label.add_css_class("time-column")
        gesture = Gtk.GestureClick.new()
        gesture.connect("pressed", lambda g, n, x, y, _cb=cb: self.on_deadline_click(_cb, x, y))
        time_label.add_controller(gesture)
        box.append(time_label)

        checkbox = Gtk.CheckButton()
        checkbox.set_active(cb['checked'])
        checkbox.connect("toggled", lambda btn, _cb=cb: self.on_checkbox_toggled(_cb, btn.get_active()))
        box.append(checkbox)

        label = Gtk.Label(label=cb['text'], xalign=0)
        label.set_hexpand(True)
        box.append(label)

        chip = Gtk.Label(label=cb['note'])
        chip.add_css_class("note-chip")
        box.append(chip)

        row.set_child(box)
        return row
