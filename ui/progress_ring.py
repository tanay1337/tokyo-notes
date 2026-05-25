"""Animated progress ring widget for dashboard date headers."""

from __future__ import annotations

import math
from typing import Any

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class ProgressRing(Gtk.DrawingArea):
    """Circular progress indicator with subtle animation and counter text."""

    def __init__(self, size: int = 32, line_width: int = 3) -> None:
        super().__init__()
        self._size = size
        self._line_width = line_width
        self._completed = 0
        self._total = 0
        self._target_ratio = 0.0
        self._current_ratio = 0.0
        self._start_ratio: float = 0.0
        self._animating = False
        self._tick_id: int | None = None
        self.set_size_request(size, size)
        self.set_draw_func(self.on_draw)

    def set_progress(self, completed: int, total: int, animate: bool = True) -> None:
        """Set progress and animate from current ratio to target."""
        self._completed = completed
        self._total = total
        self._target_ratio = completed / total if total > 0 else 0.0

        if not animate or self._current_ratio == self._target_ratio:
            self._current_ratio = self._target_ratio
            self.queue_draw()
            return

        self._start_ratio = self._current_ratio
        self._animating = True
        self._start_time: float | None = None
        if self._tick_id is not None:
            self.remove_tick_callback(self._tick_id)
        self._tick_id = self.add_tick_callback(self._on_tick)

    def _on_tick(self, widget: Gtk.Widget, frame_clock: Any) -> bool:
        """Animation tick — ease-out from current to target ratio."""
        t = frame_clock.get_frame_time() / 1_000_000.0
        if self._start_time is None:
            self._start_time = t

        elapsed = t - self._start_time
        duration = 0.4

        if elapsed >= duration:
            self._current_ratio = self._target_ratio
            self._animating = False
            self._tick_id = None
            self.queue_draw()
            return False

        progress = elapsed / duration
        eased = 1.0 - (1.0 - progress) ** 3
        self._current_ratio = (
            self._start_ratio + (self._target_ratio - self._start_ratio) * eased
        )
        self.queue_draw()
        return True

    def on_draw(
        self, area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int
    ) -> None:
        """Draw the progress ring with counter text inside."""
        if self._total == 0:
            return

        cx = width / 2.0
        cy = height / 2.0
        radius = (min(width, height) - self._line_width) / 2.0

        cr.set_line_width(self._line_width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgba(0.47, 0.64, 0.96, 0.15)
        cr.stroke()

        if self._current_ratio > 0:
            start_angle = -math.pi / 2
            end_angle = start_angle + (2 * math.pi * self._current_ratio)
            cr.arc(cx, cy, radius, start_angle, end_angle)
            cr.set_source_rgba(0.47, 0.64, 0.96, 0.9)
            cr.stroke()

        text = f"{self._completed}/{self._total}"
        cr.set_font_size(9)
        extents = cr.text_extents(text)
        x = cx - (extents.width / 2 + extents.x_bearing)
        y = cy - (extents.height / 2 + extents.y_bearing)
        cr.move_to(x, y)
        cr.set_source_rgba(0.47, 0.64, 0.96, 0.8)
        cr.show_text(text)
