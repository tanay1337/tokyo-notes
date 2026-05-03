import gi
import random
import math
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, GObject

class SakuraPetal:
    def __init__(self, width, height):
        self.reset(width, height, initial=True)

    def reset(self, width, height, initial=False):
        self.x = random.uniform(0, width)
        if initial:
            self.y = random.uniform(-height, 0)
        else:
            self.y = random.uniform(-50, -10)
        
        self.size = random.uniform(8, 14)
        self.speed_y = random.uniform(1.5, 3.5)
        self.speed_x = random.uniform(-1, 1)
        self.oscillation_speed = random.uniform(0.02, 0.05)
        self.oscillation_amplitude = random.uniform(10, 30)
        self.oscillation_offset = random.uniform(0, 2 * math.pi)
        
        self.rotation = random.uniform(0, 2 * math.pi)
        self.rotation_speed = random.uniform(-0.05, 0.05)
        
        # Soft pink colors
        pinks = [
            (1.0, 0.75, 0.8),  # Pink
            (1.0, 0.71, 0.75), # LightPink
            (1.0, 0.8, 0.85),  # Even lighter
            (0.98, 0.85, 0.87) # MistyRose-ish
        ]
        self.color = random.choice(pinks)
        self.opacity = random.uniform(0.6, 0.9)

    def update(self, width, height, t):
        self.y += self.speed_y
        self.x += self.speed_x + math.sin(t * self.oscillation_speed + self.oscillation_offset) * 0.5
        self.rotation += self.rotation_speed
        
        return self.y <= height + 20

class SakuraOverlay(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_target(False) # Don't block clicks
        
        self.petals = []
        self.is_animating = False
        self.start_time = None
        self.duration = 4.0 # seconds
        
        self.set_draw_func(self.on_draw)

    def start_celebration(self):
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            width, height = 1000, 700 # Fallback
            
        self.petals = [SakuraPetal(width, height) for _ in range(40)]
        self.start_time = None
        
        if not self.is_animating:
            self.is_animating = True
            self.add_tick_callback(self.on_tick)

    def on_tick(self, widget, frame_clock):
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

    def on_draw(self, area, cr, width, height):
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
