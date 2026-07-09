from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from core.translations import tr
from ui.widgets.base import WidgetBase

logger = logging.getLogger(__name__)

CELL = 14
GAP = 2
LABEL_W = 28
PAD = 8


class HabitGraph(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self._dates: set[str] = set()
        self._habit_name = ""
        self._today = date.today()
        self._accent: Gdk.RGBA | None = None
        self._fg: Gdk.RGBA | None = None
        self._on_toggle: Any = None
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_size_request(-1, 200)
        self.set_draw_func(self._on_draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

    def set_data(self, dates: set[str], name: str, on_toggle: Any = None) -> None:
        self._dates = dates
        self._habit_name = name
        self._today = date.today()
        self._on_toggle = on_toggle
        self._accent = None
        self._fg = None
        self.queue_draw()

    def _end_sunday(self) -> date:
        ds = (self._today.weekday() + 1) % 7
        return self._today - timedelta(days=ds)

    def _col_count(self, width: int) -> int:
        avail = width - LABEL_W - 2 * PAD
        return max(1, int(avail // (CELL + GAP)))

    def _cell_date(self, col: int, row: int, cols: int) -> date:
        end_sun = self._end_sunday()
        start = end_sun - timedelta(weeks=cols - 1)
        return start + timedelta(weeks=col, days=row)

    def _resolve_colors(self, area: Gtk.DrawingArea) -> None:
        ctx = area.get_style_context()
        ok, c = ctx.lookup_color("accent_color")
        if ok:
            self._accent = c
        else:
            self._accent = Gdk.RGBA()
            self._accent.parse("#7aa2f7")
        ok, c = ctx.lookup_color("fg_color")
        if ok:
            self._fg = c
        else:
            self._fg = Gdk.RGBA()
            self._fg.parse("#a9b1d6")

    def _draw_rounded_rect(
        self, cr: cairo.Context, x: float, y: float, w: float, h: float, r: float
    ) -> None:
        cr.new_sub_path()
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
        cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()

    def _on_draw(
        self, area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int
    ) -> None:
        if width < 50 or height < 50:
            return

        if not self._habit_name:
            cr.select_font_face(
                "Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
            )
            cr.set_font_size(13)
            text = tr("Configure a habit in settings")
            ext = cr.text_extents(text)
            cx = (width - ext.width) / 2
            cy = (height - ext.height) / 2
            cr.move_to(cx, cy + ext.height)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
            cr.show_text(text)
            return

        self._resolve_colors(area)
        cols = self._col_count(width)

        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(14)
        ext = cr.text_extents(self._habit_name)
        cx = (width - ext.width) / 2
        cr.move_to(cx, PAD + ext.height)
        cr.set_source_rgba(self._accent.red, self._accent.green, self._accent.blue, 0.9)
        cr.show_text(self._habit_name)

        grid_y = PAD + 20 + 6
        cell_h = CELL + GAP
        total_grid_h = 7 * cell_h
        grid_x0 = PAD + LABEL_W

        day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9)
        for row in range(7):
            ly = grid_y + row * cell_h + cell_h / 2 + 3
            cr.move_to(PAD, ly)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
            cr.show_text(day_labels[row])

        has_any = bool(self._dates)

        for col in range(cols):
            for row in range(7):
                d = self._cell_date(col, row, cols)
                ds = d.isoformat()
                filled = ds in self._dates

                cx2 = grid_x0 + col * (CELL + GAP)
                cy2 = grid_y + row * (CELL + GAP)
                r = 2.5

                if filled:
                    cr.set_source_rgba(
                        self._accent.red,
                        self._accent.green,
                        self._accent.blue,
                        0.85,
                    )
                    self._draw_rounded_rect(cr, cx2, cy2, CELL, CELL, r)
                    cr.fill()
                else:
                    cr.set_source_rgba(
                        self._fg.red,
                        self._fg.green,
                        self._fg.blue,
                        0.06,
                    )
                    self._draw_rounded_rect(cr, cx2, cy2, CELL, CELL, r)
                    cr.fill()

                if d == self._today:
                    cr.set_source_rgba(
                        self._accent.red,
                        self._accent.green,
                        self._accent.blue,
                        1.0,
                    )
                    cr.set_line_width(1.5)
                    self._draw_rounded_rect(cr, cx2, cy2, CELL, CELL, r)
                    cr.stroke()

        footer_y = grid_y + total_grid_h + 6
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(10)
        hint = tr("Today") if has_any else tr("Click today to start tracking")
        ext = cr.text_extents(hint)
        cx = (width - ext.width) / 2
        cr.move_to(cx, footer_y + ext.height)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.5)
        cr.show_text(hint)

    def _on_click(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        if not self._habit_name or self._on_toggle is None:
            return
        alloc = self.get_allocation()
        if alloc.width <= 0:
            return

        cols = self._col_count(alloc.width)
        grid_y = PAD + 20 + 6
        grid_x0 = PAD + LABEL_W

        col = int((x - grid_x0) // (CELL + GAP))
        row = int((y - grid_y) // (CELL + GAP))
        if not (0 <= col < cols and 0 <= row < 7):
            return

        d = self._cell_date(col, row, cols)
        earliest = self._today - timedelta(days=7)
        if not (earliest <= d <= self._today):
            return

        self._on_toggle(d.isoformat())


class HabitTrackerWidget(WidgetBase):
    widget_type = "habits"
    widget_title = "Habit Tracker"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._timer_id: int | None = None
        self._fetched = False
        self._note_name = ""
        self._habit_name = ""
        self._dates: set[str] = set()
        self._build_ui()
        self.connect("map", lambda *a: self._on_map())

    def _build_ui(self) -> None:
        self._graph = HabitGraph()
        self._content.set_vexpand(True)
        self._content.append(self._graph)

    def get_config_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        label = Gtk.Label(label=tr("Habit name:"), xalign=0)
        box.append(label)

        self._cfg_entry = Gtk.Entry()
        self._cfg_entry.set_text(self.settings.get("name", ""))
        self._cfg_entry.set_placeholder_text(tr("e.g. Workout"))
        self._cfg_entry.set_hexpand(True)
        box.append(self._cfg_entry)

        return box

    def apply_config(self) -> None:
        name = self._cfg_entry.get_text().strip()
        if not name:
            return
        self.settings["name"] = name
        self._note_name = f"Habits/{name}"
        self._habit_name = name

        content = self.app.notes_manager.read_plain(self._note_name)
        if not content:
            content = f"# {name}\n"
            self.app.notes_manager.save_note(self._note_name, content)
            self.app.refresh_list()

        self._load_dates()
        self._update_graph()

    def _on_map(self) -> None:
        if self._fetched:
            return
        self._fetched = True

        name = self.settings.get("name", "")
        if not name:
            return
        self._note_name = f"Habits/{name}"
        self._habit_name = name

        content = self.app.notes_manager.read_plain(self._note_name)
        if not content:
            self.app.notes_manager.save_note(self._note_name, f"# {name}\n\n")
            self._load_dates()
            self._update_graph()
            self._ensure_timer()
            return

        self._load_dates()
        self._update_graph()
        self._ensure_timer()

    def _load_dates(self) -> None:
        if not self._note_name:
            self._dates = set()
            return
        content = self.app.notes_manager.read_plain(self._note_name)
        if not content:
            self._dates = set()
            return
        lines = content.split("\n")
        self._dates = {
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        }

    def _toggle_date(self, date_str: str) -> None:
        if not self._note_name:
            return
        content = self.app.notes_manager.read_plain(self._note_name)
        lines = content.split("\n")

        header = []
        dates: list[str] = []
        for line in lines:
            s = line.strip()
            if s.startswith("#") or not s:
                header.append(line)
            else:
                dates.append(s)

        if date_str in dates:
            dates.remove(date_str)
        else:
            dates.append(date_str)

        dates.sort(reverse=True)
        new_content = "\n".join(header + dates)
        self.app.notes_manager.save_note(self._note_name, new_content)

        if date_str in self._dates:
            self._dates.discard(date_str)
        else:
            self._dates.add(date_str)

        self._graph.set_data(self._dates, self._habit_name, self._toggle_date)

    def _update_graph(self) -> None:
        self._graph.set_data(self._dates, self._habit_name, self._toggle_date)

    def stop_periodic(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _ensure_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add(300_000, self._refresh)

    def _refresh(self) -> bool:
        if not self._note_name:
            return True
        self._load_dates()
        self._graph._today = date.today()
        self._graph.queue_draw()
        return True

    def update_periodic(self) -> None:
        if not self._note_name:
            name = self.settings.get("name", "")
            if name:
                self._note_name = f"Habits/{name}"
                self._habit_name = name
                content = self.app.notes_manager.read_plain(self._note_name)
                if not content:
                    self.app.notes_manager.save_note(self._note_name, f"# {name}\n\n")
                self._load_dates()
                self._graph.set_data(self._dates, self._habit_name, self._toggle_date)
        self._refresh()
        self._ensure_timer()
