import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango, GLib, Gdk
import re
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
        self.controller = Gtk.EventControllerKey()
        self.controller.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.controller.connect("key-pressed", self.on_key_pressed)
        self.text_view.add_controller(self.controller)
        
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

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in [Gdk.KEY_Return, Gdk.KEY_KP_Enter]:
            buffer = self.text_view.get_buffer()
            insert_mark = buffer.get_insert()
            iter = buffer.get_iter_at_mark(insert_mark)
            
            # Get current line text up to cursor
            line_start = iter.copy()
            line_start.set_line(iter.get_line())
            line_end = line_start.copy()
            line_end.forward_to_line_end()
            line_text = buffer.get_text(line_start, iter, False)

            # Check for patterns (Pattern, Type)
            patterns = [
                (r'^(\s*-\s*\[[ xX]\])(.*)$', "task"),
                (r'^(\s*[-*+])\s+', "list"),
                (r'^(\s*\d+\.)\s+', "ordered")
            ]
            
            for pattern, p_type in patterns:
                match = re.match(pattern, line_text)
                if match:
                    # Capture the full prefix including spaces and marker
                    prefix_match = re.match(r'^(\s*-\s*\[[ xX]\])', line_text)
                    if prefix_match:
                        marker_only = prefix_match.group(1)
                    else:
                        marker_only = match.group(1)

                    # If line is empty (contains only the marker), break the list
                    if len(line_text.strip()) == len(marker_only.strip()):
                        buffer.delete(line_start, line_end)
                        return False 

                    # Otherwise, continue the list
                    if p_type == "task":
                        # Always use a clean unchecked marker
                        new_prefix = re.sub(r'\[[xX ]\]', '[ ]', marker_only) + " "
                    elif p_type == "ordered":
                        # Increment the number
                        num_match = re.search(r'(\d+)', marker_only)
                        if num_match:
                            num = int(num_match.group(1))
                            new_prefix = marker_only.replace(str(num), str(num + 1), 1) + " "
                        else:
                            new_prefix = marker_only.rstrip() + " "
                    else:
                        new_prefix = marker_only.rstrip() + " "
                    
                    GLib.idle_add(lambda p=new_prefix: buffer.insert_at_cursor("\n" + p))
                    return True # Prevent default to avoid extra newline
        return False

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
