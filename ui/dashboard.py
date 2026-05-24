"""Dashboard view for task management."""
from __future__ import annotations

import datetime
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango

from core.utils import clear_listbox
from ui.progress_ring import ProgressRing


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
        get_show_progress_rings: Callable[[], bool],
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
        self.get_show_progress_rings = get_show_progress_rings
        self._prev_stats: dict[str, tuple[int, int]] = {}
        self._collapsed: set[str] = set()
        self._date_rows: dict[str, list[Gtk.ListBoxRow]] = {}
        self._header_rows: dict[str, Gtk.ListBoxRow] = {}

        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        filter_box.add_css_class("toolbar")
        filter_box.set_halign(Gtk.Align.CENTER)

        self.buttons: dict[str, Gtk.Button] = {}
        for label in ("Today", "Week", "All"):
            btn = Gtk.Button(label=label)
            btn.add_css_class("filter-btn")
            btn.connect("clicked", self.on_filter_clicked, label.lower())
            filter_box.append(btn)
            self.buttons[label.lower()] = btn

        shortcut_ctrl = Gtk.ShortcutController()
        shortcut_ctrl.set_scope(Gtk.ShortcutScope.MANAGED)
        for key, filter_type in (("1", "today"), ("2", "week"), ("3", "all")):
            trigger = Gtk.ShortcutTrigger.parse_string(f"<Primary>{key}")
            action = Gtk.CallbackAction.new(lambda *_a, ft=filter_type: self._activate_filter(ft))
            shortcut_ctrl.add_shortcut(Gtk.Shortcut(trigger=trigger, action=action))
        self.add_controller(shortcut_ctrl)

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

    def _activate_filter(self, filter_type: str) -> bool:
        if self.active_filter != filter_type:
            self.on_filter_clicked(None, filter_type)
        return True

    # Population

    def populate(self, checkboxes: list[dict[str, Any]], filter_type: str) -> int:
        """Clear and repopulate the list for *filter_type*. Returns visible item count."""
        self._clear()
        filtered = self._filter(checkboxes, filter_type)

        if not filtered:
            self.on_empty(filter_type)
            return 0

        if filter_type in ("week", "all"):
            self._populate_grouped(filtered, include_misc=(filter_type == "all"), show_year=(filter_type == "all"))
        else:
            self._populate_flat(filtered)

        return len(filtered)

    def _clear(self) -> None:
        clear_listbox(self.dashboard_list)

    def _filter(
        self, checkboxes: list[dict[str, Any]], filter_type: str
    ) -> list[dict[str, Any]]:
        today = datetime.date.today()
        _TODAY = today.isoformat()
        _WEEK_START = (today - datetime.timedelta(days=today.weekday())).isoformat()
        _WEEK_END = (today + datetime.timedelta(days=6 - today.weekday())).isoformat()

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
                if cb.get("deadline") and _WEEK_START <= cb["deadline"] <= _WEEK_END
            ]
        return pool

    def _populate_flat(self, items: list[dict[str, Any]]) -> None:
        completed = sum(1 for cb in items if cb["checked"])
        total = len(items)
        today_key = datetime.date.today().isoformat()
        self._date_rows.clear()
        self._header_rows.clear()
        if total > 0:
            today = datetime.date.today().strftime("%A, %B %d")
            prev = self._prev_stats.get(today_key, (0, 0))
            animate = (completed, total) != prev
            header_row = self._make_date_header(
                None, label=today, progress=(completed, total),
                show_year=False, animate=animate, show_disclosure=False,
            )
            self.dashboard_list.append(header_row)
            self._header_rows[today_key] = header_row
            self._prev_stats[today_key] = (completed, total)
        for cb in sorted(items, key=lambda x: x.get("deadline") or ""):
            task_row = self._make_row(cb)
            self.dashboard_list.append(task_row)
            self._date_rows.setdefault(today_key, []).append(task_row)

    def _populate_grouped(
        self, items: list[dict[str, Any]], include_misc: bool = False,
        show_year: bool = False,
    ) -> None:
        items_with = sorted(
            [cb for cb in items if cb.get("deadline")],
            key=lambda x: x["deadline"],
        )
        items_without = [cb for cb in items if not cb.get("deadline")]

        date_stats: dict[str, tuple[int, int]] = {}
        for cb in items_with:
            date_str = cb["deadline"].split(" ")[0]
            if date_str not in date_stats:
                date_stats[date_str] = (0, 0)
            c, t = date_stats[date_str]
            date_stats[date_str] = (c + (1 if cb["checked"] else 0), t + 1)

        self._date_rows.clear()
        self._header_rows.clear()
        current_date: str | None = None
        for cb in items_with:
            date_str = cb["deadline"].split(" ")[0]
            if date_str != current_date:
                current_date = date_str
                is_collapsed = date_str in self._collapsed
                prev = self._prev_stats.get(date_str, (0, 0))
                animate = date_stats[date_str] != prev
                header_row = self._make_date_header(
                    date_str, progress=date_stats[date_str],
                    show_year=show_year, animate=animate,
                    collapsed=is_collapsed,
                )
                self.dashboard_list.append(header_row)
                self._date_rows[date_str] = []
                self._header_rows[date_str] = header_row
            task_row = self._make_row(cb)
            self.dashboard_list.append(task_row)
            self._date_rows[date_str].append(task_row)
            if is_collapsed:
                task_row.set_visible(False)

        if include_misc and items_without:
            nd_completed = sum(1 for cb in items_without if cb["checked"])
            nd_total = len(items_without)
            nd_key = "no_deadline"
            nd_stats = (nd_completed, nd_total)
            date_stats[nd_key] = nd_stats
            prev = self._prev_stats.get(nd_key, (0, 0))
            animate = nd_stats != prev
            is_collapsed = nd_key in self._collapsed
            header_row = self._make_date_header(
                None, label="No Deadline", progress=nd_stats,
                show_year=False, animate=animate,
                collapsed=is_collapsed,
            )
            self.dashboard_list.append(header_row)
            self._date_rows[nd_key] = []
            self._header_rows[nd_key] = header_row
            for cb in items_without:
                task_row = self._make_row(cb)
                self.dashboard_list.append(task_row)
                self._date_rows[nd_key].append(task_row)
                if is_collapsed:
                    task_row.set_visible(False)

        self._prev_stats = date_stats

    # Row builders

    def _make_date_header(
        self, date_str: str | None, label: str | None = None,
        progress: tuple[int, int] | None = None,
        show_year: bool = False,
        animate: bool = True,
        collapsed: bool = False,
        show_disclosure: bool = True,
    ) -> Gtk.ListBoxRow:
        if label is None:
            try:
                dt = datetime.datetime.strptime(date_str or "", "%Y-%m-%d")
                label = dt.strftime("%A, %B %d, %Y" if show_year else "%A, %B %d")
            except ValueError:
                label = date_str or ""

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(15)
        box.set_margin_bottom(5)
        box.set_margin_start(10)
        box.set_margin_end(10)

        if show_disclosure:
            triangle = Gtk.Label(label="▼" if not collapsed else "▶")
            triangle.add_css_class("disclosure-triangle")
            box.append(triangle)

        ring_widget: ProgressRing | None = None
        if progress is not None and self.get_show_progress_rings():
            ring_widget = ProgressRing()
            ring_widget.set_progress(progress[0], progress[1], animate=animate)
            box.append(ring_widget)

        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("day-header")
        box.append(lbl)

        if show_disclosure:
            gesture = Gtk.GestureClick.new()
            gesture.connect("pressed", lambda *a, _d=date_str or "no_deadline", _t=triangle: self._toggle_date(_d, _t))
            box.add_controller(gesture)

        row = Gtk.ListBoxRow()
        row.set_child(box)
        row.set_selectable(False)
        if ring_widget is not None:
            row._progress_ring = ring_widget
        return row

    def _toggle_date(self, date_key: str, triangle: Gtk.Label) -> None:
        if date_key in self._collapsed:
            self._collapsed.discard(date_key)
            triangle.set_label("▼")
            for task_row in self._date_rows.get(date_key, []):
                task_row.set_visible(True)
        else:
            self._collapsed.add(date_key)
            triangle.set_label("▶")
            for task_row in self._date_rows.get(date_key, []):
                task_row.set_visible(False)

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

        row._time_label = None
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
            row._time_label = time_label

        # Checkbox — Bug fix: block "toggled" signal during programmatic
        # set_active() so that rebuilding the list doesn't re-fire toggles
        # for already-checked items and corrupt the underlying note.
        checkbox = Gtk.CheckButton()
        row._checkbox_handler_id = checkbox.connect(
            "toggled",
            lambda btn, _cb=cb: self.on_checkbox_toggled(_cb, btn.get_active()),
        )
        checkbox.handler_block(row._checkbox_handler_id)
        checkbox.set_active(cb["checked"])
        checkbox.handler_unblock(row._checkbox_handler_id)
        if cb["checked"]:
            checkbox.add_css_class("task-completed")
        box.append(checkbox)
        row._checkbox = checkbox

        # Task text — ellipsise long text instead of clipping.
        text_label = Gtk.Label(label=cb["text"], xalign=0)
        text_label.set_hexpand(True)
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
        if cb["checked"]:
            text_label.add_css_class("task-completed")
        box.append(text_label)
        row._text_label = text_label

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

    def update_checkbox(self, note_name: str, line_num: int, checked: bool) -> bool:
        """Update a single checkbox row in-place without full rebuild.

        Returns True if the row was found and updated, False otherwise
        (caller should fall back to a full refresh).
        """
        target_row: Gtk.ListBoxRow | None = None
        target_date: str | None = None
        for date_key, rows in self._date_rows.items():
            for row in rows:
                cb = getattr(row, "checkbox_data", None)
                if cb and cb.get("note") == note_name and cb.get("line") == line_num:
                    target_row = row
                    target_date = date_key
                    break
            if target_row:
                break

        if not target_row:
            return False

        target_row.checkbox_data["checked"] = checked

        checkbox: Gtk.CheckButton | None = getattr(target_row, "_checkbox", None)
        if checkbox:
            h_id = getattr(target_row, "_checkbox_handler_id", 0)
            if h_id:
                checkbox.handler_block(h_id)
            try:
                checkbox.set_active(checked)
            finally:
                if h_id:
                    checkbox.handler_unblock(h_id)
            if checked:
                checkbox.add_css_class("task-completed")
            else:
                checkbox.remove_css_class("task-completed")

        text_label: Gtk.Label | None = getattr(target_row, "_text_label", None)
        if text_label:
            if checked:
                text_label.add_css_class("task-completed")
            else:
                text_label.remove_css_class("task-completed")

        time_label: Gtk.Label | None = getattr(target_row, "_time_label", None)
        if time_label:
            if checked:
                time_label.add_css_class("task-completed")
            else:
                time_label.remove_css_class("task-completed")

        if checked:
            target_row.add_css_class("task-completed-row")
        else:
            target_row.remove_css_class("task-completed-row")

        if target_date and target_date in self._header_rows:
            header_row = self._header_rows[target_date]
            rows = self._date_rows.get(target_date, [])
            completed = sum(1 for r in rows if getattr(r, "checkbox_data", {}).get("checked", False))
            total = len(rows)
            ring: ProgressRing | None = getattr(header_row, "_progress_ring", None)
            if ring:
                ring.set_progress(completed, total, animate=True)

        return True
