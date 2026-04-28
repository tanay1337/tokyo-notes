import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

class LinkPicker(Gtk.Popover):
    def __init__(self, notes, on_selected):
        super().__init__()
        self.add_css_class("link-picker")
        self.notes = notes
        self.on_selected = on_selected
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_size_request(200, 300)
        
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.connect("search-changed", self.on_search_changed)
        box.append(self.search_entry)
        
        self.list_box = Gtk.ListBox()
        self.list_box.connect("row-activated", self.on_row_activated)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_box)
        scrolled.set_vexpand(True)
        box.append(scrolled)
        
        self.set_child(box)
        self.populate_list(notes)

    def populate_list(self, notes):
        while (child := self.list_box.get_first_child()):
            self.list_box.remove(child)
        for note in notes:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=note, xalign=0)
            label.add_css_class("sidebar-label")
            row.set_child(label)
            row.note_name = note
            self.list_box.append(row)

    def on_search_changed(self, entry):
        text = entry.get_text().lower()
        filtered = [n for n in self.notes if text in n.lower()]
        self.populate_list(filtered)

    def on_row_activated(self, listbox, row):
        if row:
            self.on_selected(row.note_name)
            self.popdown()
