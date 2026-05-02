import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
from pathlib import Path
from core.utils import create_empty_state_widget

_PIN_ICON_NAME: str | None = None

def _get_pin_icon_name() -> str:
    global _PIN_ICON_NAME
    if _PIN_ICON_NAME is None:
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        _PIN_ICON_NAME = "pin-symbolic" if theme.has_icon("pin-symbolic") else "view-pin-symbolic"
    return _PIN_ICON_NAME

class Sidebar(Gtk.Box):
    def __init__(self, on_new_note, on_search_changed, on_dashboard_clicked, on_archive_clicked, on_graph_clicked):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("sidebar")
        
        # Header
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Gtk.Label(label="Tokyo Notes"))
        
        new_note_btn = Gtk.Button(icon_name="document-new-symbolic")
        new_note_btn.connect("clicked", on_new_note)
        sidebar_header.pack_start(new_note_btn)

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
        graph_btn.connect("clicked", lambda b: on_graph_clicked())
        graph_btn.add_css_class("dashboard-footer-btn")
        buttons_box.append(graph_btn)
        footer_box.append(buttons_box)
        
        self.append(footer_box)

    def populate(self, main_notes: list, pinned: set, archived_notes: list,
                 on_right_click, snippet_fn, base_dir, filter_text=""):
        """Rebuild both list boxes. Caller provides data and callbacks."""
        self._clear(self.main_list)
        self._clear(self.archive_list)

        pinned_notes = [n for n in main_notes if n in pinned]
        other_notes  = [n for n in main_notes if n not in pinned]

        for note in pinned_notes:
            self.main_list.append(
                self._make_row(note, snippet_fn(note), is_pinned=True,
                               on_right_click=on_right_click, base_dir=base_dir))
        for note in other_notes:
            self.main_list.append(
                self._make_row(note, snippet_fn(note), is_pinned=False,
                               on_right_click=on_right_click, base_dir=base_dir))

        if not pinned_notes and not other_notes and filter_text:
            self.main_list.append(create_empty_state_widget("No notes match.", base_dir))

        for note in archived_notes:
            if not filter_text or filter_text.lower() in note.lower():
                self.archive_list.append(
                    self._make_row(note, snippet_fn(note), is_archived=True,
                                   on_right_click=on_right_click, base_dir=base_dir))

        self.archived_nav_btn.set_visible(bool(archived_notes))

    def _clear(self, list_box):
        while (child := list_box.get_first_child()):
            list_box.remove(child)

    def _make_row(self, note_name, snippet_text, is_pinned=False,
                  is_archived=False, on_right_click=None, base_dir=None):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(5)
        box.set_margin_bottom(5)
        
        # Title row
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        label = Gtk.Label(label=note_name, xalign=0)
        label.add_css_class("sidebar-label")
        if is_archived:
            label.add_css_class("muted-label")
        label.set_hexpand(True)
        title_box.append(label)
        
        if is_pinned:
            pin_icon = Gtk.Image()
            pin_icon.set_from_icon_name(_get_pin_icon_name())
            title_box.append(pin_icon)
        box.append(title_box)

        # Snippet
        snippet = Gtk.Label(label=snippet_text, xalign=0)
        snippet.add_css_class("sidebar-snippet")
        box.append(snippet)
        
        row.set_child(box)
        row.note_name = note_name
        
        if on_right_click:
            gesture = Gtk.GestureClick(button=3)
            gesture.connect("pressed", on_right_click, row, is_archived)
            row.add_controller(gesture)
        return row
