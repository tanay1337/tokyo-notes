"""Graph view — zoomable, pannable force-directed note relationship graph."""
from __future__ import annotations

import math
import random
from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

_REPULSION   = 8_000.0   # node-node repulsion constant
_ATTRACTION  = 0.06      # edge spring constant
_DAMPING     = 0.85      # velocity damping per step
_STEPS       = 120       # simulation steps before display
_NODE_R      = 10        # node circle radius in canvas pixels
_PREVIEW_MS  = 400       # ms before navigating on node click


class GraphView(Gtk.Box):
    """Cairo-rendered graph with zoom, pan, force-directed layout, and click preview."""

    def __init__(
        self,
        graph_data: dict[str, list[str]],
        on_node_clicked: Callable[[str], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.graph_data: dict[str, list[str]] = graph_data
        self.on_node_clicked = on_node_clicked
        self.nodes: list[str] = list(graph_data.keys())

        # Positions in graph-space (not canvas-space).
        self._pos: dict[str, list[float]] = {}
        self._vel: dict[str, list[float]] = {}

        # Viewport state.
        self._scale: float = 1.0
        self._offset: list[float] = [0.0, 0.0]
        self._drag_start: tuple[float, float] | None = None
        self._offset_start: list[float] = [0.0, 0.0]

        # Hovered / selected node for preview panel.
        self._hovered: str | None = None
        self._pending_navigate: int = 0  # GLib source id

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_draw_func(self._on_draw)
        self.canvas.set_vexpand(True)
        self.canvas.set_hexpand(True)
        # Pre-allocate a Pango layout for node labels so _on_draw doesn't
        # allocate a new object on every repaint (every scroll/zoom/resize).
        self._label_layout: object = None  # created lazily on first draw

        # Scroll → zoom.
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self._on_scroll)
        self.canvas.add_controller(scroll)

        # Click → navigate (with short preview delay).
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_press)
        self.canvas.add_controller(click)

        # Drag → pan.
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        self.canvas.add_controller(drag)

        # Instruction label.
        hint = Gtk.Label(label="Scroll to zoom · Drag to pan · Click a node to open")
        hint.add_css_class("dim-label")
        hint.set_margin_bottom(4)

        self.append(self.canvas)
        self.append(hint)

        self._layout_nodes()

    # ------------------------------------------------------------------ #
    # Data
    # ------------------------------------------------------------------ #

    def update_data(self, new_data: dict[str, list[str]]) -> None:
        self.graph_data = new_data
        self.nodes = list(new_data.keys())
        self._layout_nodes()
        self.canvas.queue_draw()

    # ------------------------------------------------------------------ #
    # Force-directed layout
    # ------------------------------------------------------------------ #

    def _layout_nodes(self) -> None:
        """Compute a stable force-directed layout."""
        if not self.nodes:
            self._pos = {}
            return

        rng = random.Random(42)  # deterministic seed for reproducibility
        n = len(self.nodes)
        # Spread initial positions on a circle so forces converge cleanly.
        for i, node in enumerate(self.nodes):
            angle = 2 * math.pi * i / n
            self._pos[node] = [300 * math.cos(angle), 300 * math.sin(angle)]
            self._vel[node] = [rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1)]

        for _ in range(_STEPS):
            forces: dict[str, list[float]] = {n: [0.0, 0.0] for n in self.nodes}

            # Repulsion between every pair.
            for i, a in enumerate(self.nodes):
                for b in self.nodes[i + 1:]:
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

            # Attraction along edges.
            for node, targets in self.graph_data.items():
                if node not in self._pos:
                    continue
                for t in targets:
                    if t not in self._pos:
                        continue
                    dx = self._pos[t][0] - self._pos[node][0]
                    dy = self._pos[t][1] - self._pos[node][1]
                    forces[node][0] += _ATTRACTION * dx
                    forces[node][1] += _ATTRACTION * dy
                    forces[t][0]    -= _ATTRACTION * dx
                    forces[t][1]    -= _ATTRACTION * dy

            for node in self.nodes:
                self._vel[node][0] = (self._vel[node][0] + forces[node][0]) * _DAMPING
                self._vel[node][1] = (self._vel[node][1] + forces[node][1]) * _DAMPING
                self._pos[node][0] += self._vel[node][0]
                self._pos[node][1] += self._vel[node][1]

        # Centre the layout around (0, 0).
        if self.nodes:
            cx = sum(self._pos[n][0] for n in self.nodes) / len(self.nodes)
            cy = sum(self._pos[n][1] for n in self.nodes) / len(self.nodes)
            for n in self.nodes:
                self._pos[n][0] -= cx
                self._pos[n][1] -= cy

    # ------------------------------------------------------------------ #
    # Coordinate helpers
    # ------------------------------------------------------------------ #

    def _graph_to_canvas(self, gx: float, gy: float, w: int, h: int) -> tuple[float, float]:
        return (
            gx * self._scale + w / 2 + self._offset[0],
            gy * self._scale + h / 2 + self._offset[1],
        )

    def _canvas_to_graph(self, cx: float, cy: float, w: int, h: int) -> tuple[float, float]:
        return (
            (cx - w / 2 - self._offset[0]) / self._scale,
            (cy - h / 2 - self._offset[1]) / self._scale,
        )

    # ------------------------------------------------------------------ #
    # Drawing
    # ------------------------------------------------------------------ #

    def _on_draw(
        self, area: Gtk.DrawingArea, cr, width: int, height: int
    ) -> None:
        if not self.nodes:
            return

        ctx = area.get_style_context()
        ok, accent = ctx.lookup_color("accent_color")
        if not ok:
            accent = Gdk.RGBA()
            accent.parse("#7aa2f7")
        ok, fg = ctx.lookup_color("fg_color")
        if not ok:
            fg = Gdk.RGBA()
            fg.parse("#a9b1d6")
        ok, sel = ctx.lookup_color("selection_color")
        if not ok:
            sel = Gdk.RGBA()
            sel.parse("#364a82")

        positions = {
            n: self._graph_to_canvas(self._pos[n][0], self._pos[n][1], width, height)
            for n in self.nodes
            if n in self._pos
        }

        # Edges.
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.25)
        cr.set_line_width(1.5)
        for node, targets in self.graph_data.items():
            if node not in positions:
                continue
            x1, y1 = positions[node]
            for t in targets:
                if t not in positions:
                    continue
                x2, y2 = positions[t]
                cr.move_to(x1, y1)
                cr.line_to(x2, y2)
                cr.stroke()

                # Arrowhead.
                angle = math.atan2(y2 - y1, x2 - x1)
                ax = x2 - (_NODE_R + 2) * math.cos(angle)
                ay = y2 - (_NODE_R + 2) * math.sin(angle)
                hl, ha = 8, math.pi / 6
                cr.move_to(ax, ay)
                cr.line_to(ax - hl * math.cos(angle - ha), ay - hl * math.sin(angle - ha))
                cr.move_to(ax, ay)
                cr.line_to(ax - hl * math.cos(angle + ha), ay - hl * math.sin(angle + ha))
                cr.stroke()

        # Nodes.
        # Reuse the cached layout — only create it once (or when the Cairo
        # context changes, which PangoCairo.create_layout detects internally).
        if self._label_layout is None:
            self._label_layout = PangoCairo.create_layout(cr)
            self._label_layout.set_font_description(
                Pango.FontDescription.from_string("Sans 9")
            )
        layout = self._label_layout

        for node, (x, y) in positions.items():
            is_hovered = node == self._hovered
            r = _NODE_R * (1.4 if is_hovered else 1.0)

            if is_hovered:
                cr.set_source_rgba(sel.red, sel.green, sel.blue, 0.4)
                cr.arc(x, y, r + 4, 0, 2 * math.pi)
                cr.fill()

            cr.set_source_rgb(accent.red, accent.green, accent.blue)
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.fill()

            cr.set_source_rgb(fg.red, fg.green, fg.blue)
            layout.set_text(node, -1)
            cr.move_to(x + r + 4, y - 6)
            PangoCairo.show_layout(cr, layout)

    # ------------------------------------------------------------------ #
    # Interaction
    # ------------------------------------------------------------------ #

    def _node_at(self, cx: float, cy: float) -> str | None:
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        for node in self.nodes:
            if node not in self._pos:
                continue
            nx, ny = self._graph_to_canvas(self._pos[node][0], self._pos[node][1], w, h)
            # 1.8× the visual radius gives a generous touch/click target,
            # matching platform HIG recommendations for small interactive elements.
            if math.hypot(nx - cx, ny - cy) < _NODE_R * 1.8:
                return node
        return None

    def _on_press(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        node = self._node_at(x, y)
        if not node:
            return
        # Short delay so user sees the hover highlight before navigating.
        if self._pending_navigate:
            GLib.source_remove(self._pending_navigate)
        self._hovered = node
        self.canvas.queue_draw()
        self._pending_navigate = GLib.timeout_add(
            _PREVIEW_MS, self._do_navigate, node
        )

    def _do_navigate(self, node: str) -> bool:
        self._pending_navigate = 0
        self._hovered = None
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
        self, gesture: Gtk.GestureDrag, x: float, y: float
    ) -> None:
        self._drag_start = (x, y)
        self._offset_start = list(self._offset)

    def _on_drag_update(
        self, gesture: Gtk.GestureDrag, dx: float, dy: float
    ) -> None:
        self._offset[0] = self._offset_start[0] + dx
        self._offset[1] = self._offset_start[1] + dy
        self.canvas.queue_draw()
