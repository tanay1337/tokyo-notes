"""Diagram view — interactive Cairo canvas editor for node diagrams."""

from __future__ import annotations

import logging
import math
from typing import Callable

logger = logging.getLogger(__name__)

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango, PangoCairo

from core.diagram import Diagram, DiagramEdge, DiagramNode
from core.translations import tr

_NODE_MIN_W = 80.0
_NODE_MIN_H = 40.0
_NODE_PAD_X = 20.0
_NODE_PAD_Y = 12.0
_NODE_RADIUS = 8.0


def _get_color_swatches() -> list[tuple[str, str]]:
    return [
        ("#4a90d9", tr("Default")),
        ("#e95b45", tr("Red")),
        ("#2686C2", tr("Blue")),
        ("#6DA861", tr("Green")),
        ("#EAB308", tr("Yellow")),
        ("#886ce4", tr("Purple")),
    ]


def _get_shapes() -> list[tuple[str, str]]:
    return [
        ("pill", tr("Pill")),
        ("rectangle", tr("Rectangle")),
        ("circle", tr("Circle")),
        ("diamond", tr("Diamond")),
    ]


def _get_edge_types() -> list[tuple[str, str]]:
    return [
        ("solid", tr("Solid")),
        ("dashed", tr("Dashed")),
        ("dotted", tr("Dotted")),
        ("double", tr("Double")),
        ("arrow", tr("Arrow")),
        ("bidirect", tr("Bidirectional")),
    ]


def _rounded_rect_path(
    cr: cairo.Context, x: float, y: float, w: float, h: float, r: float
) -> None:
    """Add a rounded rectangle path to the cairo context."""
    r = min(r, w / 2, h / 2)
    cr.move_to(x + r, y)
    cr.line_to(x + w - r, y)
    cr.curve_to(x + w, y, x + w, y + r, x + w, y + r)
    cr.line_to(x + w, y + h - r)
    cr.curve_to(x + w, y + h, x + w - r, y + h, x + w - r, y + h)
    cr.line_to(x + r, y + h)
    cr.curve_to(x, y + h, x, y + h - r, x, y + h - r)
    cr.line_to(x, y + r)
    cr.curve_to(x, y, x + r, y, x + r, y)
    cr.close_path()


def _pill_path(cr: cairo.Context, x, y, w, h):
    """Pill shape — fully rounded ends."""
    r = min(w, h) / 2
    _rounded_rect_path(cr, x, y, w, h, r)


def _circle_path(cr: cairo.Context, x, y, w, h):
    """Circle shape — centred in the bounding box."""
    r = min(w, h) / 2
    cr.arc(x + w / 2, y + h / 2, r, 0, 2 * math.pi)


def _diamond_path(cr: cairo.Context, x, y, w, h):
    """Diamond shape — 45° rotated square."""
    cx, cy = x + w / 2, y + h / 2
    s = min(w, h) / 2
    cr.move_to(cx, cy - s)
    cr.line_to(cx + s, cy)
    cr.line_to(cx, cy + s)
    cr.line_to(cx - s, cy)
    cr.close_path()


def _node_shape_path(cr: cairo.Context, x, y, w, h, shape: str):
    if shape == "circle":
        _circle_path(cr, x, y, w, h)
    elif shape == "pill":
        _pill_path(cr, x, y, w, h)
    elif shape == "diamond":
        _diamond_path(cr, x, y, w, h)
    else:
        _rounded_rect_path(cr, x, y, w, h, _NODE_RADIUS)


def _draw_arrowhead(cr, tip_x, tip_y, angle, size=10.0):
    ha = math.pi / 6
    cr.move_to(tip_x, tip_y)
    cr.line_to(
        tip_x - size * math.cos(angle - ha),
        tip_y - size * math.sin(angle - ha),
    )
    cr.move_to(tip_x, tip_y)
    cr.line_to(
        tip_x - size * math.cos(angle + ha),
        tip_y - size * math.sin(angle + ha),
    )
    cr.stroke()


def _draw_edge_line(cr, sx, sy, ex, ey, edge_type: str, color, line_width: float):
    mid_y = (sy + ey) / 2
    cr.move_to(sx, sy)
    cr.curve_to(sx, mid_y, ex, mid_y, ex, ey)

    if edge_type == "dashed":
        cr.set_dash([6.0, 3.0], 0)
    elif edge_type == "dotted":
        cr.set_dash([2.0, 3.0], 0)
    else:
        cr.set_dash([], 0)

    if edge_type == "double":
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.set_line_width(line_width)
        cr.stroke()
        offset = 3.0
        cr.move_to(sx, sy - offset)
        cr.curve_to(sx, mid_y - offset, ex, mid_y - offset, ex, ey - offset)
        cr.stroke()
    else:
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.set_line_width(line_width)
        cr.stroke()

    cr.set_dash([], 0)

    angle = math.pi / 2 if ey > mid_y else -math.pi / 2

    if edge_type in ("arrow", "bidirect"):
        _draw_arrowhead(cr, ex, ey, angle, size=8.0)
    if edge_type == "bidirect":
        _draw_arrowhead(cr, sx, sy, angle + math.pi, size=8.0)


def render_diagram_preview(
    diagram: Diagram,
    max_width: int = 400,
    max_height: int = 300,
    bg_color: Gdk.RGBA | None = None,
    text_color: Gdk.RGBA | None = None,
) -> object:
    """Render a diagram to a pixbuf for inline preview in the editor.

    The diagram graph-space coordinates are scaled to fit within
    *max_width* x *max_height* pixels.
    *bg_color* is the editor background colour (theme-aware); if omitted
    a neutral grey is used as fallback.
    *text_color* is the node text colour; if omitted white is used.
    """
    if not diagram or not diagram.nodes:
        return None

    margin = 20.0

    min_x = min(n.x - n.w / 2 for n in diagram.nodes)
    max_x = max(n.x + n.w / 2 for n in diagram.nodes)
    min_y = min(n.y - n.h / 2 for n in diagram.nodes)
    max_y = max(n.y + n.h / 2 for n in diagram.nodes)

    graph_w = max_x - min_x + margin * 2
    graph_h = max_y - min_y + margin * 2

    scale = min(max_width / graph_w, max_height / graph_h)
    scale = max(scale, 0.1)

    surf_w = max(round(graph_w * scale), 100)
    surf_h = max(round(graph_h * scale), 50)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, surf_w, surf_h)
    cr = cairo.Context(surface)

    # Background (theme-aware when bg_color provided)
    if bg_color is not None:
        cr.set_source_rgb(bg_color.red, bg_color.green, bg_color.blue)
    else:
        cr.set_source_rgb(0.97, 0.97, 0.97)
    cr.paint()

    # Centre the diagram in the surface
    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    cr.translate(surf_w / 2, surf_h / 2)
    cr.scale(scale, scale)
    cr.translate(-cx, -cy)

    # Pango layout for high-quality text
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription.from_string("Sans 11")
    layout.set_font_description(desc)

    # Edges
    for edge in diagram.edges:
        src = diagram.find_node(edge.from_id)
        dst = diagram.find_node(edge.to_id)
        if not src or not dst:
            continue

        s_bottom = src.y + src.h / 2
        d_top = dst.y - dst.h / 2
        line_color = Gdk.RGBA(red=0.4, green=0.4, blue=0.4, alpha=0.5)
        _draw_edge_line(
            cr, src.x, s_bottom, dst.x, d_top, edge.edge_type, line_color, 1.5
        )

    # Edge labels with anti-collision
    label_rects: list[tuple[float, float, float, float]] = []
    for edge in diagram.edges:
        if not edge.label:
            continue
        src = diagram.find_node(edge.from_id)
        dst = diagram.find_node(edge.to_id)
        if not src or not dst:
            continue
        s_bottom = src.y + src.h / 2
        d_top = dst.y - dst.h / 2
        mid_x = (src.x + dst.x) / 2
        mid_y = (s_bottom + d_top) / 2

        layout.set_text(edge.label, -1)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_width(int(150 * Pango.SCALE))
        lw_t, lh_t = layout.get_pixel_size()
        if text_color is not None:
            cr.set_source_rgba(text_color.red, text_color.green, text_color.blue, 0.8)
        else:
            cr.set_source_rgba(1, 1, 1, 0.8)

        def _overlaps(r, others):
            rx, ry, rw, rh = r
            for ox, oy, ow, oh in others:
                if rx < ox + ow and rx + rw > ox and ry < oy + oh and ry + rh > oy:
                    return True
            return False

        ax = mid_x - lw_t / 2
        ay = mid_y - lh_t - 4
        a_rect = (ax, ay, lw_t, lh_t)

        bx = mid_x - lw_t / 2
        by = mid_y + 4
        b_rect = (bx, by, lw_t, lh_t)

        if not _overlaps(a_rect, label_rects):
            cr.move_to(ax, ay)
            label_rects.append(a_rect)
        elif not _overlaps(b_rect, label_rects):
            cr.move_to(bx, by)
            label_rects.append(b_rect)
        else:
            cr.move_to(ax, ay)
            label_rects.append(a_rect)
        PangoCairo.show_layout(cr, layout)

    # Nodes
    for node in diagram.nodes:
        nw = node.w
        nh = node.h
        rx = node.x - nw / 2
        ry = node.y - nh / 2

        col = Gdk.RGBA()
        col.parse(node.color)
        cr.set_source_rgb(col.red, col.green, col.blue)
        _node_shape_path(cr, rx, ry, nw, nh, node.shape)
        cr.fill()

        # Node text — hardcoded white matches on-screen _on_draw
        if node.text:
            layout.set_text(node.text, -1)
            lw, lh = layout.get_pixel_size()
            cr.set_source_rgb(1, 1, 1)
            cr.move_to(node.x - lw / 2, node.y - lh / 2)
            PangoCairo.show_layout(cr, layout)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, surf_w, surf_h)


class DiagramView(Gtk.Box):
    """Interactive diagram editor with Cairo canvas, zoom/pan, and node editing."""

    def __init__(
        self,
        diagram_manager: object,
        on_save_and_insert: Callable[[Diagram], None],
        on_close: Callable[[], None],
        on_save_only: Callable[[Diagram], None] | None = None,
        on_diagram_delete: Callable[[str], None] | None = None,
        on_title_changed: Callable[[str], None] | None = None,
        transient_for: Gtk.Window | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._diagram_manager = diagram_manager
        self._on_save_and_insert = on_save_and_insert
        self._on_close = on_close
        self._on_save_only = on_save_only
        self._on_diagram_delete = on_diagram_delete
        self._on_title_changed = on_title_changed
        self._transient_for = transient_for

        self._diagram: Diagram | None = None
        self._dirty: bool = False

        # Viewport
        self._scale: float = 1.0
        self._offset: list[float] = [0.0, 0.0]
        self._fit_anim_id: int = 0

        # Interaction state
        self._selected: set[str] = set()
        self._selected_edge: str | None = None
        self._hovered: str | None = None
        self._drag_type: str | None = None
        self._drag_node_id: str | None = None
        self._drag_start: list[float] = [0.0, 0.0]
        self._offset_start: list[float] = [0.0, 0.0]
        self._node_pos_start: dict[str, tuple[float, float]] = {}

        # Layout cache
        self._text_layout: Pango.Layout | None = None
        self._size_layout: Pango.Layout | None = None

        # Edit entry state
        self._edit_node_id: str | None = None
        self._edit_original_text: str = ""

        # Undo / Redo
        self._undo_stack: list[tuple[str, Diagram]] = []
        self._redo_stack: list[tuple[str, Diagram]] = []

        # Clipboard (in-memory)
        self._clipboard: list[dict] = []
        self._clipboard_edges: list[tuple[str, str, str, str]] = []

        # Autosave
        self._autosave_id: int = 0

        self._setup_ui()

    # ── UI construction ──────────────────────────────────────────────

    def _setup_ui(self) -> None:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(4)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)

        self.diagrams_btn = Gtk.Button(label=tr("Diagrams"))
        self.diagrams_btn.add_css_class("pill")
        self.diagrams_btn.connect("clicked", self._show_diagram_list)
        toolbar.append(self.diagrams_btn)

        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Undo / Redo
        self.undo_btn = Gtk.Button(label=tr("Undo"))
        self.undo_btn.add_css_class("pill")
        self.undo_btn.set_sensitive(False)
        self.undo_btn.set_tooltip_text(tr("Undo (Ctrl+Z)"))
        self.undo_btn.connect("clicked", self._undo)
        toolbar.append(self.undo_btn)

        self.redo_btn = Gtk.Button(label=tr("Redo"))
        self.redo_btn.add_css_class("pill")
        self.redo_btn.set_sensitive(False)
        self.redo_btn.set_tooltip_text(tr("Redo (Ctrl+Y)"))
        self.redo_btn.connect("clicked", self._redo)
        toolbar.append(self.redo_btn)

        self.save_btn = Gtk.Button(label=tr("Save and Insert"))
        self.save_btn.add_css_class("pill")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.set_tooltip_text(tr("Save diagram and insert into note"))
        self.save_btn.connect("clicked", self._on_save_and_insert_clicked)
        toolbar.append(self.save_btn)

        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.fit_btn = Gtk.Button(label=tr("Zoom Fit"))
        self.fit_btn.add_css_class("pill")
        self.fit_btn.connect("clicked", self._on_fit_clicked)
        toolbar.append(self.fit_btn)

        self._zoom_label = Gtk.Label(label="100%")
        self._zoom_label.set_margin_start(4)
        self._zoom_label.add_css_class("dim-label")
        toolbar.append(self._zoom_label)

        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.add_child_btn = Gtk.Button(label=tr("Add Child"))
        self.add_child_btn.add_css_class("pill")
        self.add_child_btn.set_sensitive(False)
        self.add_child_btn.set_tooltip_text(tr("Add child node"))
        self.add_child_btn.connect("clicked", self._on_add_child)
        toolbar.append(self.add_child_btn)

        self.delete_node_btn = Gtk.Button(label=tr("Delete"))
        self.delete_node_btn.add_css_class("pill")
        self.delete_node_btn.add_css_class("destructive-action")
        self.delete_node_btn.set_sensitive(False)
        self.delete_node_btn.set_tooltip_text(tr("Delete (Del)"))
        self.delete_node_btn.connect("clicked", self._on_delete_selected)
        toolbar.append(self.delete_node_btn)

        self.export_btn = Gtk.Button(label=tr("Export"))
        self.export_btn.add_css_class("pill")
        self.export_btn.set_tooltip_text(tr("Export as PNG"))
        self.export_btn.connect("clicked", self._export_png)
        toolbar.append(self.export_btn)

        # ── Second toolbar row: style ──
        stylebar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        stylebar.set_margin_start(6)
        stylebar.set_margin_end(6)
        stylebar.set_margin_top(4)
        stylebar.set_margin_bottom(4)

        self._color_btns: list[Gtk.ToggleButton] = []
        for hex_color, name in _get_color_swatches():
            btn = Gtk.ToggleButton()
            btn.set_tooltip_text(name)
            css = Gtk.CssProvider()
            css_data = (
                f"button {{ background-color: {hex_color};"
                " min-width: 24px; min-height: 24px;"
                " border-radius: 50%;"
                " padding: 0; border: none; }"
                f"button:checked {{ background-color: {hex_color};"
                " border-radius: 50%;"
                " border: none; }"
            )
            css.load_from_data(css_data.encode())
            btn.get_style_context().add_provider(
                css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            btn.connect("clicked", self._on_color_picked, hex_color)
            btn.set_sensitive(False)
            stylebar.append(btn)
            self._color_btns.append(btn)

        stylebar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self._shape_group: list[Gtk.ToggleButton] = []
        for shape_val, shape_name in _get_shapes():
            btn = Gtk.ToggleButton(label=shape_name)
            btn.set_tooltip_text(shape_name)
            btn.add_css_class("pill")
            btn.connect("clicked", self._on_shape_picked, shape_val)
            btn.set_sensitive(False)
            stylebar.append(btn)
            self._shape_group.append(btn)

        hint = Gtk.Label(label=tr("Drag \u00b7 D-click to edit \u00b7 R-click menu"))
        hint.add_css_class("dim-label")
        hint.set_halign(Gtk.Align.END)
        hint.set_hexpand(True)
        hint.set_margin_end(8)
        stylebar.append(hint)

        self.append(toolbar)
        self.append(stylebar)

        # Canvas overlay (for floating edit entry)
        self._canvas_overlay = Gtk.Overlay()
        self._canvas_overlay.set_vexpand(True)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_draw_func(self._on_draw)
        self.canvas.set_vexpand(True)
        self.canvas.set_hexpand(True)
        self.canvas.set_focusable(True)
        self._canvas_overlay.set_child(self.canvas)

        # Hidden edit entry, positioned over the canvas
        self._edit_entry = Gtk.Entry()
        self._edit_entry.set_visible(False)
        self._canvas_overlay.add_overlay(self._edit_entry)

        self.append(self._canvas_overlay)

        # Event controllers
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.canvas.add_controller(scroll)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_motion_leave)
        self.canvas.add_controller(motion)

        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("pressed", self._on_click_pressed)
        self.canvas.add_controller(click)

        rclick = Gtk.GestureClick.new()
        rclick.set_button(3)
        rclick.connect("pressed", self._on_right_click)
        self.canvas.add_controller(rclick)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.canvas.add_controller(drag)

        key = Gtk.EventControllerKey.new()
        key.connect("key-pressed", self._on_key_pressed)
        self.canvas.add_controller(key)

    # ── Public API ───────────────────────────────────────────────────

    def set_diagram(self, diagram: Diagram) -> None:
        """Load a diagram into the editor and zoom to fit."""
        self._diagram = diagram
        self._selected = set()
        self._hovered = None
        self._cancel_autosave()
        self._dirty = False
        self._edit_node_id = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_buttons()
        self._update_toolbar_buttons()
        self._update_zoom_label()
        GLib.idle_add(self._fit_once_allocated)
        self.canvas.queue_draw()

    def _primary_selected(self) -> str | None:
        return next(iter(self._selected)) if self._selected else None

    def get_diagram(self) -> Diagram | None:
        return self._diagram

    # ── Coordinate helpers ───────────────────────────────────────────

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

    # ── Hit testing ──────────────────────────────────────────────────

    def _node_at(self, cx: float, cy: float) -> str | None:
        if not self._diagram:
            return None
        cw = self.canvas.get_width()
        ch = self.canvas.get_height()
        for node in reversed(self._diagram.nodes):
            nx, ny = self._graph_to_canvas(node.x, node.y, cw, ch)
            half_w = node.w * self._scale / 2
            half_h = node.h * self._scale / 2
            if abs(cx - nx) <= half_w and abs(cy - ny) <= half_h:
                return node.id
        return None

    def _edge_near(self, cx: float, cy: float) -> str | None:
        """Return the ID of the edge within 15px of (cx, cy)."""
        if not self._diagram:
            return None
        cw = self.canvas.get_width()
        ch = self.canvas.get_height()
        threshold = 15.0
        for edge in self._diagram.edges:
            src = self._diagram.find_node(edge.from_id)
            dst = self._diagram.find_node(edge.to_id)
            if not src or not dst:
                continue
            sx, sy = self._graph_to_canvas(src.x, src.y, cw, ch)
            dx, dy = self._graph_to_canvas(dst.x, dst.y, cw, ch)
            x1, y1 = sx, sy + src.h * self._scale / 2
            x2, y2 = dx, dy - dst.h * self._scale / 2
            # Distance from point (cx, cy) to the line segment
            seg_dx = x2 - x1
            seg_dy = y2 - y1
            seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy
            if seg_len_sq > 0:
                t = max(
                    0.0,
                    min(1.0, ((cx - x1) * seg_dx + (cy - y1) * seg_dy) / seg_len_sq),
                )
                near_x = x1 + t * seg_dx
                near_y = y1 + t * seg_dy
                if math.hypot(cx - near_x, cy - near_y) <= threshold:
                    return edge.id
            else:
                if math.hypot(cx - x1, cy - y1) <= threshold:
                    return edge.id
        return None

    # ── Drawing ──────────────────────────────────────────────────────

    def _on_draw(self, area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        if not self._diagram:
            return

        if self._text_layout is None:
            self._text_layout = PangoCairo.create_layout(cr)
            desc = Pango.FontDescription.from_string("Sans 11")
            self._text_layout.set_font_description(desc)
        layout = self._text_layout

        ctx = area.get_style_context()

        def _lookup(name: str, fallback: str) -> Gdk.RGBA:
            ok, c = ctx.lookup_color(name)
            if not ok:
                c = Gdk.RGBA()
                c.parse(fallback)
            return c

        fg = _lookup("fg_color", "#333333")
        accent = _lookup("accent_color", "#4a90d9")
        ok_bg, base = ctx.lookup_color("editor_bg")
        if not ok_bg:
            base = _lookup("theme_base_color", "#ffffff")

        # Background
        cr.set_source_rgb(base.red, base.green, base.blue)
        cr.paint()

        # Cache canvas positions and update node sizes
        positions: dict[str, tuple[float, float]] = {}
        for node in self._diagram.nodes:
            if self._edit_node_id != node.id:
                layout.set_text(node.text, -1)
                lw, lh = layout.get_pixel_size()
                node.w = max(lw + _NODE_PAD_X * 2, _NODE_MIN_W)
                node.h = max(lh + _NODE_PAD_Y * 2, _NODE_MIN_H)
            positions[node.id] = self._graph_to_canvas(node.x, node.y, width, height)

        # ── Edges ──
        for edge in self._diagram.edges:
            src = self._diagram.find_node(edge.from_id)
            dst = self._diagram.find_node(edge.to_id)
            if not src or not dst:
                continue
            if src.id not in positions or dst.id not in positions:
                continue

            sx, sy = positions[src.id]
            dx, dy = positions[dst.id]

            s_bottom = sy + src.h * self._scale / 2
            d_top = dy - dst.h * self._scale / 2
            is_hovered = edge.from_id == self._hovered or edge.to_id == self._hovered
            is_edge_sel = edge.id == self._selected_edge
            if is_hovered or is_edge_sel:
                ec = Gdk.RGBA(
                    red=accent.red,
                    green=accent.green,
                    blue=accent.blue,
                    alpha=0.8,
                )
                lw = 2.0
            else:
                ec = Gdk.RGBA(red=fg.red, green=fg.green, blue=fg.blue, alpha=0.25)
                lw = 1.5

            _draw_edge_line(cr, sx, s_bottom, dx, d_top, edge.edge_type, ec, lw)

        # Edge labels with anti-collision
        label_rects: list[tuple[float, float, float, float]] = []
        for edge in self._diagram.edges:
            if not edge.label:
                continue
            src = self._diagram.find_node(edge.from_id)
            dst = self._diagram.find_node(edge.to_id)
            if not src or not dst:
                continue
            if src.id not in positions or dst.id not in positions:
                continue
            sx, sy = positions[src.id]
            dx, dy = positions[dst.id]
            s_bottom = sy + src.h * self._scale / 2
            d_top = dy - dst.h * self._scale / 2
            mid_x = (sx + dx) / 2
            mid_y = (s_bottom + d_top) / 2

            layout.set_text(edge.label, -1)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_width(int(150 * Pango.SCALE))
            lw_t, lh_t = layout.get_pixel_size()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.8)

            # Candidate A: above midpoint (default)
            ax = mid_x - lw_t / 2
            ay = mid_y - lh_t - 4
            a_rect = (ax, ay, lw_t, lh_t)

            # Candidate B: below midpoint
            bx = mid_x - lw_t / 2
            by = mid_y + 4
            b_rect = (bx, by, lw_t, lh_t)

            def _overlaps(r, others):
                rx, ry, rw, rh = r
                for ox, oy, ow, oh in others:
                    if rx < ox + ow and rx + rw > ox and ry < oy + oh and ry + rh > oy:
                        return True
                return False

            if not _overlaps(a_rect, label_rects):
                cr.move_to(ax, ay)
                label_rects.append(a_rect)
            elif not _overlaps(b_rect, label_rects):
                cr.move_to(bx, by)
                label_rects.append(b_rect)
            else:
                cr.move_to(ax, ay)
                label_rects.append(a_rect)
            PangoCairo.show_layout(cr, layout)

        # ── Nodes ──
        for node in self._diagram.nodes:
            if node.id not in positions:
                continue
            cx, cy = positions[node.id]
            nw = node.w * self._scale
            nh = node.h * self._scale
            rx = cx - nw / 2
            ry = cy - nh / 2

            is_sel = node.id in self._selected
            is_hov = node.id == self._hovered
            is_editing = node.id == self._edit_node_id

            # Background
            col = Gdk.RGBA()
            col.parse(node.color)
            cr.set_source_rgb(col.red, col.green, col.blue)
            _node_shape_path(cr, rx, ry, nw, nh, node.shape)
            cr.fill()

            # Border
            if is_sel or is_editing:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.9)
                cr.set_line_width(2.5)
                _node_shape_path(cr, rx - 2, ry - 2, nw + 4, nh + 4, node.shape)
                cr.stroke()
            elif is_hov:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.4)
                cr.set_line_width(1.5)
                _node_shape_path(cr, rx - 1.5, ry - 1.5, nw + 3, nh + 3, node.shape)
                cr.stroke()

            # Text (skip if being edited in the entry overlay)
            if not is_editing:
                cr.set_source_rgb(1, 1, 1)
                layout.set_text(node.text, -1)
                lw, lh = layout.get_pixel_size()
                cr.move_to(cx - lw / 2, cy - lh / 2)
                PangoCairo.show_layout(cr, layout)

    # ── Interaction: zoom/pan ────────────────────────────────────────

    def _on_scroll(
        self, controller: Gtk.EventControllerScroll, dx: float, dy: float
    ) -> bool:
        factor = 1.1 if dy < 0 else (1 / 1.1)
        self._scale = max(0.1, min(5.0, self._scale * factor))
        self._update_zoom_label()
        self.canvas.queue_draw()
        return True

    def _on_motion(
        self, controller: Gtk.EventControllerMotion, x: float, y: float
    ) -> None:
        if self._edit_node_id is not None:
            return
        nid = self._node_at(x, y)
        if nid != self._hovered:
            self._hovered = nid
            self.canvas.queue_draw()

    def _on_motion_leave(self, controller: Gtk.EventControllerMotion) -> None:
        if self._hovered is not None:
            self._hovered = None
            self.canvas.queue_draw()

    def _on_fit_clicked(self, _btn: object = None) -> None:
        if not self._diagram or not self._diagram.nodes:
            return
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        if w <= 0 or h <= 0:
            return

        min_x = min(n.x for n in self._diagram.nodes)
        max_x = max(n.x for n in self._diagram.nodes)
        min_y = min(n.y for n in self._diagram.nodes)
        max_y = max(n.y for n in self._diagram.nodes)

        padding = 80.0
        graph_w = max_x - min_x + padding * 2
        graph_h = max_y - min_y + padding * 2
        target_scale = min(w / graph_w, h / graph_h, 3.0)

        target_offset_x = -(min_x + max_x) / 2 * target_scale
        target_offset_y = -(min_y + max_y) / 2 * target_scale

        start_scale = self._scale
        start_offset = list(self._offset)
        start_time: float | None = None

        def animate(_widget: Gtk.Widget, frame_clock: Gdk.FrameClock) -> bool:
            nonlocal start_time
            if start_time is None:
                start_time = frame_clock.get_frame_time() / 1_000_000.0
            elapsed = frame_clock.get_frame_time() / 1_000_000.0 - start_time
            duration = 0.3
            if elapsed >= duration:
                self._scale = target_scale
                self._offset = [target_offset_x, target_offset_y]
                self._fit_anim_id = 0
                self._update_zoom_label()
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

    def _fit_to_canvas(self) -> None:
        """Jump directly to the fit scale/offset without animation."""
        if not self._diagram or not self._diagram.nodes:
            return
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        if w <= 0 or h <= 0:
            return
        min_x = min(n.x for n in self._diagram.nodes)
        max_x = max(n.x for n in self._diagram.nodes)
        min_y = min(n.y for n in self._diagram.nodes)
        max_y = max(n.y for n in self._diagram.nodes)
        padding = 80.0
        graph_w = max_x - min_x + padding * 2
        graph_h = max_y - min_y + padding * 2
        self._scale = min(w / graph_w, h / graph_h, 3.0)
        self._offset[0] = -(min_x + max_x) / 2 * self._scale
        self._offset[1] = -(min_y + max_y) / 2 * self._scale
        self._update_zoom_label()
        self.canvas.queue_draw()

    def _fit_once_allocated(self) -> bool:
        """Call fit after canvas has been allocated its size (idle callback)."""
        if self.canvas.get_width() > 0 and self.canvas.get_height() > 0:
            self._fit_to_canvas()
            return False
        return True  # re-schedule if not yet allocated

    def _update_zoom_label(self) -> None:
        """Refresh the zoom percentage label."""
        pct = round(self._scale * 100)
        self._zoom_label.set_text(f"{pct}%")

    # ── Interaction: drag (pan / move node) ──────────────────────────

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        if self._edit_node_id is not None:
            return
        self._drag_start = [x, y]
        self._offset_start = list(self._offset)
        nid = self._node_at(x, y)
        if nid:
            self._push_undo(tr("Move node"))
            self._drag_type = "move_node"
            self._drag_node_id = nid
            if self._diagram:
                self._node_pos_start = {}
                state = gesture.get_current_event_state()
                ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
                if not ctrl and nid not in self._selected:
                    self._selected = {nid}
                for did in self._selected:
                    n = self._diagram.find_node(did)
                    if n:
                        self._node_pos_start[did] = (n.x, n.y)
        else:
            self._drag_type = "pan"
            self._drag_node_id = None

    def _on_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._drag_type == "pan":
            self._offset[0] = self._offset_start[0] + dx
            self._offset[1] = self._offset_start[1] + dy
            self.canvas.queue_draw()
        elif self._drag_type == "move_node" and self._drag_node_id and self._diagram:
            cw = self.canvas.get_width()
            ch = self.canvas.get_height()
            # Mouse delta in graph space from drag origin
            mx, my = self._canvas_to_graph(
                self._drag_start[0] + dx,
                self._drag_start[1] + dy,
                cw,
                ch,
            )
            sx_g, sy_g = self._canvas_to_graph(
                self._drag_start[0],
                self._drag_start[1],
                cw,
                ch,
            )
            delta_gx = mx - sx_g
            delta_gy = my - sy_g
            for did, (orig_sx, orig_sy) in self._node_pos_start.items():
                node = self._diagram.find_node(did)
                if node:
                    node.x = orig_sx + delta_gx
                    node.y = orig_sy + delta_gy
            self.canvas.queue_draw()

    def _on_drag_end(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._drag_type == "move_node" and self._node_pos_start:
            self._mark_dirty()
        self._drag_type = None
        self._drag_node_id = None
        self._node_pos_start.clear()

    # ── Interaction: click / double-click ────────────────────────────

    def _on_click_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        if n_press >= 2:
            nid = self._node_at(x, y)
            if nid:
                self._selected = {nid}
                self._update_toolbar_buttons()
                self._start_edit_node(nid, x, y)
            return

        nid = self._node_at(x, y)
        state = gesture.get_current_event_state()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if ctrl and nid:
            if nid in self._selected:
                self._selected.discard(nid)
            else:
                self._selected.add(nid)
        else:
            self._selected = {nid} if nid else set()

        self._update_toolbar_buttons()
        self._update_style_buttons()
        self.canvas.queue_draw()
        if nid:
            self.canvas.grab_focus()

    # ── Interaction: right-click context menu ────────────────────────

    def _on_right_click(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        if not self._diagram:
            return

        nid = self._node_at(x, y)
        eid = self._edge_near(x, y)

        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.connect("closed", lambda p, *_: GLib.idle_add(p.unparent))
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_margin_top(4)
        vbox.set_margin_bottom(4)

        def _item(label: str, fn) -> None:
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.set_halign(Gtk.Align.FILL)
            btn.set_hexpand(True)
            btn.connect("clicked", lambda *_: (popover.popdown(), fn()))
            vbox.append(btn)

        if nid:
            # Node context menu
            self._selected = {nid}
            self._selected_edge = None
            self._update_toolbar_buttons()
            self.canvas.queue_draw()

            _item(tr("Rename"), lambda: self._start_edit_node(nid, x, y))
            _item(tr("Add Child"), lambda: self._add_child(nid))
            _item(tr("Add Sibling"), lambda: self._add_sibling(nid))
            vbox.append(Gtk.Separator())
            _item(tr("Copy"), self._copy_selected)
            _item(tr("Duplicate"), self._duplicate_selected)
            vbox.append(Gtk.Separator())
            _item(tr("Delete"), lambda: self._delete_node(nid))

            vbox.append(Gtk.Separator())
            color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            color_box.set_margin_start(4)
            color_box.set_margin_end(4)
            color_box.set_margin_top(4)
            color_box.set_margin_bottom(4)
            for hex_color, name in _get_color_swatches():
                cbtn = Gtk.Button()
                cbtn.set_tooltip_text(name)
                cbtn.set_size_request(20, 20)
                css_c = Gtk.CssProvider()
                css_c_data = (
                    f"button {{ background-color: {hex_color};"
                    " min-width: 20px; min-height: 20px;"
                    " border-radius: 50%;"
                    " padding: 0; border: none; }"
                )
                css_c.load_from_data(css_c_data.encode())
                cbtn.get_style_context().add_provider(
                    css_c, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                cbtn.connect(
                    "clicked",
                    lambda *_, h=hex_color: (
                        self._apply_color(h),
                        popover.popdown(),
                    ),
                )
                color_box.append(cbtn)
            vbox.append(color_box)

        elif eid:
            # Edge context menu
            self._selected_edge = eid
            self.canvas.queue_draw()
            edge = next((e for e in self._diagram.edges if e.id == eid), None)
            if not edge:
                return

            group: Gtk.CheckButton | None = None
            for et_val, et_name in _get_edge_types():
                et_btn = Gtk.CheckButton(label=et_name)
                et_btn.set_active(et_val == edge.edge_type)
                if group is not None:
                    et_btn.set_group(group)
                else:
                    group = et_btn
                et_btn.connect(
                    "toggled",
                    lambda *_, v=et_val, e=eid, b=et_btn: (
                        self._apply_edge_type(e, v) if b.get_active() else None
                    ),
                )
                vbox.append(et_btn)
            vbox.append(Gtk.Separator())

            if edge.label:
                _item(tr("Edit Label"), lambda: self._edit_edge_label(eid))
                _item(tr("Remove Label"), lambda: self._remove_edge_label(eid))
            else:
                _item(tr("Add Label"), lambda: self._edit_edge_label(eid))

        else:
            return

        popover.set_child(vbox)
        popover.set_parent(self.canvas)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    # ── Interaction: keyboard ────────────────────────────────────────

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self._undo()
            return True
        if ctrl and keyval in (Gdk.KEY_y, Gdk.KEY_Y):
            self._redo()
            return True
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self._copy_selected()
            return True
        if ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self._paste()
            return True
        if ctrl and keyval in (Gdk.KEY_d, Gdk.KEY_D):
            self._duplicate_selected()
            return True

        if keyval in (Gdk.KEY_F2,):
            pid = self._primary_selected()
            if pid and self._diagram:
                node = self._diagram.find_node(pid)
                if node:
                    cw = self.canvas.get_width()
                    ch = self.canvas.get_height()
                    nx, ny = self._graph_to_canvas(node.x, node.y, cw, ch)
                    self._start_edit_node(pid, nx, ny)
            return True

        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete, Gdk.KEY_BackSpace):
            if self._selected and self._diagram:
                self._delete_selected_nodes()
                return True

        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            if self._selected and self._diagram:
                shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
                step = 10.0 if shift else 1.0
                dx = dy = 0.0
                if keyval == Gdk.KEY_Up:
                    dy = -step
                elif keyval == Gdk.KEY_Down:
                    dy = step
                elif keyval == Gdk.KEY_Left:
                    dx = -step
                elif keyval == Gdk.KEY_Right:
                    dx = step
                self._nudge_selected(dx, dy)
                return True
        return False

    # ── Dialogs (rename node / edge label) ────────────────────────────

    def _show_text_dialog(
        self,
        title: str,
        current_text: str,
        on_save: Callable[[str], None],
    ) -> None:
        """Show a centered dialog with a text entry."""
        entry = Gtk.Entry()
        entry.set_text(current_text)
        entry.set_activates_default(True)
        entry.select_region(0, -1)

        dialog = Adw.MessageDialog(
            transient_for=self._transient_for,
            heading=title,
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("ok", tr("OK"))
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def on_response(d: Adw.MessageDialog, response: str) -> None:
            if response == "ok":
                text = entry.get_text().strip()
                if text:
                    on_save(text)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()
        entry.grab_focus()

    def _start_edit_node(self, node_id: str, click_x: float, click_y: float) -> None:
        if not self._diagram:
            return
        node = self._diagram.find_node(node_id)
        if not node:
            return
        self._show_text_dialog(
            tr("Rename Node"),
            node.text,
            lambda new_text: self._apply_rename(node_id, new_text),
        )

    def _apply_rename(self, node_id: str, new_text: str) -> None:
        if not self._diagram:
            return
        node = self._diagram.find_node(node_id)
        if node:
            node.text = new_text
            self._mark_dirty()
            self.canvas.queue_draw()

    # ── Node operations ──────────────────────────────────────────────

    def _node_rects(self) -> list[tuple[float, float, float, float]]:
        return [(n.x - n.w / 2, n.y - n.h / 2, n.w, n.h) for n in self._diagram.nodes]

    def _estimate_node_size(self, text: str) -> tuple[float, float]:
        if self._size_layout is None:
            fontmap = PangoCairo.font_map_get_default()
            ctx = fontmap.create_context()
            self._size_layout = Pango.Layout(ctx)
            desc = Pango.FontDescription.from_string("Sans 11")
            self._size_layout.set_font_description(desc)
        self._size_layout.set_text(text, -1)
        lw, lh = self._size_layout.get_pixel_size()
        return max(lw + _NODE_PAD_X * 2, _NODE_MIN_W), max(
            lh + _NODE_PAD_Y * 2, _NODE_MIN_H
        )

    def _edge_hits_any_node(
        self,
        sx: float,
        sy: float,
        ex: float,
        ey: float,
        node_rects: list[tuple[float, float, float, float]],
    ) -> bool:
        mid_y = (sy + ey) / 2
        for i in range(21):
            t = i / 20
            mt = 1 - t
            bx = mt**3 * sx + 3 * mt**2 * t * sx + 3 * mt * t**2 * ex + t**3 * ex
            by = mt**3 * sy + 3 * mt**2 * t * mid_y + 3 * mt * t**2 * mid_y + t**3 * ey
            for rx, ry, rw, rh in node_rects:
                if rx <= bx <= rx + rw and ry <= by <= ry + rh:
                    return True
        return False

    def _add_child(self, parent_id: str) -> None:
        if not self._diagram:
            return
        self._push_undo(tr("Add child"))
        parent = self._diagram.find_node(parent_id)
        if not parent:
            return
        children = self._diagram.children_of(parent_id)
        n = len(children)
        if n == 0:
            offset_x = parent.x
        elif n % 2 == 1:  # right side
            offset_x = parent.x + ((n + 1) // 2) * 70
        else:  # left side
            offset_x = parent.x - (n // 2) * 70
        offset_y = parent.y + 150

        nw, nh = self._estimate_node_size("New Node")
        ox, oy = offset_x, offset_y
        hw, hh = nw / 2, nh / 2

        sx, sy = parent.x, parent.y + parent.h / 2
        all_boxes = self._node_rects()
        parent_box = (
            parent.x - parent.w / 2,
            parent.y - parent.h / 2,
            parent.w,
            parent.h,
        )
        edge_boxes = [b for b in all_boxes if b != parent_box]

        ex, ey = ox, oy - hh
        mid_y = (sy + ey) / 2

        min_oy = oy
        for rx, ry, rw, rh in all_boxes:
            if (
                ox - hw < rx + rw
                and ox + hw > rx
                and oy - hh < ry + rh
                and oy + hh > ry
            ):
                min_oy = max(min_oy, ry + rh + 10)
        for rx, ry, rw, rh in edge_boxes:
            for i in range(21):
                t = i / 20
                mt = 1 - t
                bx = mt**3 * sx + 3 * mt**2 * t * sx + 3 * mt * t**2 * ex + t**3 * ex
                by = (
                    mt**3 * sy
                    + 3 * mt**2 * t * mid_y
                    + 3 * mt * t**2 * mid_y
                    + t**3 * ey
                )
                if rx <= bx <= rx + rw and ry <= by <= ry + rh:
                    min_oy = max(min_oy, ry + rh + 10)
                    break
        oy = min_oy

        for _ in range(5):
            ex, ey = ox, oy - hh
            hits = any(
                ox - hw < rx + rw
                and ox + hw > rx
                and oy - hh < ry + rh
                and oy + hh > ry
                for rx, ry, rw, rh in all_boxes
            ) or self._edge_hits_any_node(sx, sy, ex, ey, edge_boxes)
            if not hits:
                break
            oy += 30
        child = DiagramNode.new("New Node", ox, oy)
        self._diagram.nodes.append(child)
        edge = DiagramEdge.new(parent_id, child.id)
        self._diagram.edges.append(edge)
        self._selected = {child.id}
        self._mark_dirty()
        self._update_toolbar_buttons()
        self.canvas.queue_draw()

    def _add_sibling(self, node_id: str) -> None:
        if not self._diagram:
            return
        self._push_undo(tr("Add sibling"))
        parent = self._diagram.parent_of(node_id)
        if not parent:
            return
        node = self._diagram.find_node(node_id)
        if not node:
            return
        nw, nh = self._estimate_node_size("New Node")
        ox, oy = node.x + 140, node.y
        hw, hh = nw / 2, nh / 2

        sx, sy = parent.x, parent.y + parent.h / 2
        all_boxes = self._node_rects()
        parent_box = (
            parent.x - parent.w / 2,
            parent.y - parent.h / 2,
            parent.w,
            parent.h,
        )
        node_box = (node.x - node.w / 2, node.y - node.h / 2, node.w, node.h)
        edge_boxes = [b for b in all_boxes if b != parent_box and b != node_box]

        ex, ey = ox, oy - hh
        mid_y = (sy + ey) / 2

        min_oy = oy
        for rx, ry, rw, rh in all_boxes:
            if (
                ox - hw < rx + rw
                and ox + hw > rx
                and oy - hh < ry + rh
                and oy + hh > ry
            ):
                min_oy = max(min_oy, ry + rh + 10)
        for rx, ry, rw, rh in edge_boxes:
            for i in range(21):
                t = i / 20
                mt = 1 - t
                bx = mt**3 * sx + 3 * mt**2 * t * sx + 3 * mt * t**2 * ex + t**3 * ex
                by = (
                    mt**3 * sy
                    + 3 * mt**2 * t * mid_y
                    + 3 * mt * t**2 * mid_y
                    + t**3 * ey
                )
                if rx <= bx <= rx + rw and ry <= by <= ry + rh:
                    min_oy = max(min_oy, ry + rh + 10)
                    break
        oy = min_oy

        for _ in range(5):
            ex, ey = ox, oy - hh
            hits = any(
                ox - hw < rx + rw
                and ox + hw > rx
                and oy - hh < ry + rh
                and oy + hh > ry
                for rx, ry, rw, rh in all_boxes
            ) or self._edge_hits_any_node(sx, sy, ex, ey, edge_boxes)
            if not hits:
                break
            oy += 30
        sibling = DiagramNode.new("New Node", ox, oy)
        self._diagram.nodes.append(sibling)
        edge = DiagramEdge.new(parent.id, sibling.id)
        self._diagram.edges.append(edge)
        self._selected = {sibling.id}
        self._mark_dirty()
        self._update_toolbar_buttons()
        self.canvas.queue_draw()

    def _nudge_selected(self, dx: float, dy: float) -> None:
        if not self._diagram or not self._selected:
            return
        self._push_undo(tr("Nudge"))
        for nid in self._selected:
            node = self._diagram.find_node(nid)
            if node:
                node.x += dx
                node.y += dy
        self._mark_dirty()
        self.canvas.queue_draw()

    def _delete_selected_nodes(self) -> None:
        if not self._diagram or not self._selected:
            return
        self._push_undo(tr("Delete"))
        for nid in list(self._selected):
            self._diagram.remove_node(nid)
        self._selected = set()
        if self._hovered and self._diagram.find_node(self._hovered) is None:
            self._hovered = None
        self._mark_dirty()
        self._update_toolbar_buttons()
        self.canvas.queue_draw()

    def _delete_node(self, node_id: str) -> None:
        if not self._diagram:
            return
        self._push_undo(tr("Delete node"))
        self._diagram.remove_node(node_id)
        self._selected.discard(node_id)
        if self._hovered == node_id:
            self._hovered = None
        self._mark_dirty()
        self._update_toolbar_buttons()
        self.canvas.queue_draw()

    def _on_add_child(self, _btn: object) -> None:
        pid = self._primary_selected()
        if pid:
            self._add_child(pid)

    def _on_delete_selected(self, _btn: object) -> None:
        self._delete_selected_nodes()

    def _update_toolbar_buttons(self) -> None:
        has_sel = len(self._selected) > 0
        self.add_child_btn.set_sensitive(has_sel)
        self.delete_node_btn.set_sensitive(has_sel)
        self.save_btn.set_sensitive(True)
        # Color and shape buttons
        for btn in self._color_btns:
            btn.set_sensitive(has_sel)
        for btn in self._shape_group:
            btn.set_sensitive(has_sel)

    def _update_undo_buttons(self) -> None:
        self.undo_btn.set_sensitive(len(self._undo_stack) > 0)
        self.redo_btn.set_sensitive(len(self._redo_stack) > 0)

    # ── Autosave ─────────────────────────────────────────────────────

    def _mark_dirty(self) -> None:
        """Mark diagram dirty and (re)schedule autosave after 2s of inactivity."""
        self._dirty = True
        if self._autosave_id:
            GLib.source_remove(self._autosave_id)
        self._autosave_id = GLib.timeout_add(2000, self._do_autosave)

    def _do_autosave(self) -> bool:
        """Flush pending changes to disk."""
        self._autosave_id = 0
        if self._dirty and self._diagram:
            self._diagram_manager.save(self._diagram)
            self._dirty = False
        return False

    def _cancel_autosave(self) -> None:
        if self._autosave_id:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = 0

    # ── Save ─────────────────────────────────────────────────────────

    def save_if_dirty(self) -> None:
        """Persist any unsaved changes. Returns True if something was saved."""
        self._cancel_autosave()
        if self._dirty and self._diagram:
            self._diagram_manager.save(self._diagram)
            self._dirty = False

    def _on_save_and_insert_clicked(self, _btn: object) -> None:
        if not self._diagram:
            return
        self._cancel_autosave()
        self._diagram_manager.save(self._diagram)
        self._dirty = False
        self._on_save_and_insert(self._diagram)

    # ── Undo / Redo ──────────────────────────────────────────────────

    def _push_undo(self, description: str = "") -> None:
        if self._diagram is None:
            return
        self._redo_stack.clear()
        self._undo_stack.append((description, self._diagram.copy()))
        # Keep stack bounded
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._update_undo_buttons()

    def _undo(self, _btn: object = None) -> None:
        if not self._undo_stack or self._diagram is None:
            return
        desc, prev = self._undo_stack.pop()
        self._redo_stack.append((desc, self._diagram.copy()))
        self._diagram.nodes = prev.nodes
        self._diagram.edges = prev.edges
        self._diagram.title = prev.title
        self._mark_dirty()
        self._update_undo_buttons()
        self._update_toolbar_buttons()
        self.canvas.queue_draw()

    def _redo(self, _btn: object = None) -> None:
        if not self._redo_stack or self._diagram is None:
            return
        desc, next_state = self._redo_stack.pop()
        self._undo_stack.append((desc, self._diagram.copy()))
        self._diagram.nodes = next_state.nodes
        self._diagram.edges = next_state.edges
        self._diagram.title = next_state.title
        self._mark_dirty()
        self._update_undo_buttons()
        self._update_toolbar_buttons()
        self.canvas.queue_draw()

    # ── Clipboard: copy / paste / duplicate ─────────────────────────

    def _copy_selected(self) -> None:
        if not self._diagram or not self._selected:
            return
        self._clipboard.clear()
        selected_ids = set(self._selected)
        # Find centre of selection for offset
        sel_nodes = [n for n in self._diagram.nodes if n.id in selected_ids]
        if not sel_nodes:
            return
        cx_sel = sum(n.x for n in sel_nodes) / len(sel_nodes)
        cy_sel = sum(n.y for n in sel_nodes) / len(sel_nodes)
        self._clipboard_edges.clear()
        for n in sel_nodes:
            self._clipboard.append(
                {
                    "_orig_id": n.id,
                    "text": n.text,
                    "x": n.x - cx_sel,
                    "y": n.y - cy_sel,
                    "w": n.w,
                    "h": n.h,
                    "color": n.color,
                    "shape": n.shape,
                }
            )
        self._clipboard_edges = [
            (e.from_id, e.to_id, e.edge_type, e.label)
            for e in self._diagram.edges
            if e.from_id in selected_ids and e.to_id in selected_ids
        ]

    def _paste(self, dx: float = 0.0, dy: float = 0.0) -> None:
        if not self._diagram or not self._clipboard:
            return
        self._push_undo(tr("Paste"))
        cw = self.canvas.get_width()
        ch = self.canvas.get_height()
        gx, gy = self._canvas_to_graph(cw / 2 + dx, ch / 2 + dy, cw, ch)
        id_map: dict[str, str] = {}
        new_ids: list[str] = []
        for ndata in self._clipboard:
            nid = DiagramNode.new(ndata["text"], gx + ndata["x"], gy + ndata["y"]).id
            node = DiagramNode(
                id=nid,
                text=ndata["text"],
                x=gx + ndata["x"],
                y=gy + ndata["y"],
                w=ndata["w"],
                h=ndata["h"],
                color=ndata["color"],
                shape=ndata.get("shape", "pill"),
            )
            self._diagram.nodes.append(node)
            new_ids.append(nid)
        for from_id, to_id, etype, elabel in self._clipboard_edges:
            src = id_map.get(from_id)
            dst = id_map.get(to_id)
            if src and dst:
                edge = DiagramEdge.new(src, dst)
                edge.edge_type = etype
                edge.label = elabel
                self._diagram.edges.append(edge)
        self._selected = set(new_ids)
        self._mark_dirty()
        self._update_toolbar_buttons()
        self.canvas.queue_draw()

    def _duplicate_selected(self) -> None:
        if not self._diagram or not self._selected:
            return
        self._copy_selected()
        self._paste(dx=60.0, dy=60.0)

    # ── Edge operations ──────────────────────────────────────────────

    def _apply_edge_type(self, edge_id: str, edge_type: str) -> None:
        if not self._diagram:
            return
        self._push_undo(tr("Change edge type"))
        for e in self._diagram.edges:
            if e.id == edge_id:
                e.edge_type = edge_type
                break
        self._mark_dirty()
        self.canvas.queue_draw()

    def _edit_edge_label(self, edge_id: str) -> None:
        if not self._diagram:
            return
        edge = next((e for e in self._diagram.edges if e.id == edge_id), None)
        if not edge:
            return

        logger.debug("Opening edge label dialog for edge=%s", edge_id)
        self._show_text_dialog(
            tr("Edge Label"),
            edge.label,
            lambda label: self._set_edge_label(edge_id, label),
        )

    def _set_edge_label(self, edge_id: str, label: str) -> None:
        if not self._diagram:
            return
        logger.debug("_set_edge_label edge=%s label=%r", edge_id, label)
        self._push_undo(tr("Edge label"))
        for e in self._diagram.edges:
            if e.id == edge_id:
                e.label = label
                break
        self._mark_dirty()
        self.canvas.queue_draw()

    def _remove_edge_label(self, edge_id: str) -> None:
        self._set_edge_label(edge_id, "")

    # ── Style: color & shape ─────────────────────────────────────────

    def _apply_color(self, hex_color: str) -> None:
        if not self._diagram or not self._selected:
            return
        self._push_undo(tr("Change color"))
        for nid in self._selected:
            node = self._diagram.find_node(nid)
            if node:
                node.color = hex_color
        self._mark_dirty()
        self._update_style_buttons()
        self.canvas.queue_draw()

    def _on_color_picked(self, btn: Gtk.ToggleButton, hex_color: str) -> None:
        self._apply_color(hex_color)

    def _apply_shape(self, shape: str) -> None:
        if not self._diagram or not self._selected:
            return
        self._push_undo(tr("Change shape"))
        for nid in self._selected:
            node = self._diagram.find_node(nid)
            if node:
                node.shape = shape
        self._mark_dirty()
        self._update_style_buttons()
        self.canvas.queue_draw()

    def _on_shape_picked(self, btn: Gtk.ToggleButton, shape: str) -> None:
        self._apply_shape(shape)

    def _update_style_buttons(self) -> None:
        """Update color/shape toggle state to match primary selected node."""
        nid = self._primary_selected()
        if nid and self._diagram:
            node = self._diagram.find_node(nid)
            if node:
                for i, (hc, _) in enumerate(_get_color_swatches()):
                    active = hc.lower() == node.color.lower()
                    if i < len(self._color_btns):
                        self._color_btns[i].set_active(active)
                        self._color_btns[i].set_sensitive(True)
                for i, (sv, _) in enumerate(_get_shapes()):
                    active = sv == node.shape
                    if i < len(self._shape_group):
                        self._shape_group[i].set_active(active)
                        self._shape_group[i].set_sensitive(True)
                return
        for btn in self._color_btns:
            btn.set_active(False)
        for btn in self._shape_group:
            btn.set_active(False)

    # ── Diagram list & delete ────────────────────────────────────────

    def _show_diagram_list(self, _btn: object) -> None:
        """Show a popover listing all diagrams for open/delete."""
        titles = self._diagram_manager.list_titles()
        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_position(Gtk.PositionType.BOTTOM)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_margin_top(4)
        vbox.set_margin_bottom(4)

        if not titles:
            lbl = Gtk.Label(label=tr("No diagrams yet"))
            lbl.set_margin_start(12)
            lbl.set_margin_end(12)
            lbl.add_css_class("dim-label")
            vbox.append(lbl)
        else:
            for did, dtitle in titles:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                row.set_margin_start(6)
                row.set_margin_end(6)
                row.set_margin_top(2)
                row.set_margin_bottom(2)
                lbl = Gtk.Label(label=dtitle)
                lbl.set_hexpand(True)
                lbl.set_halign(Gtk.Align.START)
                row.append(lbl)

                open_btn = Gtk.Button(label=tr("Open"))
                open_btn.add_css_class("pill")
                open_btn.connect(
                    "clicked",
                    lambda *_, d=did: (
                        self._open_diagram(d),
                        popover.popdown(),
                    ),
                )
                row.append(open_btn)

                rename_btn = Gtk.Button(label=tr("Rename"))
                rename_btn.add_css_class("pill")
                rename_btn.connect(
                    "clicked",
                    lambda *_, d=did: (
                        self._rename_diagram(d),
                        popover.popdown(),
                    ),
                )
                row.append(rename_btn)

                del_btn = Gtk.Button(label=tr("Delete"))
                del_btn.add_css_class("pill")
                del_btn.add_css_class("destructive-action")
                del_btn.connect(
                    "clicked",
                    lambda *_, d=did: (
                        self._delete_diagram(d),
                        popover.popdown(),
                    ),
                )
                row.append(del_btn)

                vbox.append(row)

        popover.set_child(vbox)
        popover.set_parent(self.diagrams_btn)
        popover.connect("closed", lambda p, *_: GLib.idle_add(p.unparent))
        popover.popup()

    def _open_diagram(self, diagram_id: str) -> None:
        diagram = self._diagram_manager.load(diagram_id)
        if diagram:
            self.set_diagram(diagram)

    def _rename_diagram(self, diagram_id: str) -> None:
        diagram = self._diagram_manager.load(diagram_id)
        if not diagram:
            return
        self._show_text_dialog(
            tr("Rename Diagram"),
            diagram.title,
            lambda new_title: self._apply_rename_diagram(diagram_id, new_title),
        )

    def _apply_rename_diagram(self, diagram_id: str, new_title: str) -> None:
        if not self._diagram_manager:
            return
        diagram = self._diagram_manager.load(diagram_id)
        if not diagram:
            return
        diagram.title = new_title
        self._diagram_manager.save(diagram)
        if self._diagram and self._diagram.id == diagram_id and self._on_title_changed:
            self._on_title_changed(new_title)

    def _delete_diagram(self, diagram_id: str) -> None:
        diagram = (
            self._diagram_manager.load(diagram_id) if self._diagram_manager else None
        )
        title = diagram.title if diagram else tr("Untitled")
        dialog = Adw.MessageDialog(
            transient_for=self._transient_for,
            heading=tr("Delete diagram?"),
            body=tr('Delete "%s"? This cannot be undone.') % title,
        )
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("delete", tr("Delete"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.add_css_class("destructive-action")

        def on_response(d: Adw.MessageDialog, response: str) -> None:
            if response == "delete":
                self._do_delete_diagram(diagram_id)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _do_delete_diagram(self, diagram_id: str) -> None:
        if self._on_diagram_delete:
            self._on_diagram_delete(diagram_id)
        elif self._diagram_manager:
            self._diagram_manager.delete(diagram_id)
        if self._diagram and self._diagram.id == diagram_id:
            self._on_close()

    # ── Export ───────────────────────────────────────────────────────

    def _export_png(self, _btn: object = None) -> None:
        """Export the current diagram as a PNG file."""
        if not self._diagram:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title(tr("Export Diagram as PNG"))
        dialog.set_initial_name(f"{self._diagram.title}.png")

        dialog.save(self.get_root(), None, self._on_export_dialog_response)

    def _on_export_dialog_response(self, dialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file or not self._diagram:
            return
        path = file.get_path()
        if not path:
            return
        ctx = self.canvas.get_style_context()
        ok_bg, bg_color = ctx.lookup_color("editor_bg")
        if not ok_bg:
            ok_bg, bg_color = ctx.lookup_color("theme_base_color")
        if not ok_bg:
            bg_color = Gdk.RGBA()
            bg_color.parse("#ffffff")
        ok_fg, fg_color = ctx.lookup_color("theme_fg_color")
        if not ok_fg:
            fg_color = Gdk.RGBA()
            fg_color.parse("#ffffff")

        pixbuf = render_diagram_preview(
            self._diagram,
            max_width=4000,
            max_height=4000,
            bg_color=bg_color,
            text_color=fg_color,
        )
        if pixbuf:
            pixbuf.savev(str(path), "png", [], [])
