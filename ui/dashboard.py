"""Widget grid with right-click menus, drag-to-reposition, and resize grip."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk

from core.translations import tr
from ui.widget_picker import WidgetPicker
from ui.widgets import WidgetBase, create_widget, get_widget_types

if TYPE_CHECKING:
    from main import TokyoNotes

_DEFAULT_WIDTHS: dict[str, int] = {
    "tasks": 4,
    "weather": 2,
    "rss": 2,
    "api": 2,
    "worldtime": 2,
    "notestats": 2,
    "habits": 2,
}

_DEFAULT_HEIGHTS: dict[str, int] = {
    "tasks": 4,
}

_HANDLE_SIZE = 20


class Dashboard(Gtk.Box):
    """Scrollable widget grid with right-click menus, drag, and resize grip."""

    def __init__(self, app: TokyoNotes) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app
        self._grid_cols: int = app.cfg.get("grid_cols", 4)
        self._widget_infos: list[dict[str, Any]] = []
        self._repos_data: dict[str, Any] | None = None
        self._resize_data: dict[str, Any] | None = None

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._grid = Gtk.Grid()
        self._grid.set_column_spacing(12)
        self._grid.set_row_spacing(12)
        self._grid.set_margin_top(12)
        self._grid.set_margin_bottom(12)
        self._grid.set_margin_start(12)
        self._grid.set_margin_end(12)
        self._grid.set_hexpand(True)
        self._grid.set_vexpand(True)
        layout = self._grid.get_layout_manager()
        if hasattr(layout, "set_column_homogeneous"):
            layout.set_column_homogeneous(True)
        self._scrolled.set_child(self._grid)
        self.append(self._scrolled)

        # Right-click on the scrolled window background → Add Widget
        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_empty_right_click)
        self._scrolled.add_controller(right_click)

        self._load_widgets()

    # ── Widget lifecycle ──

    def _load_widgets(self) -> None:
        configs = self.app.cfg.get("widgets", [])
        if not configs:
            configs = [{"type": "tasks", "id": "tasks-1", "settings": {}}]
        self._rebuild_grid(configs)

    def _rebuild_grid(self, configs: list[dict[str, Any]] | None = None) -> None:
        child = self._grid.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._grid.remove(child)
            child = nxt
        self._widget_infos.clear()

        if configs is None:
            configs = self.app.cfg.get("widgets", [])
            if not configs:
                configs = [{"type": "tasks", "id": "tasks-1", "settings": {}}]

        configs = self._migrate_configs(configs)

        for cfg in configs:
            try:
                w = create_widget(
                    cfg["type"], cfg.get("id", ""), cfg.get("settings"), app=self.app
                )
                self._setup_widget_controls(w)

                info: dict[str, Any] = {
                    "widget": w,
                    "row": cfg.get("row", 0),
                    "col": cfg.get("col", 0),
                    "width": cfg.get("width", 2),
                    "height": cfg.get("height", _DEFAULT_HEIGHTS.get(cfg["type"], 1)),
                }
                self._widget_infos.append(info)

                w.set_halign(Gtk.Align.FILL)
                w.set_hexpand(True)
                w.set_valign(Gtk.Align.FILL)
                if info["height"] > 1:
                    w.set_vexpand(True)
                self._grid.attach(
                    w, info["col"], info["row"], info["width"], info["height"]
                )
            except Exception:
                continue

        tasks_w = self.get_widget("tasks")
        if tasks_w is not None:
            tasks_w._refresh("all")

    def _setup_widget_controls(self, widget: WidgetBase) -> None:
        # Single drag gesture: reposition on body, resize if started near corner
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin, widget)
        drag.connect("drag-end", self._on_drag_end, widget)
        widget.add_controller(drag)

        # Right-click context menu
        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_widget_right_click, widget)
        widget.add_controller(right_click)

        # Resize grip handle (visual only — gesture is on the widget body)
        handle = Gtk.DrawingArea()
        handle.set_size_request(_HANDLE_SIZE, _HANDLE_SIZE)
        handle.set_halign(Gtk.Align.END)
        handle.set_valign(Gtk.Align.END)
        handle.add_css_class("widget-resize-handle")
        handle.set_draw_func(self._draw_grip)
        handle.set_visible(False)
        widget.add_overlay(handle)

        # Motion controller to show handle on corner hover
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_widget_motion, widget, handle)
        motion.connect("leave", self._on_widget_motion_leave, widget, handle)
        widget.add_controller(motion)

    def _migrate_configs(self, configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        has_position = any("row" in c for c in configs)
        if has_position:
            return configs

        col, row = 0, 0
        for cfg in configs:
            dw = _DEFAULT_WIDTHS.get(cfg.get("type", ""), 2)
            cfg["row"] = row
            cfg["col"] = col
            cfg["width"] = dw
            cfg["height"] = _DEFAULT_HEIGHTS.get(cfg.get("type", ""), 1)
            col += dw
            if col >= self._grid_cols:
                col = 0
                row += 1
        return configs

    def _context_remove_widget(
        self, widget: WidgetBase, menu_popover: Gtk.Popover
    ) -> None:
        menu_popover.popdown()
        GLib.idle_add(lambda w=widget: self._remove_widget(w))

    def _remove_widget(self, widget: WidgetBase) -> None:
        info = self._find_info(widget)
        if info is None:
            return
        self._grid.remove(widget)
        self._widget_infos.remove(info)
        snapshot = [
            (o["row"], o["col"], o["width"], o["height"]) for o in self._widget_infos
        ]
        self._compact_layout()
        moved = [
            o
            for o, (r, c, w, h) in zip(self._widget_infos, snapshot)
            if o["row"] != r or o["col"] != c or o["width"] != w or o["height"] != h
        ]
        if moved:
            self._relocate_widgets(moved)
        self._save_config()

    # ── Right-click context menus ──

    def _on_widget_right_click(
        self,
        _gesture: Gtk.GestureClick,
        _n: int,
        x: float,
        y: float,
        widget: WidgetBase,
    ) -> None:
        self._show_widget_context_menu(widget, x, y)

    def _on_empty_right_click(
        self, _gesture: Gtk.GestureClick, _n: int, x: float, y: float
    ) -> None:
        self._show_add_popover(x, y)

    def _show_widget_context_menu(self, widget: WidgetBase, x: float, y: float) -> None:
        count = 0
        if widget.get_config_widget() is not None:
            count += 1
        if widget.widget_type != "tasks":
            count += 1
        if count == 0:
            return

        popover = Gtk.Popover()
        popover.set_parent(widget)
        popover.set_autohide(True)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x + 2), int(y + 2), 1, 1
        popover.set_pointing_to(rect)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        if widget.get_config_widget() is not None:
            btn = Gtk.Button(label=tr("Configure"))
            btn.set_halign(Gtk.Align.FILL)
            btn.set_hexpand(True)
            btn.add_css_class("flat")
            btn.add_css_class("context-menu-item")
            btn.connect("clicked", lambda *a: self._context_configure(widget, popover))
            box.append(btn)

        if widget.widget_type != "tasks":
            btn = Gtk.Button(label=tr("Remove"))
            btn.set_halign(Gtk.Align.FILL)
            btn.set_hexpand(True)
            btn.add_css_class("flat")
            btn.add_css_class("context-menu-item")
            btn.connect(
                "clicked", lambda *a: self._context_remove_widget(widget, popover)
            )
            box.append(btn)

        popover.set_child(box)
        popover.popup()

    def _context_configure(self, widget: WidgetBase, menu_popover: Gtk.Popover) -> None:
        menu_popover.popdown()
        GLib.idle_add(lambda w=widget: self._show_configure_popover(w))

    def _show_configure_popover(self, widget: WidgetBase) -> None:
        config_w = widget.get_config_widget()
        if config_w is None:
            return

        popover = Gtk.Popover()
        popover.set_parent(widget)
        popover.set_autohide(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        header = Gtk.Label(
            label=tr("Configure {title}").format(title=widget.widget_title)
        )
        header.add_css_class("heading")
        box.append(header)
        box.append(config_w)

        close_btn = Gtk.Button(label=tr("Close"))
        close_btn.connect("clicked", lambda *a: popover.popdown())
        box.append(close_btn)

        save_btn = Gtk.Button(label=tr("Save"))
        save_btn.add_css_class("suggested-action")
        save_btn.connect(
            "clicked", lambda *a: self._save_widget_config(widget, popover)
        )
        box.append(save_btn)

        popover.set_child(box)
        GLib.idle_add(popover.popup)

    def _save_widget_config(self, widget: WidgetBase, popover: Gtk.Popover) -> None:
        popover.popdown()
        widget.apply_config()
        self._save_config()

    # ── Add widget ──

    def _show_add_popover(self, x: float | None = None, y: float | None = None) -> None:
        has_tasks = any(
            info["widget"].widget_type == "tasks" for info in self._widget_infos
        )
        types = {
            wtype: cls
            for wtype, cls in get_widget_types().items()
            if not (wtype == "tasks" and has_tasks)
        }
        picker = WidgetPicker(
            widget_types=types,
            on_selected=lambda wtype: self._add_new_widget(wtype),
        )
        picker.set_parent(self._scrolled)
        if x is not None and y is not None:
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            picker.set_pointing_to(rect)
        picker.popup()

    def _add_new_widget(self, widget_type: str) -> None:
        import uuid

        wid = f"{widget_type}-{uuid.uuid4().hex[:8]}"
        try:
            w = create_widget(widget_type, wid, {}, app=self.app)
            self._setup_widget_controls(w)

            row, col = self._find_first_free()
            dw = _DEFAULT_WIDTHS.get(widget_type, 2)
            if col + dw > self._grid_cols:
                col = self._grid_cols - dw

            dh = _DEFAULT_HEIGHTS.get(widget_type, 1)
            info: dict[str, Any] = {
                "widget": w,
                "row": row,
                "col": col,
                "width": dw,
                "height": dh,
            }
            self._widget_infos.append(info)

            w.set_halign(Gtk.Align.FILL)
            w.set_hexpand(True)
            w.set_valign(Gtk.Align.FILL)
            if dh > 1:
                w.set_vexpand(True)
            self._grid.attach(w, col, row, dw, dh)

            self._save_config()
        except Exception:
            pass

    # ── Drag gesture (reposition body / resize corner) ──

    def _draw_grip(
        self, area: Gtk.DrawingArea, cr: Gdk.CairoContext, width: int, height: int
    ) -> None:
        if width <= 0 or height <= 0:
            return
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.5)
        spacing = 5
        dot_r = 1.5
        ox = (width - (3 * spacing - 2)) / 2.0
        oy = (height - (3 * spacing - 2)) / 2.0
        for row in range(3):
            for col in range(3):
                cx = ox + col * spacing
                cy = oy + row * spacing
                cr.arc(cx, cy, dot_r, 0, 2 * math.pi)
                cr.fill()

    def _on_widget_motion(
        self,
        controller: Gtk.EventControllerMotion,
        x: float,
        y: float,
        widget: WidgetBase,
        handle: Gtk.DrawingArea,
    ) -> None:
        alloc = widget.get_allocation()
        if not alloc or alloc.width <= 0 or alloc.height <= 0:
            return
        margin = _HANDLE_SIZE + 10
        show = x >= alloc.width - margin and y >= alloc.height - margin
        if show and not handle.get_visible():
            handle.set_visible(True)
            self._grid.set_cursor_from_name("se-resize")
        elif not show and handle.get_visible():
            handle.set_visible(False)
            self._grid.set_cursor_from_name(None)

    def _on_widget_motion_leave(
        self,
        controller: Gtk.EventControllerMotion,
        widget: WidgetBase,
        handle: Gtk.DrawingArea,
    ) -> None:
        handle.set_visible(False)
        self._grid.set_cursor_from_name(None)

    def _on_drag_begin(
        self,
        gesture: Gtk.GestureDrag,
        start_x: float,
        start_y: float,
        widget: WidgetBase,
    ) -> None:
        info = self._find_info(widget)
        if info is None:
            return
        alloc = widget.get_allocation()
        margin = _HANDLE_SIZE + 10
        near_corner = (
            alloc is not None
            and alloc.width > 0
            and alloc.height > 0
            and start_x >= alloc.width - margin
            and start_y >= alloc.height - margin
        )
        if near_corner:
            grid_w = self._grid.get_width()
            self._resize_data = {
                "orig_w": info["width"],
                "orig_h": info["height"],
                "col_px": grid_w / self._grid_cols if grid_w > 0 else 1,
                "row_px": (alloc.height / info["height"])
                if info["height"] > 0 and alloc.height > 0
                else 60,
            }
        else:
            self._repos_data = {
                "orig_row": info["row"],
                "orig_col": info["col"],
            }

    def _on_drag_end(
        self,
        gesture: Gtk.GestureDrag,
        offset_x: float,
        offset_y: float,
        widget: WidgetBase,
    ) -> None:
        if self._resize_data is not None:
            self._finish_resize(widget, offset_x, offset_y)
        elif self._repos_data is not None:
            self._finish_reposition(widget, offset_x, offset_y)

    def _finish_reposition(
        self, widget: WidgetBase, offset_x: float, offset_y: float
    ) -> None:
        data = self._repos_data
        self._repos_data = None
        if data is None:
            return
        info = self._find_info(widget)
        if info is None:
            return
        grid_w = self._grid.get_width()
        col_px = grid_w / self._grid_cols if grid_w > 0 else 1
        alloc = widget.get_allocation()
        row_px = (
            (alloc.height / info["height"])
            if info["height"] > 0 and alloc and alloc.height > 0
            else 60
        )
        dc = round(offset_x / col_px)
        dr = round(offset_y / row_px)
        new_col = max(0, min(self._grid_cols - info["width"], data["orig_col"] + dc))
        new_row = max(0, data["orig_row"] + dr)

        overlapping = self._overlapping_widgets(
            new_row, new_col, info["width"], info["height"], skip=info
        )
        moved: list[dict[str, Any]] = []
        if not overlapping:
            info["row"] = new_row
            info["col"] = new_col
            moved.append(info)
        elif len(overlapping) == 1:
            target = overlapping[0]
            if self._can_place_at(
                data["orig_row"],
                data["orig_col"],
                target["width"],
                target["height"],
                skip=target,
            ):
                info["row"], target["row"] = target["row"], info["row"]
                info["col"], target["col"] = target["col"], info["col"]
                moved.extend([info, target])
        self._save_config()
        if moved:
            GLib.idle_add(lambda: self._relocate_widgets(list(moved)))

    def _finish_resize(
        self, widget: WidgetBase, offset_x: float, offset_y: float
    ) -> None:
        data = self._resize_data
        self._resize_data = None
        if data is None:
            return
        info = self._find_info(widget)
        if info is None:
            return
        desired_w = max(
            1,
            min(
                self._grid_cols - info["col"],
                round(data["orig_w"] + offset_x / data["col_px"]),
            ),
        )
        desired_h = max(1, round(data["orig_h"] + offset_y / data["row_px"]))
        snapshot = [
            (o["row"], o["col"], o["width"], o["height"]) for o in self._widget_infos
        ]
        info["width"] = desired_w
        info["height"] = desired_h
        self._compact_layout()
        self._save_config()
        moved = [
            o
            for o, (r, c, w, h) in zip(self._widget_infos, snapshot)
            if o["row"] != r or o["col"] != c or o["width"] != w or o["height"] != h
        ]
        GLib.idle_add(lambda: self._relocate_widgets(moved or [info]))

    def _relocate_widgets(self, infos: list[dict[str, Any]]) -> None:
        for info in infos:
            w = info["widget"]
            self._grid.remove(w)
            self._grid.attach(
                w, info["col"], info["row"], info["width"], info["height"]
            )

    # ── Occupancy helpers ──

    def _occupied_cells(self) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        for info in self._widget_infos:
            for r in range(info["row"], info["row"] + info["height"]):
                for c in range(info["col"], info["col"] + info["width"]):
                    cells.add((r, c))
        return cells

    def _find_first_free(self) -> tuple[int, int]:
        occ = self._occupied_cells()
        row, col = 0, 0
        while (row, col) in occ:
            col += 1
            if col >= self._grid_cols:
                col = 0
                row += 1
        return row, col

    @staticmethod
    def _rects_intersect(
        r1: int,
        c1: int,
        w1: int,
        h1: int,
        r2: int,
        c2: int,
        w2: int,
        h2: int,
    ) -> bool:
        return r1 < r2 + h2 and r1 + h1 > r2 and c1 < c2 + w2 and c1 + w1 > c2

    def _overlapping_widgets(
        self,
        row: int,
        col: int,
        width: int,
        height: int,
        skip: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for info in self._widget_infos:
            if info is skip:
                continue
            if self._rects_intersect(
                row,
                col,
                width,
                height,
                info["row"],
                info["col"],
                info["width"],
                info["height"],
            ):
                result.append(info)
        return result

    def _can_place_at(
        self,
        row: int,
        col: int,
        width: int,
        height: int,
        skip: dict[str, Any] | None = None,
    ) -> bool:
        if col + width > self._grid_cols:
            return False
        return not self._overlapping_widgets(row, col, width, height, skip)

    def _compact_layout(self) -> None:
        sorted_infos = sorted(self._widget_infos, key=lambda x: (x["row"], x["col"]))
        placed: list[dict[str, Any]] = []
        for info in sorted_infos:
            max_row = info["row"]
            for p in placed:
                if self._rects_intersect(
                    info["row"],
                    info["col"],
                    info["width"],
                    info["height"],
                    p["row"],
                    p["col"],
                    p["width"],
                    p["height"],
                ):
                    max_row = max(max_row, p["row"] + p["height"])
            info["row"] = max_row
            placed.append(info)

    def _find_widget_at(
        self, row: int, col: int, skip: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        for info in self._widget_infos:
            if info is skip:
                continue
            if (
                info["row"] <= row < info["row"] + info["height"]
                and info["col"] <= col < info["col"] + info["width"]
            ):
                return info
        return None

    # ── Config persistence ──

    def _save_config(self) -> None:
        configs: list[dict[str, Any]] = []
        for info in self._widget_infos:
            cfg = info["widget"].to_dict()
            cfg["row"] = info["row"]
            cfg["col"] = info["col"]
            cfg["width"] = info["width"]
            cfg["height"] = info["height"]
            configs.append(cfg)
        self.app.cfg.set("widgets", configs)
        self.app.cfg.flush_immediate()

    def get_widget(self, widget_type: str) -> WidgetBase | None:
        for info in self._widget_infos:
            if info["widget"].widget_type == widget_type:
                return info["widget"]
        return None

    # ── Lookup helpers ──

    def _find_info(self, widget: WidgetBase) -> dict[str, Any] | None:
        for info in self._widget_infos:
            if info["widget"] is widget:
                return info
        return None

    # ── Periodic refresh ──

    def on_show(self) -> None:
        for info in self._widget_infos:
            info["widget"].update_periodic()

    def on_hide(self) -> None:
        for info in self._widget_infos:
            info["widget"].stop_periodic()
