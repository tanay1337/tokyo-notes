"""Dashboard view for task management."""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, Gtk, Pango

from core.utils import clear_listbox
from ui.deadline_picker import DeadlinePicker
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
        get_start_week_on_sunday: Callable[[], bool],
        on_snooze: Callable[[str, int, str | None], Any] | None = None,
        assets_dir: Path | None = None,
        default_filter: str = "today",
        on_quick_add: Callable[[str, str, str | None], Any] | None = None,
        get_notes_fn: Callable[[], list[str]] | None = None,
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
        self.get_start_week_on_sunday = get_start_week_on_sunday
        self.on_snooze = on_snooze
        self.on_quick_add = on_quick_add
        self.get_notes_fn = get_notes_fn or (lambda: [])
        self._assets_dir = assets_dir
        self._quick_add_deadline: str | None = None
        self._prev_stats: dict[str, tuple[int, int]] = {}
        self._collapsed: set[str] = set()
        self._date_rows: dict[str, list[Gtk.ListBoxRow]] = {}
        self._header_rows: dict[str, Gtk.ListBoxRow] = {}
        self._search_text: str = ""
        self._show_overdue: bool = False
        self._filter_date: str | None = None
        self._show_completed: bool = self.get_show_completed()
        self._temp_show_completed: bool = False

        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        filter_box.add_css_class("dashboard-toolbar")

        # Spacers to keep filter buttons centered
        left_spacer = Gtk.Box()
        left_spacer.set_hexpand(True)
        filter_box.append(left_spacer)

        self.buttons: dict[str, Gtk.Button] = {}
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        for label in ("Today", "Week", "All"):
            btn = Gtk.Button(label=label)
            btn.add_css_class("filter-btn")
            btn.connect("clicked", self.on_filter_clicked, label.lower())
            btn_box.append(btn)
            self.buttons[label.lower()] = btn
        filter_box.append(btn_box)

        right_spacer = Gtk.Box()
        right_spacer.set_hexpand(True)
        filter_box.append(right_spacer)

        # Quick Add "+" button
        self.quick_add_btn = Gtk.Button()
        self.quick_add_btn.set_tooltip_text("Quick Add Task (Ctrl+T)")
        self.quick_add_btn.add_css_class("flat")
        self.quick_add_btn.add_css_class("header-btn")
        quick_add_icon = Gtk.Image.new_from_file(
            str(assets_dir / "dashboard" / "new.svg")
        )
        quick_add_icon.set_pixel_size(16)
        self.quick_add_btn.set_child(quick_add_icon)
        self.quick_add_btn.connect("clicked", self._on_quick_add_clicked)
        filter_box.append(self.quick_add_btn)

        # Advanced filter button — funnel icon with indicator dot
        self.advanced_btn = Gtk.Button()
        self.advanced_btn.set_tooltip_text("Advanced filters")
        self.advanced_btn.add_css_class("flat")
        self.advanced_btn.add_css_class("advanced-filter-btn")

        # Load SVG icon from assets
        icon_path = (assets_dir / "dashboard" / "filter.svg") if assets_dir else None
        funnel_icon: Gtk.Widget
        if icon_path and icon_path.exists():
            funnel_icon = Gtk.Image.new_from_file(str(icon_path))
            funnel_icon.set_pixel_size(16)
        else:
            funnel_icon = Gtk.Label(label="⋮")
            funnel_icon.add_css_class("advanced-filter-fallback")

        self.advanced_btn.set_child(funnel_icon)
        self.advanced_btn.connect("clicked", self._on_advanced_filter_clicked)

        # Outer overlay positions the indicator dot at the button's top-right corner
        self._filter_indicator = Gtk.Label(label="●")
        self._filter_indicator.add_css_class("filter-indicator-dot")
        self._filter_indicator.set_halign(Gtk.Align.END)
        self._filter_indicator.set_valign(Gtk.Align.START)
        self._filter_indicator.set_margin_end(1)
        self._filter_indicator.set_margin_top(1)
        self._filter_indicator.set_visible(False)

        btn_overlay = Gtk.Overlay()
        btn_overlay.set_child(self.advanced_btn)
        btn_overlay.add_overlay(self._filter_indicator)
        filter_box.append(btn_overlay)

        # Popover for advanced filters
        self._filter_popover = Gtk.Popover()
        self._filter_popover.set_autohide(True)
        self._filter_popover.set_parent(self.advanced_btn)
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        popover_box.set_margin_top(8)
        popover_box.set_margin_bottom(8)
        popover_box.set_margin_start(8)
        popover_box.set_margin_end(8)
        popover_box.add_css_class("filter-popover-box")

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search tasks…")
        self._search_entry.connect("search-changed", self._on_search_changed)
        popover_box.append(self._search_entry)

        completed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        completed_label = Gtk.Label(label="Show Completed", xalign=0)
        completed_label.set_hexpand(True)
        self._completed_switch = Gtk.Switch()
        self._completed_switch.set_active(self._show_completed)
        self._completed_switch.connect("notify::active", self._on_completed_toggled)
        completed_box.append(completed_label)
        completed_box.append(self._completed_switch)
        popover_box.append(completed_box)

        overdue_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        overdue_label = Gtk.Label(label="Show Overdue", xalign=0)
        overdue_label.set_hexpand(True)
        self._overdue_switch = Gtk.Switch()
        self._overdue_switch.set_active(self._show_overdue)
        self._overdue_switch.connect("notify::active", self._on_overdue_toggled)
        overdue_box.append(overdue_label)
        overdue_box.append(self._overdue_switch)
        popover_box.append(overdue_box)

        self._calendar = Gtk.Calendar()
        self._calendar.connect("day-selected", self._on_calendar_date_selected)
        popover_box.append(self._calendar)

        clear_date_btn = Gtk.Button(label="Clear date")
        clear_date_btn.add_css_class("flat")
        clear_date_btn.connect("clicked", self._on_clear_date)
        popover_box.append(clear_date_btn)

        collapse_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        collapse_all_btn = Gtk.Button(label="Collapse All")
        collapse_all_btn.add_css_class("flat")
        collapse_all_btn.connect("clicked", self._collapse_all)
        collapse_box.append(collapse_all_btn)
        expand_all_btn = Gtk.Button(label="Expand All")
        expand_all_btn.add_css_class("flat")
        expand_all_btn.connect("clicked", self._expand_all)
        collapse_box.append(expand_all_btn)
        popover_box.append(collapse_box)

        self._filter_popover.set_child(popover_box)

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

        self._build_quick_add_popover()

    # ── Quick Add ──

    def _build_quick_add_popover(self) -> None:
        self._quick_add_popover = Gtk.Popover()
        self._quick_add_popover.set_autohide(False)
        self._quick_add_popover.set_parent(self.quick_add_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.add_css_class("quick-add-popover-box")

        heading = Gtk.Label(label="Add Task")
        heading.add_css_class("quick-add-heading")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._quick_add_entry = Gtk.Entry()
        self._quick_add_entry.set_placeholder_text("Task content")
        self._quick_add_entry.set_hexpand(True)
        self._quick_add_entry.add_css_class("quick-add-entry")
        self._quick_add_entry.connect("activate", self._on_quick_add_submit)
        box.append(self._quick_add_entry)

        note_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._quick_add_notes_model = Gtk.StringList.new([])
        self._quick_add_dropdown = Gtk.DropDown(model=self._quick_add_notes_model)
        self._quick_add_dropdown.set_hexpand(True)
        self._quick_add_dropdown.add_css_class("quick-add-dropdown")
        self._quick_add_dropdown.set_size_request(-1, 24)
        note_row.append(self._quick_add_dropdown)

        self._quick_add_deadline_btn = Gtk.Button()
        self._quick_add_deadline_btn.set_tooltip_text("Set deadline")
        self._quick_add_deadline_btn.add_css_class("flat")
        self._quick_add_deadline_btn.add_css_class("header-btn")
        cal_icon = Gtk.Image.new_from_file(
            str(self._assets_dir / "dashboard" / "calendar.svg")
        )
        cal_icon.set_pixel_size(16)
        self._quick_add_deadline_btn.set_child(cal_icon)
        self._quick_add_deadline_btn.connect("clicked", self._on_quick_add_deadline_clicked)
        note_row.append(self._quick_add_deadline_btn)
        box.append(note_row)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_top(6)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: self._quick_add_popover.popdown())
        btn_row.append(cancel_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_row.append(spacer)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", self._on_quick_add_submit)
        btn_row.append(add_btn)
        box.append(btn_row)

        self._quick_add_popover.set_child(box)

    def _refresh_quick_add_notes(self) -> None:
        notes = set(self.get_notes_fn())
        notes.discard("Inbox")
        sorted_notes = sorted(notes)

        model = self._quick_add_notes_model
        model.splice(0, model.get_n_items(), [])
        model.append("Inbox")
        for n in sorted_notes:
            model.append(n)
        self._quick_add_dropdown.set_selected(0)

    def open_quick_add_popover(self) -> None:
        self._refresh_quick_add_notes()
        self._quick_add_entry.set_text("")
        self._quick_add_entry.grab_focus()
        self._quick_add_deadline = None
        self._quick_add_popover.popup()

    def _on_quick_add_clicked(self, btn: Gtk.Button) -> None:
        self.open_quick_add_popover()

    def _on_quick_add_deadline_clicked(self, btn: Gtk.Button) -> None:
        picker = DeadlinePicker(self._on_quick_add_deadline_selected)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 0, 0, 1, 1
        picker.set_parent(btn)
        picker.set_pointing_to(rect)
        picker.popup()

    def _on_quick_add_deadline_selected(self, deadline: str | None) -> None:
        self._quick_add_deadline = deadline

    def _on_quick_add_submit(self, *args) -> None:
        text = self._quick_add_entry.get_text().strip()
        if not text or not self.on_quick_add:
            return

        selected = self._quick_add_dropdown.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        note_name = self._quick_add_notes_model.get_string(selected)

        self.on_quick_add(text, note_name, self._quick_add_deadline)
        self._quick_add_entry.set_text("")
        self._quick_add_deadline = None
        self._quick_add_popover.popdown()

    # Filter controls

    def on_filter_clicked(self, btn: Gtk.Button, filter_type: str) -> None:
        self.update_active_filter(filter_type)
        self.refresh_callback(filter_type)

    def update_active_filter(self, active_type: str) -> None:
        self.active_filter = active_type
        if self._filter_date is not None:
            self._filter_date = None
            self._update_filter_indicator()
        for f_type, btn in self.buttons.items():
            if f_type == active_type:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    def _activate_filter(self, filter_type: str) -> bool:
        if self.active_filter != filter_type:
            self.on_filter_clicked(None, filter_type)
        return True

    # Advanced filter controls

    def _on_advanced_filter_clicked(self, btn: Gtk.Button) -> None:
        self._completed_switch.set_active(self._show_completed)
        self._filter_popover.popup()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._search_text = entry.get_text().strip()
        self._apply_search_filter()
        self._update_filter_indicator()

    def _apply_search_filter(self) -> None:
        if not self._search_text:
            for rows in self._date_rows.values():
                for row in rows:
                    row.set_visible(True)
            return
        text = self._search_text.lower()
        for rows in self._date_rows.values():
            for row in rows:
                cb = getattr(row, "checkbox_data", {})
                match = text in cb.get("text", "").lower()
                row.set_visible(match)

    def _on_completed_toggled(self, switch: Gtk.Switch, *_) -> None:
        self._show_completed = switch.get_active()
        self._temp_show_completed = True
        self.refresh_callback(self.active_filter)
        self._update_filter_indicator()

    def _on_overdue_toggled(self, switch: Gtk.Switch, *_) -> None:
        self._show_overdue = switch.get_active()
        self.refresh_callback(self.active_filter)
        self._update_filter_indicator()

    def _on_calendar_date_selected(self, calendar: Gtk.Calendar) -> None:
        year = calendar.get_year()
        month = calendar.get_month() + 1
        day = calendar.get_day()
        self._filter_date = f"{year:04d}-{month:02d}-{day:02d}"
        self._update_filter_indicator()
        for btn in self.buttons.values():
            btn.remove_css_class("active")
        self._filter_popover.popdown()
        self.refresh_callback(self.active_filter)

    def _on_clear_date(self, btn: Gtk.Button) -> None:
        self._filter_date = None
        self._update_filter_indicator()
        self.update_active_filter(self.active_filter)
        self._filter_popover.popdown()
        self.refresh_callback(self.active_filter)

    def _has_active_filters(self) -> bool:
        return bool(
            self._search_text
            or self._show_overdue
            or self._filter_date is not None
            or self._temp_show_completed
        )

    def _update_filter_indicator(self) -> None:
        active = self._has_active_filters()
        self._filter_indicator.set_visible(active)
        if active:
            self.advanced_btn.add_css_class("has-active-filters")
        else:
            self.advanced_btn.remove_css_class("has-active-filters")

    def _collapse_all(self, *_) -> None:
        for date_key, rows in self._date_rows.items():
            self._collapsed.add(date_key)
            for task_row in rows:
                task_row.set_visible(False)

    def _expand_all(self, *_) -> None:
        self._collapsed.clear()
        for rows in self._date_rows.values():
            for task_row in rows:
                task_row.set_visible(True)

    # Population

    def populate(self, checkboxes: list[dict[str, Any]], filter_type: str) -> int:
        """Clear and repopulate the list for *filter_type*. Returns visible item count."""
        if not self._temp_show_completed:
            self._show_completed = self.get_show_completed()
        self._clear()
        filtered = self._filter(checkboxes, filter_type)

        # Extract overdue items so they render as a separate section at the top
        overdue: list[dict[str, Any]] = []
        if self._show_overdue:
            today_str = datetime.date.today().isoformat()
            remaining = []
            for cb in filtered:
                dl = cb.get("deadline", "")
                if dl and dl.split(" ")[0] < today_str and not cb["checked"]:
                    overdue.append(cb)
                else:
                    remaining.append(cb)
            filtered = remaining

        if not filtered and not overdue:
            self.on_empty(filter_type)
            return 0

        if filter_type in ("week", "all") and not self._filter_date:
            self._populate_grouped(filtered, overdue=overdue,
                                   include_misc=(filter_type == "all"),
                                   show_year=(filter_type == "all"))
        else:
            date_label = self._filter_date if self._filter_date else None
            self._populate_flat(filtered, overdue=overdue, date_label=date_label)

        return len(filtered) + len(overdue)

    def _clear(self) -> None:
        clear_listbox(self.dashboard_list)

    def _filter(
        self, checkboxes: list[dict[str, Any]], filter_type: str
    ) -> list[dict[str, Any]]:
        # Date override — bypass the date-range filter entirely
        if self._filter_date:
            if self._show_completed:
                pool = checkboxes
            else:
                pool = [cb for cb in checkboxes if not cb["checked"]]
            return [
                cb for cb in pool
                if (cb.get("deadline") or "").startswith(self._filter_date)
            ]

        today = datetime.date.today()
        _TODAY = today.isoformat()
        if self.get_start_week_on_sunday():
            _WEEK_START = (today - datetime.timedelta(days=(today.weekday() + 1) % 7)).isoformat()
            _WEEK_END = (today + datetime.timedelta(days=6 - (today.weekday() + 1) % 7)).isoformat()
        else:
            _WEEK_START = (today - datetime.timedelta(days=today.weekday())).isoformat()
            _WEEK_END = (today + datetime.timedelta(days=6 - today.weekday())).isoformat()

        if self._show_completed:
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

    def _populate_flat(
        self, items: list[dict[str, Any]], overdue: list[dict[str, Any]] | None = None,
        date_label: str | None = None,
    ) -> None:
        today_key = datetime.date.today().isoformat()
        self._date_rows.clear()
        self._header_rows.clear()

        if overdue:
            od_key = "overdue"
            od_completed = sum(1 for cb in overdue if cb["checked"])
            od_total = len(overdue)
            header_row = self._make_date_header(
                None, label="Overdue", progress=(od_completed, od_total),
                show_year=False, animate=False, collapsible=False,
                extra_css="overdue-header",
            )
            self.dashboard_list.append(header_row)
            self._header_rows[od_key] = header_row
            self._date_rows[od_key] = []
            for cb in sorted(overdue, key=lambda x: x.get("deadline") or ""):
                task_row = self._make_row(cb)
                self.dashboard_list.append(task_row)
                self._date_rows[od_key].append(task_row)

        completed = sum(1 for cb in items if cb["checked"])
        total = len(items)
        if total > 0:
            if date_label:
                try:
                    dt = datetime.datetime.strptime(date_label, "%Y-%m-%d")
                    header_label = dt.strftime("%A, %B %d")
                except ValueError:
                    header_label = date_label
            else:
                header_label = datetime.date.today().strftime("%A, %B %d")
            prev = self._prev_stats.get(today_key, (0, 0))
            animate = (completed, total) != prev
            header_row = self._make_date_header(
                None, label=header_label, progress=(completed, total),
                show_year=False, animate=animate, collapsible=False,
            )
            self.dashboard_list.append(header_row)
            self._header_rows[today_key] = header_row
            self._prev_stats[today_key] = (completed, total)
        for cb in sorted(items, key=lambda x: x.get("deadline") or ""):
            task_row = self._make_row(cb)
            self.dashboard_list.append(task_row)
            self._date_rows.setdefault(today_key, []).append(task_row)

    def _populate_grouped(
        self, items: list[dict[str, Any]], overdue: list[dict[str, Any]] | None = None,
        include_misc: bool = False,
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

        # Overdue section at the top
        if overdue:
            od_key = "overdue"
            od_completed = sum(1 for cb in overdue if cb["checked"])
            od_total = len(overdue)
            header_row = self._make_date_header(
                None, label="Overdue", progress=(od_completed, od_total),
                show_year=False, animate=False, collapsible=False,
                extra_css="overdue-header",
            )
            self.dashboard_list.append(header_row)
            self._header_rows[od_key] = header_row
            self._date_rows[od_key] = []
            for cb in sorted(overdue, key=lambda x: x.get("deadline") or ""):
                task_row = self._make_row(cb)
                self.dashboard_list.append(task_row)
                self._date_rows[od_key].append(task_row)

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
        collapsible: bool = True,
        extra_css: str | None = None,
    ) -> Gtk.ListBoxRow:
        if label is None:
            try:
                dt = datetime.datetime.strptime(date_str or "", "%Y-%m-%d")
                label = dt.strftime("%A, %B %d, %Y" if show_year else "%A, %B %d")
            except ValueError:
                label = date_str or ""

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(8)
        box.set_margin_bottom(5)

        ring_widget: ProgressRing | None = None
        if progress is not None and self.get_show_progress_rings():
            ring_widget = ProgressRing(size=24)
            ring_widget.set_progress(progress[0], progress[1], animate=animate)
            ring_widget.set_valign(Gtk.Align.CENTER)
            box.append(ring_widget)

        lbl = Gtk.Label(label=label, xalign=0)
        lbl.add_css_class("day-header")
        box.append(lbl)

        if collapsible:
            gesture = Gtk.GestureClick.new()
            gesture.connect("pressed", lambda *a, _d=date_str or "no_deadline": self._toggle_date(_d))
            box.add_controller(gesture)
            box.set_cursor_from_name("pointer")

        row = Gtk.ListBoxRow()
        row.set_child(box)
        row.set_selectable(False)
        row.add_css_class("date-header-row")
        if extra_css:
            row.add_css_class(extra_css)
        if ring_widget is not None:
            row._progress_ring = ring_widget
        return row

    def _toggle_date(self, date_key: str) -> None:
        if date_key in self._collapsed:
            self._collapsed.discard(date_key)
            for task_row in self._date_rows.get(date_key, []):
                task_row.set_visible(True)
        else:
            self._collapsed.add(date_key)
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

        time_label: Gtk.Label | None = None
        row._time_label = None
        if time_str:
            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("time-column")
            time_label.set_xalign(1)
            if cb["checked"]:
                time_label.add_css_class("task-completed")
            deadline_gesture = Gtk.GestureClick.new()
            deadline_gesture.connect(
                "pressed", lambda *a, _cb=cb: self.on_deadline_click(_cb, a[2], a[3])
            )
            time_label.add_controller(deadline_gesture)
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
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_strikethrough_new(True))
            text_label.set_attributes(attrs)
        box.append(text_label)
        row._text_label = text_label

        if time_label is not None:
            box.append(time_label)

        # Note chip — double-click navigates to the source note.
        chip = Gtk.Label(label=cb["note"])
        chip.add_css_class("note-chip")
        chip.add_css_class("dim-chip")
        chip.set_max_width_chars(8)
        chip.set_ellipsize(Pango.EllipsizeMode.END)
        chip_gesture = Gtk.GestureClick.new()
        chip_gesture.connect(
            "pressed", lambda *a, _cb=cb: self.on_row_click(a[0], a[1], a[2], a[3], _cb)
        )
        chip.add_controller(chip_gesture)
        box.append(chip)

        # Right-click snooze menu
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect("pressed", lambda *a, _cb=cb: self._show_snooze_popover(a[0], a[2], a[3], _cb))
        box.add_controller(right_click)

        row.set_child(box)
        return row

    # Snooze

    def _show_snooze_popover(self, gesture: Gtk.GestureClick, x: float, y: float, cb: dict[str, Any]) -> None:
        self._snooze_cb = cb

        today = datetime.date.today()
        deadline = cb.get("deadline") or ""
        time_part = deadline.split(" ")[1] if " " in deadline else ""

        def _dl(days: int) -> str:
            d = (today + datetime.timedelta(days=days)).isoformat()
            return f"{d} {time_part}" if time_part else d

        presets: list[tuple[str, str, str]] = [
            ("Tomorrow", "tomorrow", _dl(1)),
            ("Next Week", "next-week", _dl(7 - today.weekday())),
            ("Next Month", "next-month", _dl(30)),
        ]

        group = Gio.SimpleActionGroup()
        for _label, action_name, dl in presets:
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", lambda *a, _dl=dl: self._apply_snooze(self._snooze_cb, _dl))
            group.add_action(action)

        pick_action = Gio.SimpleAction.new("pick-date", None)
        pick_action.connect("activate", lambda *a: self._snooze_pick_date(self._snooze_cb))
        group.add_action(pick_action)

        gesture.get_widget().insert_action_group("snooze", group)

        preset_section = Gio.Menu()
        for _label, action_name, _dl in presets:
            preset_section.append_item(Gio.MenuItem.new(_label, f"snooze.{action_name}"))

        pick_section = Gio.Menu()
        pick_section.append_item(Gio.MenuItem.new("Pick Date", "snooze.pick-date"))

        menu = Gio.Menu()
        menu.append_section("Snooze", preset_section)
        menu.append_section(None, pick_section)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(gesture.get_widget())
        popover.popup()

    def _apply_snooze(self, cb: dict[str, Any], new_dl: str | None) -> None:
        if self.on_snooze:
            self.on_snooze(cb["note"], cb["line"], new_dl)

    def _snooze_pick_date(self, cb: dict[str, Any]) -> None:
        self.on_deadline_click(cb, 0, 0)

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
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_strikethrough_new(checked))
            text_label.set_attributes(attrs)

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
