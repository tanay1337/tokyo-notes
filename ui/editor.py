"""Markdown editor component with syntax highlighting and image support."""
from __future__ import annotations

import re
from typing import Any, Callable, TYPE_CHECKING

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from pathlib import Path
from ui.deadline_picker import DeadlinePicker
from ui.link_picker import LinkPicker

_CONTINUATION_PATTERNS: list[tuple[str, str]] = [
    (r'^(\s*-\s*\[[ xX]\])(.*)$', "task"),
    (r'^(\s*[-*+])\s+',           "list"),
    (r'^(\s*\d+\.)\s+',           "ordered"),
]

class Editor(Gtk.Box):
    def __init__(
        self, 
        on_text_changed: Callable[[Gtk.TextBuffer], Any], 
        on_cursor_moved: Callable[[Any, Any], Any], 
        on_paste_clipboard: Callable[[Gtk.TextView], Any], 
        toolbar: Gtk.Box, 
        get_notes_callback: Callable[[], list[str]]
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_notes_callback = get_notes_callback
        
        # Toolbar
        self.toolbar = toolbar
        self.append(self.toolbar)
        
        # Editor
        scrolled_editor = Gtk.ScrolledWindow()
        scrolled_editor.set_vexpand(True)
        self.text_view: Gtk.TextView = Gtk.TextView()
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
        
        self.buffer: Gtk.TextBuffer = self.text_view.get_buffer()
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
        
        self.image_anchors: list[Gtk.TextChildAnchor] = []
        self.is_updating_images: bool = False

    def on_key_pressed(self, controller: Gtk.EventControllerKey, keyval: int, keycode: int, state: Gdk.ModifierType) -> bool:
        """Handles list continuation on Enter key."""
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

            for pattern, p_type in _CONTINUATION_PATTERNS:
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
                        new_prefix = re.sub(
                            r'(\d+)',
                            lambda m: str(int(m.group(1)) + 1),
                            marker_only,
                            count=1
                        ) + " "
                    else:
                        new_prefix = marker_only.rstrip() + " "
                    
                    GLib.idle_add(lambda p=new_prefix: buffer.insert_at_cursor("\n" + p))
                    return True # Prevent default to avoid extra newline
        return False

    def on_insert_text(self, buffer: Gtk.TextBuffer, location: Gtk.TextIter, text: str, length: int) -> None:
        """Triggers pickers for shortcuts."""
        if text == '@':
            GLib.idle_add(self.show_deadline_picker)
        elif text == '[':
            # Check for '[['
            iter = buffer.get_iter_at_offset(location.get_offset() - 1)
            if iter.get_char() == '[':
                GLib.idle_add(self.show_link_picker)

    def show_link_picker(self) -> None:
        """Shows the link selection popover."""
        def on_selected(note_name: str) -> None:
            self.buffer.insert_at_cursor(f"{note_name}]]")
            
        notes = self.get_notes_callback()
        picker = LinkPicker(notes, on_selected)
        picker.set_parent(self.text_view)
        
        # Position
        iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        rect = self.text_view.get_iter_location(iter)
        picker.set_pointing_to(rect)
        picker.popup()

    def show_deadline_picker(self) -> None:
        """Shows the deadline selection popover."""
        picker = DeadlinePicker(self.on_deadline_selected)
        picker.set_parent(self.text_view)
        
        # Calculate cursor position to place the popover at the cursor
        iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        rect = self.text_view.get_iter_location(iter)
        picker.set_pointing_to(rect)
        
        picker.popup()

    def update_images(self, note_dir: Path) -> None:
        """Updates embedded images."""
        if self.is_updating_images:
            return
        
        self.is_updating_images = True
        
        # Block the changed handler while modifying the buffer to prevent recursion
        self.buffer.handler_block(self.changed_handler_id)
        
        try:
            # 1. Clear existing anchors by deleting the \ufffc characters
            start, end = self.buffer.get_bounds()
            text = self.buffer.get_text(start, end, True)
            for i in range(len(text) - 1, -1, -1):
                if text[i] == '\ufffc':
                    it_start = self.buffer.get_iter_at_offset(i)
                    it_end = it_start.copy()
                    it_end.forward_char()
                    self.buffer.delete(it_start, it_end)
            
            self.image_anchors.clear()
            
            # Re-get text after clearing
            start, end = self.buffer.get_bounds()
            text = self.buffer.get_text(start, end, True)
            
            # 2. Find and insert images
            matches = list(re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', text))
            
            # Iterate in reverse to keep offsets valid
            for match in reversed(matches):
                url = match.group(2)
                match_end = match.end()
                
                # Insert anchor at the end of the markdown syntax
                anchor_iter = self.buffer.get_iter_at_offset(match_end)
                anchor = self.buffer.create_child_anchor(anchor_iter)
                self.image_anchors.append(anchor)
                
                if url.startswith('http://') or url.startswith('https://'):
                    # Remote image with async loading (intentional positional-arg passing)
                    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                    widget.add_css_class("image-container")
                    
                    img = Gtk.Image.new_from_icon_name("image-loading-symbolic")
                    img.set_pixel_size(64)
                    widget.append(img)
                    
                    label = Gtk.Label(label="Loading...")
                    label.add_css_class("image-caption")
                    widget.append(label)
                    
                    def on_image_loaded(file: Gio.File, result: Gio.AsyncResult, img_widget: Gtk.Image, label_widget: Gtk.Label) -> None:
                        try:
                            success, contents, etag = file.load_contents_finish(result)
                            if success:
                                # Create a stream from the bytes
                                bytes_obj = GLib.Bytes.new(contents)
                                stream = Gio.MemoryInputStream.new_from_bytes(bytes_obj)
                                texture = Gdk.Texture.new_from_stream(stream)
                                img_widget.set_from_paintable(texture)
                                img_widget.set_pixel_size(500)
                                label_widget.set_label("")
                                label_widget.set_visible(False)
                        except Exception:
                            img_widget.set_from_icon_name("image-missing-symbolic")
                            label_widget.set_label("Failed to load")

                    remote_file = Gio.File.new_for_uri(url)
                    remote_file.load_contents_async(None, on_image_loaded, img, label)
                    
                    widget.set_size_request(400, -1)
                else:
                    # Resolve local path
                    local_path = Path(url)
                    if not local_path.is_absolute():
                        local_path = (note_dir / url).resolve()
                    
                    if local_path.exists() and local_path.is_file():
                        try:
                            # Use Gtk.Image for simpler rendering
                            widget = Gtk.Image.new_from_file(str(local_path))
                            # Set a reasonable size
                            widget.set_pixel_size(500)
                            widget.set_margin_top(10)
                            widget.set_margin_bottom(10)
                        except Exception:
                            widget = Gtk.Label(label=f"Error: {url}")
                            widget.add_css_class("image-error")
                    else:
                        widget = Gtk.Label(label=f"Not Found: {url}")
                        widget.add_css_class("image-error")
                
                self.text_view.add_child_at_anchor(widget, anchor)
                
        finally:
            self.buffer.handler_unblock(self.changed_handler_id)
            self.is_updating_images = False

    def on_deadline_selected(self, deadline: str) -> None:
        """Inserts deadline string."""
        self.buffer.insert_at_cursor(deadline)
