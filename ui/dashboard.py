import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

class Dashboard(Gtk.Box):
    def __init__(self, on_item_selected, refresh_callback, default_filter="today"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("dashboard-view")
        self.refresh_callback = refresh_callback
        
        # Filter Bar
        self.filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.filter_box.add_css_class("toolbar")
        self.filter_box.set_halign(Gtk.Align.CENTER)
        
        self.buttons = {}
        for label in ["Today", "Week", "All"]:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", self.on_filter_clicked, label.lower())
            self.filter_box.append(btn)
            self.buttons[label.lower()] = btn
            
        self.append(self.filter_box)
        
        # Initialize active button
        self.update_active_filter(default_filter)
        
        scrolled_dashboard = Gtk.ScrolledWindow()
        scrolled_dashboard.set_vexpand(True)
        scrolled_dashboard.set_hexpand(True)
        
        self.dashboard_list = Gtk.ListBox()
        self.dashboard_list.connect("row-selected", on_item_selected)
        scrolled_dashboard.set_child(self.dashboard_list)
        
        self.append(scrolled_dashboard)

    def on_filter_clicked(self, btn, filter_type):
        self.update_active_filter(filter_type)
        self.refresh_callback(filter_type)

    def update_active_filter(self, active_type):
        for f_type, btn in self.buttons.items():
            if f_type == active_type:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")
