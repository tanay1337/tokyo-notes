import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

class Sidebar(Gtk.Box):
    def __init__(self, on_new_note, on_select_folder, on_search_changed, on_dashboard_clicked):
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
        
        # List
        scrolled_list = Gtk.ScrolledWindow()
        scrolled_list.set_vexpand(True)
        self.note_list = Gtk.ListBox()
        scrolled_list.set_child(self.note_list)
        self.append(scrolled_list)
        
        # Footer
        sidebar_footer = Gtk.Button(label="Dashboard")
        sidebar_footer.connect("clicked", on_dashboard_clicked)
        sidebar_footer.add_css_class("dashboard-footer-btn")
        self.append(sidebar_footer)
