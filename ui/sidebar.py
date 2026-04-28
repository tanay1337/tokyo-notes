import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

class Sidebar(Gtk.Box):
    def __init__(self, on_new_note, on_select_folder, on_search_changed, on_dashboard_clicked, on_archive_clicked, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("sidebar")
        
        # Header
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label="Tokyo Notes"))
        
        new_note_btn = Gtk.Button(icon_name="document-new-symbolic")
        new_note_btn.connect("clicked", on_new_note)
        sidebar_header.pack_start(new_note_btn)
        
        self.folder_btn = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Select Notes Folder")
        self.folder_btn.connect("clicked", on_select_folder)
        sidebar_header.pack_end(self.folder_btn)
        
        self.append(sidebar_header)
        
        # Search
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search notes...")
        self.search_entry.connect("search-changed", on_search_changed)
        self.append(self.search_entry)
        
        # Stack for Lists
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        
        self.main_list = Gtk.ListBox()
        self.stack.add_named(self.main_list, "main")
        
        self.archive_list = Gtk.ListBox()
        self.stack.add_named(self.archive_list, "archive")
        
        scrolled_list = Gtk.ScrolledWindow()
        scrolled_list.set_child(self.stack)
        self.append(scrolled_list)
        
        # Footer
        footer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        footer_box.set_margin_start(10)
        footer_box.set_margin_end(10)
        footer_box.set_margin_top(10)
        footer_box.set_margin_bottom(10)

        # Archived Link
        self.archived_nav_btn = Gtk.Button(label="Archived Notes")
        self.archived_nav_btn.add_css_class("archived-nav-btn")
        self.archived_nav_btn.connect("clicked", on_archive_clicked)
        self.archived_nav_btn.set_visible(False)
        footer_box.append(self.archived_nav_btn)
        
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dashboard_btn = Gtk.Button(label="Dashboard")
        dashboard_btn.set_hexpand(True)
        dashboard_btn.connect("clicked", on_dashboard_clicked)
        dashboard_btn.add_css_class("dashboard-footer-btn")
        buttons_box.append(dashboard_btn)
        
        graph_btn = Gtk.Button(label="Graph")
        graph_btn.set_hexpand(True)
        graph_btn.connect("clicked", lambda b: app.on_graph_clicked())
        graph_btn.add_css_class("dashboard-footer-btn")
        buttons_box.append(graph_btn)
        footer_box.append(buttons_box)
        
        self.append(footer_box)
