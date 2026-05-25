"""Graph view — zoomable, pannable force-directed note relationship graph."""

from __future__ import annotations

import math
import random
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

_REPULSION = 8_000.0  # node-node repulsion constant
_ATTRACTION = 0.06  # edge spring constant
_DAMPING = 0.85  # velocity damping per step
_STEPS = 120  # simulation steps before display

_BASE_RADIUS = 6.0
_MAX_RADIUS = 20.0


class GraphView(Gtk.Box):
    """Cairo-rendered graph with zoom, pan, force-directed layout, and click preview."""

    def __init__(
        self,
        graph_data: dict,
        on_node_clicked: Callable[[str], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._adjacency: dict[str, list[str]] = graph_data.get("adjacency", graph_data)
        self._degrees: dict[str, int] = graph_data.get("degrees", {})
        self.on_node_clicked = on_node_clicked
        self.nodes: list[str] = list(self._adjacency.keys())

        # Positions in graph-space (not canvas-space)
        self._pos: dict[str, list[float]] = {}
        self._vel: dict[str, list[float]] = {}

        # Viewport state
        self._scale: float = 1.0
        self._offset: list[float] = [0.0, 0.0]
        self._drag_start: tuple[float, float] | None = None
        self._offset_start: list[float] = [0.0, 0.0]
        self._fit_anim_id: int = 0
        self._first_layout: bool = True

        # Hovered / selected node for preview panel
        self._hovered: str | None = None
        self._connected: set[str] = set()
        self._pending_navigate: int = 0  # GLib source id
        self._sim_id: int = 0  # GLib source id for force simulation
        self._cached_colors: dict[str, Gdk.RGBA] = {}

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_halign(Gtk.Align.CENTER)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(4)

        self.fit_btn = Gtk.Button(label="Fit")
        self.fit_btn.add_css_class("pill")
        self.fit_btn.connect("clicked", self._on_fit_clicked)
        toolbar.append(self.fit_btn)

        hint = Gtk.Label(label="Scroll to zoom · Drag to pan · Click a node to open")
        hint.add_css_class("dim-label")
        toolbar.append(hint)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_draw_func(self._on_draw)
        self.canvas.set_vexpand(True)
        self.canvas.set_hexpand(True)
        self._label_layout = None
        self._realized = False
        self.canvas.connect("realize", self._on_realize)
        self.canvas.connect("unrealize", self._on_unrealize)

        # Scroll -> zoom
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.canvas.add_controller(scroll)

        # Motion -> hover highlighting
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_motion_leave)
        self.canvas.add_controller(motion)

        # Click -> navigate (with short preview delay)
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_press)
        self.canvas.add_controller(click)

        # Drag -> pan
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        self.canvas.add_controller(drag)

        self.append(toolbar)
        self.append(self.canvas)

        self._layout_nodes()

    # Data

    def update_data(self, new_data: dict) -> None:
        if self._sim_id:
            GLib.source_remove(self._sim_id)
            self._sim_id = 0
        self._adjacency = new_data.get("adjacency", new_data)
        self._degrees = new_data.get("degrees", {})
        self.nodes = list(self._adjacency.keys())
        self._layout_nodes()
        self.canvas.queue_draw()

    # Node helpers

    def _node_radius(self, node: str) -> float:
        degree = self._degrees.get(node, 0)
        return _BASE_RADIUS + min(degree * 2.0, _MAX_RADIUS - _BASE_RADIUS)

    # Force-directed layout

    def _layout_nodes(self) -> None:
        """Compute a stable force-directed layout."""
        if not self.nodes:
            self._pos = {}
            return

        rng = random.Random(42)
        n = len(self.nodes)
        for i, node in enumerate(self.nodes):
            angle = 2 * math.pi * i / n
            self._pos[node] = [300 * math.cos(angle), 300 * math.sin(angle)]
            self._vel[node] = [rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1)]

        self._run_simulation(0)

    def _run_simulation(self, step: int) -> None:
        """Run one chunk of the force simulation (10 steps per idle tick)."""
        if not getattr(self, "_realized", False):
            return  # widget unrealized/destroyed
        chunk = 10
        end = min(step + chunk, _STEPS)
        for _ in range(step, end):
            self._simulation_step()
        if end < _STEPS:
            self._sim_id = GLib.idle_add(self._run_simulation, end)
        else:
            self._sim_id = 0
            self._centre_layout()

    def _on_realize(self, widget: Gtk.Widget) -> None:
        self._realized = True

    def _on_unrealize(self, widget: Gtk.Widget) -> None:
        self._realized = False
        if self._pending_navigate:
            GLib.source_remove(self._pending_navigate)
            self._pending_navigate = 0
        if self._sim_id:
            GLib.source_remove(self._sim_id)
            self._sim_id = 0
        if self._fit_anim_id:
            GLib.source_remove(self._fit_anim_id)
            self._fit_anim_id = 0

    def _simulation_step(self) -> None:
        forces: dict[str, list[float]] = {n: [0.0, 0.0] for n in self.nodes}

        for i, a in enumerate(self.nodes):
            for b in self.nodes[i + 1 :]:
                dx = self._pos[a][0] - self._pos[b][0]
                dy = self._pos[a][1] - self._pos[b][1]
                dist2 = max(dx * dx + dy * dy, 1.0)
                dist = math.sqrt(dist2)
                f = _REPULSION / dist2
                fx, fy = f * dx / dist, f * dy / dist
                forces[a][0] += fx
                forces[a][1] += fy
                forces[b][0] -= fx
                forces[b][1] -= fy

        for node, targets in self._adjacency.items():
            if node not in self._pos:
                continue
            for t in targets:
                if t not in self._pos:
                    continue
                dx = self._pos[t][0] - self._pos[node][0]
                dy = self._pos[t][1] - self._pos[node][1]
                forces[node][0] += _ATTRACTION * dx
                forces[node][1] += _ATTRACTION * dy
                forces[t][0] -= _ATTRACTION * dx
                forces[t][1] -= _ATTRACTION * dy

        for node in self.nodes:
            self._vel[node][0] = (self._vel[node][0] + forces[node][0]) * _DAMPING
            self._vel[node][1] = (self._vel[node][1] + forces[node][1]) * _DAMPING
            self._pos[node][0] += self._vel[node][0]
            self._pos[node][1] += self._vel[node][1]

    def _centre_layout(self) -> None:
        if self.nodes:
            cx = sum(self._pos[n][0] for n in self.nodes) / len(self.nodes)
            cy = sum(self._pos[n][1] for n in self.nodes) / len(self.nodes)
            for n in self.nodes:
                self._pos[n][0] -= cx
                self._pos[n][1] -= cy
        self.canvas.queue_draw()
        if self._first_layout:
            self._first_layout = False
            GLib.idle_add(self._on_fit_clicked, None)

    # Fit-to-view

    def _on_fit_clicked(self, btn: Gtk.Button | None = None) -> None:
        if not self.nodes or not self._pos:
            return
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        if w <= 0 or h <= 0:
            return

        min_x = min(self._pos[n][0] for n in self.nodes)
        max_x = max(self._pos[n][0] for n in self.nodes)
        min_y = min(self._pos[n][1] for n in self.nodes)
        max_y = max(self._pos[n][1] for n in self.nodes)

        padding = 60.0
        graph_w = max_x - min_x + padding * 2
        graph_h = max_y - min_y + padding * 2
        target_scale = min(w / graph_w, h / graph_h, 3.0)

        target_offset_x = -(min_x + max_x) / 2
        target_offset_y = -(min_y + max_y) / 2

        start_scale = self._scale
        start_offset = list(self._offset)
        start_time = None

        def animate(widget, frame_clock):
            nonlocal start_time
            if start_time is None:
                start_time = frame_clock.get_frame_time() / 1_000_000.0
            elapsed = frame_clock.get_frame_time() / 1_000_000.0 - start_time
            duration = 0.3
            if elapsed >= duration:
                self._scale = target_scale
                self._offset = [target_offset_x, target_offset_y]
                self._fit_anim_id = 0
                self.canvas.queue_draw()
                return False
            t = elapsed / duration
            eased = 1.0 - (1.0 - t) ** 3
            self._scale = start_scale + (target_scale - start_scale) * eased
            self._offset[0] = (
                start_offset[0] + (target_offset_x - start_offset[0]) * eased
            )
            self._offset[1] = (
                start_offset[1] + (target_offset_y - start_offset[1]) * eased
            )
            self.canvas.queue_draw()
            return True

        if self._fit_anim_id:
            GLib.source_remove(self._fit_anim_id)
        self._fit_anim_id = self.canvas.add_tick_callback(animate)

    # Coordinate helpers

    def _graph_to_canvas(
        self, gx: float, gy: float, w: int, h: int
    ) -> tuple[float, float]:
        return (
            gx * self._scale + w / 2 + self._offset[0],
            gy * self._scale + h / 2 + self._offset[1],
        )

    def _canvas_to_graph(
        self, cx: float, cy: float, w: int, h: int
    ) -> tuple[float, float]:
        return (
            (cx - w / 2 - self._offset[0]) / self._scale,
            (cy - h / 2 - self._offset[1]) / self._scale,
        )

    # Drawing

    def _on_draw(self, area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        if not self.nodes:
            return

        ctx = area.get_style_context()

        def _get_color(name: str, fallback: str) -> Gdk.RGBA:
            if name not in self._cached_colors:
                ok, c = ctx.lookup_color(name)
                if not ok:
                    c = Gdk.RGBA()
                    c.parse(fallback)
                self._cached_colors[name] = c
            return self._cached_colors[name]

        accent = _get_color("accent_color", "#7aa2f7")
        fg = _get_color("fg_color", "#a9b1d6")
        sel = _get_color("selection_color", "#364a82")

        positions = {
            n: self._graph_to_canvas(self._pos[n][0], self._pos[n][1], width, height)
            for n in self.nodes
            if n in self._pos
        }

        if self._label_layout is None:
            self._label_layout = PangoCairo.create_layout(cr)
            self._label_layout.set_font_description(
                Pango.FontDescription.from_string("Sans 9")
            )
        layout = self._label_layout

        # Compute label bounding boxes for collision detection
        label_bboxes: dict[str, tuple[float, float, float, float]] = {}
        node_radii: dict[str, float] = {}
        sorted_nodes = sorted(
            self.nodes, key=lambda n: self._degrees.get(n, 0), reverse=True
        )
        for node in sorted_nodes:
            if node not in positions:
                continue
            x, y = positions[node]
            r = self._node_radius(node)
            node_radii[node] = r
            layout.set_text(node, -1)
            lw, lh = layout.get_pixel_size()
            lx = x + r + 4
            ly = y - lh / 2
            label_bboxes[node] = (lx, ly, lw, lh)

        # Resolve collisions: try alternative positions
        label_offsets: dict[str, tuple[float, float]] = {}
        label_visible: dict[str, bool] = {}
        for node in sorted_nodes:
            if node not in label_bboxes:
                continue
            x, y = positions[node]
            r = node_radii[node]
            lx, ly, lw, lh = label_bboxes[node]
            ox, oy = 0.0, 0.0
            visible = True

            if self._scale < 0.5 and self._degrees.get(node, 0) < 2:
                visible = False
            else:
                candidates = [
                    (0.0, 0.0),  # right (default)
                    (0.0, -lh - r - 4),  # above
                    (0.0, lh + r + 4),  # below
                    (-lw - r - 8, 0.0),  # left
                ]
                for dx, dy in candidates:
                    test_lx = lx + dx
                    test_ly = ly + dy
                    collision = False
                    for other in sorted_nodes:
                        if other == node or other not in label_bboxes:
                            continue
                        other_x, other_y = positions[other]
                        other_r = node_radii.get(other, 10)
                        # Check against other node's circle
                        cx = test_lx + lw / 2
                        cy = test_ly + lh / 2
                        if math.hypot(cx - other_x, cy - other_y) < other_r + 4:
                            collision = True
                            break
                        # Check against other label's bbox
                        if other in label_offsets:
                            odx, ody = label_offsets[other]
                            olx, oly, olw, olh = label_bboxes[other]
                            olx += odx
                            oly += ody
                            if not (
                                test_lx + lw < olx
                                or olx + olw < test_lx
                                or test_ly + lh < oly
                                or oly + olh < test_ly
                            ):
                                collision = True
                                break
                    if not collision:
                        ox, oy = dx, dy
                        break
                else:
                    visible = False

            label_offsets[node] = (ox, oy)
            label_visible[node] = visible

        has_hover = self._hovered is not None

        # Edges
        for node, targets in self._adjacency.items():
            if node not in positions:
                continue
            x1, y1 = positions[node]
            for t in targets:
                if t not in positions:
                    continue
                x2, y2 = positions[t]

                if has_hover:
                    is_connected = node == self._hovered or t == self._hovered
                    if is_connected:
                        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.6)
                        cr.set_line_width(2.0)
                    else:
                        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.08)
                        cr.set_line_width(1.0)
                else:
                    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.25)
                    cr.set_line_width(1.5)

                cr.move_to(x1, y1)
                cr.line_to(x2, y2)
                cr.stroke()

                # Arrowhead
                r_t = self._node_radius(t)
                angle = math.atan2(y2 - y1, x2 - x1)
                ax = x2 - (r_t + 2) * math.cos(angle)
                ay = y2 - (r_t + 2) * math.sin(angle)
                hl, ha = 8, math.pi / 6
                cr.move_to(ax, ay)
                cr.line_to(
                    ax - hl * math.cos(angle - ha), ay - hl * math.sin(angle - ha)
                )
                cr.move_to(ax, ay)
                cr.line_to(
                    ax - hl * math.cos(angle + ha), ay - hl * math.sin(angle + ha)
                )
                cr.stroke()

        # Nodes
        for node, (x, y) in positions.items():
            is_hovered = node == self._hovered
            r = self._node_radius(node)

            if has_hover:
                if is_hovered:
                    pass  # full opacity below
                elif node in self._connected:
                    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
                else:
                    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.2)
            else:
                cr.set_source_rgb(accent.red, accent.green, accent.blue)

            if is_hovered:
                cr.set_source_rgba(sel.red, sel.green, sel.blue, 0.4)
                cr.arc(x, y, r + 4, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgb(accent.red, accent.green, accent.blue)

            cr.arc(x, y, r * (1.3 if is_hovered else 1.0), 0, 2 * math.pi)
            cr.fill()

            label_alpha = 0.0
            if label_visible.get(node, False):
                if has_hover and node not in self._connected and not is_hovered:
                    label_alpha = 0.2
                else:
                    label_alpha = 1.0
            if label_alpha > 0:
                ox, oy = label_offsets.get(node, (0.0, 0.0))
                cr.set_source_rgba(fg.red, fg.green, fg.blue, label_alpha)
                layout.set_text(node, -1)
                cr.move_to(x + r + 4 + ox, y - 6 + oy)
                PangoCairo.show_layout(cr, layout)

    # Interaction

    def _node_at(self, cx: float, cy: float) -> str | None:
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        for node in self.nodes:
            if node not in self._pos:
                continue
            nx, ny = self._graph_to_canvas(self._pos[node][0], self._pos[node][1], w, h)
            threshold = self._node_radius(node) * 1.8
            if math.hypot(nx - cx, ny - cy) < threshold:
                return node
        return None

    def _on_motion(
        self, controller: Gtk.EventControllerMotion, x: float, y: float
    ) -> None:
        node = self._node_at(x, y)
        if node == self._hovered:
            return
        self._hovered = node
        if node:
            self._connected = set(self._adjacency.get(node, []))
            self._connected.add(node)
        else:
            self._connected = set()
        self.canvas.queue_draw()

    def _on_motion_leave(self, controller: Gtk.EventControllerMotion) -> None:
        if self._hovered is not None:
            self._hovered = None
            self._connected = set()
            self.canvas.queue_draw()

    def _on_press(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        node = self._node_at(x, y)
        if not node:
            return
        if self._pending_navigate:
            GLib.source_remove(self._pending_navigate)
        self._pending_navigate = GLib.timeout_add(400, self._do_navigate, node)

    def _do_navigate(self, node: str) -> bool:
        self._pending_navigate = 0
        self._hovered = None
        self._connected = set()
        self.on_node_clicked(node)
        return False

    def _on_scroll(
        self,
        controller: Gtk.EventControllerScroll,
        dx: float,
        dy: float,
    ) -> bool:
        factor = 1.1 if dy < 0 else (1 / 1.1)
        self._scale = max(0.1, min(5.0, self._scale * factor))
        self.canvas.queue_draw()
        return True

    def _on_drag_begin(
        self, gesture: Gtk.EventControllerDrag, x: float, y: float
    ) -> None:
        self._drag_start = (x, y)
        self._offset_start = list(self._offset)

    def _on_drag_update(
        self, gesture: Gtk.EventControllerDrag, dx: float, dy: float
    ) -> None:
        self._offset[0] = self._offset_start[0] + dx
        self._offset[1] = self._offset_start[1] + dy
        self.canvas.queue_draw()
