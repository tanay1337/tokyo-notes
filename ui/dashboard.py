"""Dashboard view for task management."""
from __future__ import annotations

import datetime
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango


class Dashboard(Gtk.Box):
    """Filterable task list built from checkbox metadata across all notes."""

    def __init__(
        self,
        on_checkbox_toggled: Callable[[Any, bool], Any],
        on_deadline_click: Callable[[Any, float, float], Any],
        on_row_click: Callable[[Any, int, float, float, Any], Any],
        on_empty: Callable[[str], Any],
        refresh_callback: Callable[[str], Any],
        get_show_completed: Callable[[], bool],
        default_filter: str = "today",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("dashboard-view")

        self.refresh_callback = refresh_callback
        self.on_checkbox_toggled = on_checkbox_toggled
        self.on_deadline_click = on_deadline_click
        self.on_row_click = on_row_click
        self.on_empty = on_empty
        self.get_show_completed = get_show_completed

        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        filter_box.add_css_class("toolbar")
        filter_box.set_halign(Gtk.Align.CENTER)

        self.buttons: dict[str, Gtk.Button] = {}
        for label in ("Today", "Week", "All"):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", self.on_filter_clicked, label.lower())
            filter_box.append(btn)
            self.buttons[label.lower()] = btn

        self.append(filter_box)

        self.active_filter: str = default_filter
        self.update_active_filter(default_filter)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)

        self.dashboard_list = Gtk.ListBox()
        # row-selected is intentionally not connected: navigation is handled
        # by gesture controllers on each row's chip and text widgets.
        scrolled.set_child(self.dashboard_list)
        self.append(scrolled)

    # Filter controls

    def on_filter_clicked(self, btn: Gtk.Button, filter_type: str) -> None:
        self.update_active_filter(filter_type)
        self.refresh_callback(filter_type)

    def update_active_filter(self, active_type: str) -> None:
        self.active_filter = active_type
        for f_type, btn in self.buttons.items():
            if f_type == active_type:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    # Population

    def populate(self, checkboxes: list[dict[str, Any]], filter_type: str) -> int:
        """Clear and repopulate the list for *filter_type*. Returns visible item count."""
        self._clear()
        filtered = self._filter(checkboxes, filter_type)

        if not filtered:
            self.on_empty(filter_type)
            return 0

        if filter_type in ("week", "all"):
            self._populate_grouped(filtered, include_misc=(filter_type == "all"))
        else:
            self._populate_flat(filtered)

        return len(filtered)

    def _clear(self) -> None:
        while (child := self.dashboard_list.get_first_child()):
            self.dashboard_list.remove(child)

    def _filter(
        self, checkboxes: list[dict[str, Any]], filter_type: str
    ) -> list[dict[str, Any]]:
        _TODAY = datetime.date.today().isoformat()
        _NEXT_WEEK = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()

        if self.get_show_completed():
            pool = checkboxes
        else:
            pool = [cb for cb in checkboxes if not cb["checked"]]

        if filter_type == "today":
            return [
                cb for cb in pool
                if (cb.get("deadline") or "").startswith(_TODAY)
            ]
        if filter_type == "week":
            return [
                cb for cb in pool
                if cb.get("deadline") and cb["deadline"] <= _NEXT_WEEK
            ]
        return pool

    def _populate_flat(self, items: list[dict[str, Any]]) -> None:
        for cb in sorted(items, key=lambda x: x.get("deadline") or ""):
            self.dashboard_list.append(self._make_row(cb))

    def _populate_grouped(
        self, items: list[dict[str, Any]], include_misc: bool = False
    ) -> None:
        items_with = sorted(
            [cb for cb in items if cb.get("deadline")],
            key=lambda x: x["deadline"],
        )
        items_without = [cb for cb in items if not cb.get("deadline")]

        current_date: str | None = None
        for cb in items_with:
            date_str = cb["deadline"].split(" ")[0]
            if date_str != current_date:
                current_date = date_str
                self.dashboard_list.append(self._make_date_header(date_str))
            self.dashboard_list.append(self._make_row(cb))

        if include_misc and items_without:
            self.dashboard_list.append(
                self._make_date_header(None, label="No Deadline")
            )
            for cb in items_without:
                self.dashboard_list.append(self._make_row(cb))

    # Row builders

    def _make_date_header(
        self, date_str: str | None, label: str | None = None
    ) -> Gtk.ListBoxRow:
        if label is None:
            try:
                dt = datetime.datetime.strptime(date_str or "", "%Y-%m-%d")
                # Always include year so Dec 31 vs Jan 1 is unambiguous.
                label = dt.strftime("%A, %B %d, %Y")
            except ValueError:
                label = date_str or ""

        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("day-header")
        row = Gtk.ListBoxRow()
        row.set_child(lbl)
        row.set_selectable(False)
        return row

    def _make_row(self, cb: dict[str, Any]) -> Gtk.ListBoxRow:
        """Build a single task row for *cb*."""
        row = Gtk.ListBoxRow()
        row.add_css_class("calendar-row")
        row.checkbox_data = cb
        row.set_selectable(False)
        if cb["checked"]:
            row.add_css_class("task-completed-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # Deadline badge — only show time portion if one was specified.
        deadline: str = cb.get("deadline") or ""
        if " " in deadline:
            time_str = deadline.split(" ")[1]
        else:
            time_str = ""   # date-only or no deadline: show nothing in time column

        if time_str:
            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("time-column")
            if cb["checked"]:
                time_label.add_css_class("task-completed")
            deadline_gesture = Gtk.GestureClick.new()
            deadline_gesture.connect(
                "pressed", lambda *a, _cb=cb: self.on_deadline_click(_cb, a[2], a[3])
            )
            time_label.add_controller(deadline_gesture)
            box.append(time_label)

        # Checkbox — Bug fix: block "toggled" signal during programmatic
        # set_active() so that rebuilding the list doesn't re-fire toggles
        # for already-checked items and corrupt the underlying note.
        checkbox = Gtk.CheckButton()
        handler_id = checkbox.connect(
            "toggled",
            lambda btn, _cb=cb: self.on_checkbox_toggled(_cb, btn.get_active()),
        )
        checkbox.handler_block(handler_id)
        checkbox.set_active(cb["checked"])
        checkbox.handler_unblock(handler_id)
        if cb["checked"]:
            checkbox.add_css_class("task-completed")
        box.append(checkbox)

        # Task text — ellipsise long text instead of clipping.
        text_label = Gtk.Label(label=cb["text"], xalign=0)
        text_label.set_hexpand(True)
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
        if cb["checked"]:
            text_label.add_css_class("task-completed")
        box.append(text_label)

        # Note chip — double-click navigates to the source note.
        chip = Gtk.Label(label=cb["note"])
        chip.add_css_class("note-chip")
        chip.add_css_class("dim-chip")
        chip_gesture = Gtk.GestureClick.new()
        chip_gesture.connect(
            "pressed", lambda *a, _cb=cb: self.on_row_click(a[0], a[1], a[2], a[3], _cb)
        )
        chip.add_controller(chip_gesture)
        box.append(chip)

        row.set_child(box)
        return row
