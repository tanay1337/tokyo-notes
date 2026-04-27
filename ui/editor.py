import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango, GLib
from ui.deadline_picker import DeadlinePicker

class Editor(Gtk.Box):
    def __init__(self, on_text_changed, on_cursor_moved, on_paste_clipboard, create_toolbar):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        
        # Toolbar
        self.toolbar = create_toolbar()
        self.append(self.toolbar)
        
        # Editor
        scrolled_editor = Gtk.ScrolledWindow()
        scrolled_editor.set_vexpand(True)
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_left_margin(30)
        self.text_view.set_right_margin(80)
        self.text_view.set_top_margin(40)
        self.text_view.set_bottom_margin(40)
        self.text_view.set_can_focus(True)
        self.text_view.set_receives_default(True)
        
        # Clipboard handling
        self.text_view.connect("paste-clipboard", on_paste_clipboard)
        
        # Key handling
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.text_view.add_controller(controller)
        
        self.buffer = self.text_view.get_buffer()
        # Connect key handler directly to buffer if possible or keep on TextView
        self.buffer.connect("insert-text", self.on_insert_text)
        self.changed_handler_id = self.buffer.connect("changed", on_text_changed)
        self.buffer.connect("notify::cursor-position", on_cursor_moved)
        
        scrolled_editor.set_child(self.text_view)
        self.append(scrolled_editor)
        
        # Status Bar
        self.status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.status_bar.add_css_class("status-bar")
        self.status_bar.set_visible(False)
        
        self.stats_label = Gtk.Label(label="Words: 0 | Chars: 0 | Read: 0m")
        self.stats_label.add_css_class("stats-label")
        self.status_bar.append(self.stats_label)
        
        self.append(self.status_bar)

    def on_insert_text(self, buffer, location, text, len):
        if text == '@':
            GLib.idle_add(self.show_deadline_picker)

    def show_deadline_picker(self):
        picker = DeadlinePicker(self.on_deadline_selected)
        picker.set_parent(self.text_view)
        
        # Calculate cursor position to place the popover at the cursor
        iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        rect = self.text_view.get_iter_location(iter)
        picker.set_pointing_to(rect)
        
        picker.popup()

    def on_deadline_selected(self, deadline):
        # The '@' was already inserted by the buffer (before the callback was triggered).
        # We append the date and time.
        self.buffer.insert_at_cursor(f"{deadline}")
