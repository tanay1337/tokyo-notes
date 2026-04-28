import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk
import math

class GraphView(Gtk.Box):
    def __init__(self, graph_data, on_node_clicked):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.graph_data = graph_data
        self.on_node_clicked = on_node_clicked
        self.nodes = list(graph_data.keys())
        
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_draw_func(self.on_draw)
        self.canvas.set_size_request(600, 600)
        
        self.append(self.canvas)
        
        gesture = Gtk.GestureClick.new()
        gesture.connect("pressed", self.on_press)
        self.canvas.add_controller(gesture)

    def update_data(self, new_data):
        self.graph_data = new_data
        self.nodes = list(new_data.keys())
        self.canvas.queue_draw()

    def get_node_coords(self, index, total, width, height):
        center_x, center_y = width / 2, height / 2
        radius = min(width, height) / 3
        angle = (2 * math.pi * index) / total
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        return x, y

    def on_draw(self, area, cr, width, height):
        total = len(self.nodes)
        if total == 0: return
        
        # Get theme colors
        context = area.get_style_context()
        
        # Accent color for nodes and arrows
        success, accent_color = context.lookup_color("accent_color")
        if not success:
            accent_color = Gdk.RGBA()
            accent_color.parse("rgb(122, 162, 247)") # fallback to tokyo blue

        # Foreground color for labels
        success, fg_color = context.lookup_color("fg_color")
        if not success:
            fg_color = Gdk.RGBA()
            fg_color.parse("rgb(169, 177, 214)") # fallback to tokyo fg

        node_positions = {node: self.get_node_coords(i, total, width, height) for i, node in enumerate(self.nodes)}
        
        # Draw edges
        cr.set_source_rgba(fg_color.red, fg_color.green, fg_color.blue, 0.3)
        cr.set_line_width(1.5)
        for node, targets in self.graph_data.items():
            if node not in node_positions: continue
            x1, y1 = node_positions[node]
            for target in targets:
                if target not in node_positions: continue
                x2, y2 = node_positions[target]
                
                # Draw the main line
                cr.move_to(x1, y1)
                cr.line_to(x2, y2)
                cr.stroke()
                
                # Calculate arrow head
                angle = math.atan2(y2 - y1, x2 - x1)
                # Backup tip of arrow from the node circle (node radius is 10)
                arrow_x = x2 - 12 * math.cos(angle)
                arrow_y = y2 - 12 * math.sin(angle)
                
                # Arrow head size and spread
                head_len = 10
                head_angle = math.pi / 6 # 30 degrees
                
                cr.move_to(arrow_x, arrow_y)
                cr.line_to(arrow_x - head_len * math.cos(angle - head_angle),
                          arrow_y - head_len * math.sin(angle - head_angle))
                cr.move_to(arrow_x, arrow_y)
                cr.line_to(arrow_x - head_len * math.cos(angle + head_angle),
                          arrow_y - head_len * math.sin(angle + head_angle))
                cr.stroke()
        
        # Draw nodes
        for node, (x, y) in node_positions.items():
            cr.set_source_rgb(accent_color.red, accent_color.green, accent_color.blue)
            cr.arc(x, y, 10, 0, 2 * math.pi)
            cr.fill()
            
            cr.set_source_rgb(fg_color.red, fg_color.green, fg_color.blue)
            cr.move_to(x + 14, y + 4)
            cr.show_text(node)

    def on_press(self, gesture, n_press, x, y):
        total = len(self.nodes)
        width = self.canvas.get_width()
        height = self.canvas.get_height()
        node_positions = {node: self.get_node_coords(i, total, width, height) for i, node in enumerate(self.nodes)}
        for node, (nx, ny) in node_positions.items():
            if math.hypot(nx - x, ny - y) < 20:
                self.on_node_clicked(node)
                return
