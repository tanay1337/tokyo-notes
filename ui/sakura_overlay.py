"""Sakura petal animation overlay."""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

if TYPE_CHECKING:
    import cairo

_PETAL_COLORS: tuple[tuple[float, float, float], ...] = (
    (1.0, 0.75, 0.8),   # Pink
    (1.0, 0.71, 0.75),  # LightPink
    (1.0, 0.8, 0.85),   # Lighter
    (0.98, 0.85, 0.87), # MistyRose-ish
)

class SakuraPetal:
    def __init__(self, width: int, height: int) -> None:
        self.x: float = 0.0
        self.y: float = 0.0
        self.size: float = 0.0
        self.speed_y: float = 0.0
        self.speed_x: float = 0.0
        self.oscillation_speed: float = 0.0
        self.oscillation_amplitude: float = 0.0
        self.oscillation_offset: float = 0.0
        self.rotation: float = 0.0
        self.rotation_speed: float = 0.0
        self.color: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.opacity: float = 0.0
        self.reset(width, height, initial=True)

    def reset(self, width: int, height: int, initial: bool = False) -> None:
        """Resets petal state."""
        self.x = random.uniform(0, float(width))
        if initial:
            self.y = random.uniform(-float(height), 0.0)
        else:
            self.y = random.uniform(-50.0, -10.0)
        
        self.size = random.uniform(8.0, 14.0)
        self.speed_y = random.uniform(1.5, 3.5)
        self.speed_x = random.uniform(-1.0, 1.0)
        self.oscillation_speed = random.uniform(0.02, 0.05)
        self.oscillation_amplitude = random.uniform(10.0, 30.0)
        self.oscillation_offset = random.uniform(0.0, 2 * math.pi)
        
        self.rotation = random.uniform(0.0, 2 * math.pi)
        self.rotation_speed = random.uniform(-0.05, 0.05)
        
        self.color = random.choice(_PETAL_COLORS)
        self.opacity = random.uniform(0.6, 0.9)

    def update(self, width: int, height: int, t: float) -> bool:
        """Updates petal position and rotation."""
        self.y += self.speed_y
        self.x += self.speed_x + math.sin(t * self.oscillation_speed + self.oscillation_offset) * 0.5
        self.rotation += self.rotation_speed
        
        return self.y <= height + 20

class SakuraOverlay(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_target(False) # Don't block clicks
        
        self.petals: list[SakuraPetal] = []
        self.is_animating: bool = False
        self.start_time: float | None = None
        self.duration: float = 4.0 # seconds
        
        self.set_draw_func(self.on_draw)

    def start_celebration(self) -> None:
        """Triggers the petal animation."""
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            width, height = 1000, 700 # Fallback
            
        self.petals = [SakuraPetal(width, height) for _ in range(40)]
        self.start_time = None
        
        if not self.is_animating:
            self.is_animating = True
            self.add_tick_callback(self.on_tick)

    def on_tick(self, widget: Gtk.Widget, frame_clock: Any) -> bool:
        """Animation tick callback."""
        if not self.is_animating:
            return False
        
        t = frame_clock.get_frame_time() / 1_000_000.0  # → seconds
        if self.start_time is None:
            self.start_time = t
            
        width = self.get_width()
        height = self.get_height()
        
        # Update petals
        self.petals = [p for p in self.petals if p.update(width, height, t)]
        
        # Check if duration is up and no more petals
        if t - self.start_time > self.duration and not self.petals:
            self.is_animating = False
            self.queue_draw()
            return False
            
        self.queue_draw()
        return True

    def on_draw(self, area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int) -> None:
        """Draws petals on canvas."""
        if not self.petals:
            return
            
        for p in self.petals:
            cr.save()
            cr.translate(p.x, p.y)
            cr.rotate(p.rotation)
            
            # Draw a petal shape (elongated ellipse/heart-ish)
            cr.set_source_rgba(p.color[0], p.color[1], p.color[2], p.opacity)
            
            # Simple petal: two arcs
            cr.move_to(0, 0)
            cr.curve_to(-p.size/2, -p.size/2, -p.size/2, p.size/2, 0, p.size)
            cr.curve_to(p.size/2, p.size/2, p.size/2, -p.size/2, 0, 0)
            
            cr.fill()
            cr.restore()
