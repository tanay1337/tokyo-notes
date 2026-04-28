import sys
import gi
import re
from datetime import datetime
from pathlib import Path

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Gdk, Gio, GLib, Adw, Pango
import cairo
try:
    from gi.repository import PangoCairo
except ImportError:
    PangoCairo = None

import json
from pathlib import Path

from core.storage import NotesManager
from core.highlighter import MarkdownHighlighter
from core.shortcuts import setup_shortcuts
from core.actions import ActionsHandler
from core.utils import escape_xml, format_markdown_inline
from ui.sidebar import Sidebar
from ui.editor import Editor
from ui.dashboard import Dashboard
from ui.deadline_picker import DeadlinePicker

class TokyoNotes(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id='com.example.TokyoNotes', **kwargs)
        self.actions = ActionsHandler(self)
        
        self.config_dir = Path.home() / ".config" / "tokyo-notes"
        self.config_path = self.config_dir / "tokyo-notes.json"
        self.config = self.load_config()
        self.notes_folder = self.config.get('notes_folder', "notes")
        
        self.notes_manager = NotesManager(notes_dir=self.notes_folder)
        self.current_note = None
        self.is_loading = False
        self.highlighter = None
        self.highlight_timeout_id = 0
        self.rename_timeout_id = 0
        self.changed_handler_id = 0
        self.is_updating_images = False
        self.link_anchors = {}
        self.image_anchors = []
        
        # Actions
        self.pinned_path = self.config_dir / "pinned.json"
        self.pinned_notes = self.load_pinned()
        self.setup_actions()

    def load_config(self):
        default_config = {
            'notes_folder': str(Path.home() / "Documents" / "TokyoNotes" if (Path.home() / "Documents").exists() else "notes"),
            'show_sidebar': True,
            'show_toolbar': True,
            'show_stats': False
        }
        if self.config_path.exists():
            try:
                return {**default_config, **json.loads(self.config_path.read_text())}
            except:
                pass
        return default_config

    def save_config(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.config_path.write_text(json.dumps(self.config))
        except:
            pass

    def load_pinned(self):
        if self.pinned_path.exists():
            try:
                return json.loads(self.pinned_path.read_text())
            except:
                pass
        return []

    def save_pinned(self):
        self.pinned_path.write_text(json.dumps(self.pinned_notes))

    def on_pin_note(self, action, parameter):
        note_name = parameter.get_string()
        if note_name not in self.pinned_notes:
            self.pinned_notes.append(note_name)
            self.save_pinned()
            self.refresh_list(self.sidebar.search_entry.get_text())

    def on_unpin_note(self, action, parameter):
        note_name = parameter.get_string()
        if note_name in self.pinned_notes:
            self.pinned_notes.remove(note_name)
            self.save_pinned()
            self.refresh_list(self.sidebar.search_entry.get_text())

    def setup_actions(self):
        delete_action = Gio.SimpleAction.new("delete", GLib.VariantType.new("s"))
        delete_action.connect("activate", self.on_delete_action)
        self.add_action(delete_action)
        
        pin_action = Gio.SimpleAction.new("pin", GLib.VariantType.new("s"))
        pin_action.connect("activate", self.on_pin_note)
        self.add_action(pin_action)
        
        unpin_action = Gio.SimpleAction.new("unpin", GLib.VariantType.new("s"))
        unpin_action.connect("activate", self.on_unpin_note)
        self.add_action(unpin_action)

    def load_config(self):
        default_config = {
            'notes_folder': str(Path.home() / "Documents" / "TokyoNotes" if (Path.home() / "Documents").exists() else "notes"),
            'show_sidebar': True,
            'show_toolbar': True,
            'show_stats': False
        }
        if self.config_path.exists():
            try:
                return {**default_config, **json.loads(self.config_path.read_text())}
            except:
                pass
        return default_config

    def save_config(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.config_path.write_text(json.dumps(self.config))
        except:
            pass

    def on_select_folder(self, button):
        dialog = Gtk.FileChooserDialog(
            title="Select Notes Folder",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self.win
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Select", Gtk.ResponseType.OK)
        
        if Path(self.notes_folder).exists():
            dialog.set_file(Gio.File.new_for_path(str(Path(self.notes_folder).absolute())))
        
        def on_response(dialog, response):
            if response == Gtk.ResponseType.OK:
                new_folder = dialog.get_file().get_path()
                if new_folder != self.notes_folder:
                    self.notes_folder = new_folder
                    self.config['notes_folder'] = new_folder
                    self.save_config()
                    self.notes_manager = NotesManager(notes_dir=new_folder)
                    self.refresh_list()
                    if self.current_note:
                        self.buffer.set_text("")
                        self.current_note = None
                        self.win.set_title("Tokyo Notes")
            dialog.destroy()
        
        dialog.connect("response", on_response)
        dialog.show()

    def do_activate(self):
        # Force dark mode to ensure Tokyo Night theme remains consistent
        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

        # Apply Tokyo Night CSS
        style_provider = Gtk.CssProvider()
        style_provider.load_from_path('style.css')
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                style_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        # Main Window
        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_title("Tokyo Notes")
        self.win.set_default_size(1000, 700)

        # Split View
        self.split_view = Adw.OverlaySplitView()
        self.win.set_content(self.split_view)

        # Sidebar
        self.sidebar = Sidebar(
            self.on_new_note,
            self.on_select_folder,
            self.on_search_changed,
            self.on_dashboard_clicked
        )
        self.note_list = self.sidebar.note_list
        self.note_list.connect("row-selected", self.on_note_selected)
        
        self.split_view.set_sidebar(self.sidebar)
        
        # Add Keyboard Shortcuts
        setup_shortcuts(self.win, self.on_new_note_global, self.on_dashboard_clicked, self.on_search_shortcut, self.on_escape_shortcut, self.quit)

        # Content Header
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_header = Adw.HeaderBar()
        self.content_title = Gtk.Label(label="Tokyo Notes")
        self.content_header.set_title_widget(self.content_title)
        
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.set_active(self.config.get('show_sidebar', True))
        self.sidebar_toggle.connect("toggled", self.on_sidebar_toggled)
        self.content_header.pack_start(self.sidebar_toggle)
        self.split_view.set_show_sidebar(self.sidebar_toggle.get_active())

# ... (rest of headers) ...

        # PDF Export Button
        self.pdf_btn = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Export to PDF")
        self.pdf_btn.connect("clicked", self.actions.on_export_pdf)
        self.content_header.pack_end(self.pdf_btn)

        self.copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy as Markdown")
        self.copy_btn.connect("clicked", self.actions.on_copy_markdown)
        self.content_header.pack_end(self.copy_btn)

        # Toolbar Toggle Button (Eye Icon)
        self.toolbar_toggle = Gtk.ToggleButton(icon_name="view-reveal-symbolic", tooltip_text="Toggle Formatting Toolbar")
        self.toolbar_toggle.set_active(self.config.get('show_toolbar', True))
        self.toolbar_toggle.connect("toggled", self.on_toolbar_toggled)
        self.content_header.pack_end(self.toolbar_toggle)
        
        self.content_box.append(self.content_header)

        # Editor and Toolbar
        self.editor = Editor(
            self.on_text_changed,
            self.on_cursor_moved,
            self.actions.on_paste_clipboard,
            self.create_toolbar,
            lambda: self.notes_manager.get_notes()
        )
        self.buffer = self.editor.buffer
        self.text_view = self.editor.text_view
        self.toolbar = self.editor.toolbar
        self.changed_handler_id = self.editor.changed_handler_id
        
        self.toolbar.set_visible(self.toolbar_toggle.get_active())
        
        # Apply Stats Visibility
        self.stats_toggle.set_active(self.config.get('show_stats', False))
        self.editor.status_bar.set_visible(self.stats_toggle.get_active())
        
        self.highlighter = MarkdownHighlighter(self.buffer)
        self.highlighter.highlight()
        
        self.link_anchors = {}
        self.image_anchors = []
        
        gesture = Gtk.GestureClick.new()
        gesture.set_button(1)
        gesture.connect("pressed", self.on_click_pressed)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.text_view.add_controller(gesture)
        
        self.text_view.set_focus_on_click(True)
        self.content_box.append(self.editor)
        
        # Dashboard View
        self.dashboard_view = Dashboard(self.on_dashboard_item_selected, self.refresh_dashboard, default_filter="today")
        self.dashboard_list = self.dashboard_view.dashboard_list
        
        # Stack for content switching 
        self.content_stack = Gtk.Stack()
        self.content_stack.add_named(self.content_box, "editor")
        self.content_stack.add_named(self.dashboard_view, "dashboard")
        
        self.split_view.set_content(self.content_stack)

        # Responsive Breakpoint
        display = Gdk.Display.get_default()
        if display:
            monitors = display.get_monitors()
            if monitors.get_n_items() > 0:
                monitor = monitors.get_item(0)
                screen_width = monitor.get_geometry().width
                half_width = screen_width // 2
                condition = Adw.BreakpointCondition.parse(f"max-width: {half_width}px")
                breakpoint = Adw.Breakpoint.new(condition)
                breakpoint.add_setter(self.split_view, "collapsed", True)
                breakpoint.add_setter(self.sidebar_toggle, "active", False)
                self.win.add_breakpoint(breakpoint)

        # Initial Load
        self.refresh_list()
        
        # Start with a new 'Untitled' note
        self.on_new_note(None)
        
        self.win.present()

    def create_toolbar(self):
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        toolbar.add_css_class("toolbar")
        # Use descriptive text labels instead of icons
        formats = [
            ("**", "**", "Bold"),
            ("_", "_", "Italic"),
            ("~~", "~~", "Strikethrough"),
            ("# ", "", "H1"),
            ("## ", "", "H2"),
            ("### ", "", "H3"),
            ("`", "`", "Code"),
            ("```\n", "\n```", "Block"),
            ("- ", "", "List"),
            ("- [ ] ", "", "Checkbox"),
            ("[Link](url)", "", "Link"),
            ("![Alt](url)", "", "Image"),
            ("> ", "", "Quote"),
        ]
        for prefix, suffix, label in formats:
            btn = Gtk.Button(label=label)
            btn.set_tooltip_text(label)
            btn.add_css_class("toolbar-btn")
            btn.connect("clicked", self.apply_format, prefix, suffix)
            toolbar.append(btn)
        
        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)
        
        # Stats Toggle
        self.stats_toggle = Gtk.ToggleButton(icon_name="dialog-information-symbolic")
        self.stats_toggle.set_tooltip_text("Show Word Count")
        self.stats_toggle.connect("toggled", self.on_stats_toggled)
        toolbar.append(self.stats_toggle)
        
        return toolbar

    def on_stats_toggled(self, button):
        visible = button.get_active()
        self.editor.status_bar.set_visible(visible)
        self.config['show_stats'] = visible
        self.save_config()
        if visible:
            self.update_stats()

    def update_stats(self):
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        
        char_count = len(text)
        word_count = len(text.split())
        read_time = max(1, word_count // 200)
        
        self.editor.stats_label.set_label(f"Words: {word_count} | Chars: {char_count} | Read: {read_time}m")

    def apply_format(self, btn, prefix, suffix):
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(start, end, True)
            self.buffer.delete(start, end)
            self.buffer.insert(start, f"{prefix}{text}{suffix}")
        else:
            self.buffer.insert_at_cursor(f"{prefix}{suffix}")
            if suffix:
                cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
                cursor_iter.backward_chars(len(suffix))
                self.buffer.place_cursor(cursor_iter)
        self.text_view.grab_focus()

    def refresh_list(self, filter_text=""):
        while (child := self.sidebar.note_list.get_first_child()):
            self.sidebar.note_list.remove(child)
        all_notes = self.notes_manager.get_notes(filter_text)
        
        pinned = [n for n in all_notes if n in self.pinned_notes]
        others = [n for n in all_notes if n not in self.pinned_notes]
        
        if not all_notes and filter_text:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label="No notes found", xalign=0.5)
            label.add_css_class("dim-label")
            row.set_child(label)
            row.set_selectable(False)
            self.sidebar.note_list.append(row)
        else:
            if pinned:
                for note in pinned:
                    self.add_note_row(note, is_pinned=True)
            
            for note in others:
                self.add_note_row(note, is_pinned=False)

    def add_note_row(self, note, is_pinned=False):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        label = Gtk.Label(label=note, xalign=0)
        label.add_css_class("sidebar-label")
        label.set_hexpand(True)
        box.append(label)
        
        if is_pinned:
            pin_icon = Gtk.Image.new_from_icon_name("pin-symbolic")
            box.append(pin_icon)
        
        row.set_child(box)
        row.note_name = note
        
        # Right Click Menu
        gesture = Gtk.GestureClick(button=3)
        gesture.connect("pressed", self.on_row_right_click, row)
        row.add_controller(gesture)

        self.sidebar.note_list.append(row)

    def on_row_right_click(self, gesture, n_press, x, y, row):
        note_name = getattr(row, 'note_name', None)
        if not note_name:
            return

        menu = Gio.Menu()
        menu.append("Delete", f"app.delete::{note_name}")
        if note_name in self.pinned_notes:
            menu.append("Unpin", f"app.unpin::{note_name}")
        else:
            menu.append("Pin", f"app.pin::{note_name}")
            
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        popover.set_pointing_to(Gdk.Rectangle(x=x, y=y, width=1, height=1))
        popover.popup()

    def on_delete_action(self, action, parameter):
        note_name = parameter.get_string()
        content = self.notes_manager.read_note(note_name).strip()
        
        if not content:
            self.confirm_delete(note_name)
        else:
            dialog = Adw.MessageDialog(
                transient_for=self.win,
                heading="Delete Note?",
                body=f"Are you sure you want to delete '{note_name}'? This action cannot be undone."
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self.on_delete_dialog_response, note_name)
            dialog.present()

    def on_delete_dialog_response(self, dialog, response, note_name):
        if response == "delete":
            self.confirm_delete(note_name)

    def confirm_delete(self, note_name):
        self.notes_manager.delete_note(note_name)
        if self.current_note == note_name:
            self.current_note = None
            self.buffer.set_text("")
            self.win.set_title("Tokyo Notes")
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_new_note_global(self, *args):
        self.on_new_note(None)
        # Ensure focus returns to the text view after creating a new note
        self.text_view.grab_focus()
        return True

    def on_new_note(self, btn):
        name = self.notes_manager.create_note()
        self.current_note = name
        self.content_title.set_label(name)
        self.buffer.set_text("")
        self.refresh_list()
        self.content_box.set_visible(True)
        self.dashboard_view.set_visible(False)

    def on_note_selected(self, listbox, row):
        if not row or self.is_loading:
            return
        
        note_name = getattr(row, 'note_name', None)
        if not note_name:
            return

        # Switch to editor view if in dashboard
        self.content_box.set_visible(True)
        self.dashboard_view.set_visible(False)
        
        self.is_loading = True
        self.current_note = note_name
        self.content_title.set_label(self.current_note)
        content = self.notes_manager.read_note(self.current_note)
        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.set_text(content)
        self.buffer.handler_unblock(self.changed_handler_id)
        self.update_highlighting()
        self.is_loading = False

    def on_dashboard_clicked(self, button=None):
        checkboxes = self.notes_manager.get_all_checkboxes()
        unchecked = [cb for cb in checkboxes if not cb['checked']]
        
        import datetime
        today = datetime.date.today()
        today_str = today.isoformat()
        next_week_str = (today + datetime.timedelta(days=7)).isoformat()
        
        has_today = any(cb.get('deadline') and cb['deadline'].startswith(today_str) for cb in unchecked)
        has_week = any(cb.get('deadline') and cb['deadline'] <= next_week_str for cb in unchecked)
        
        default_filter = "today"
        if not has_today:
            default_filter = "week"
            if not has_week:
                default_filter = "all"
        
        self.dashboard_view.update_active_filter(default_filter)
        self.refresh_dashboard(default_filter)
        self.content_box.set_visible(False)
        self.dashboard_view.set_visible(True)
        self.content_title.set_label("Dashboard")

    def on_dashboard_header_clicked(self, gesture, n_press, x, y, note_name):
        self.ui.content_box.set_visible(True)
        self.dashboard_view.set_visible(False)
        self.ui.content_title.set_label(note_name)
        
        row = self.ui.sidebar.note_list.get_first_child()
        while row:
            if hasattr(row, 'note_name') and row.note_name.lower() == note_name.lower():
                self.ui.sidebar.note_list.select_row(row)
                break
            row = row.get_next_sibling()

    def handle_deadline_click(self, x, y, note_name=None, line_num=None, widget=None):
        """Helper to launch DeadlinePicker."""
        def on_date_selected(deadline):
            if note_name and line_num:
                self.notes_manager.update_deadline(note_name, line_num, deadline)
                self.refresh_dashboard()
                self.refresh_list(self.sidebar.search_entry.get_text())
                if self.current_note == note_name:
                    content = self.notes_manager.read_note(note_name)
                    self.buffer.set_text(content)
            else:
                self.buffer.insert_at_cursor(f"@{deadline}")

        picker = DeadlinePicker(on_date_selected)

        # Position the picker precisely at the click coordinates
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1

        picker.set_parent(self.text_view)
        picker.set_pointing_to(rect)
        picker.popup()

    def refresh_dashboard(self, filter_type="today"):
        while (child := self.dashboard_list.get_first_child()):
            self.dashboard_list.remove(child)
        
        checkboxes = self.notes_manager.get_all_checkboxes()
        
        # Filtering logic
        import datetime
        today = datetime.date.today()
        today_str = today.isoformat()
        next_week_str = (today + datetime.timedelta(days=7)).isoformat()
        
        def filter_items(f_type):
            items = [cb for cb in checkboxes if not cb['checked']]
            if f_type == "today":
                return [cb for cb in items if cb.get('deadline') and cb['deadline'].startswith(today_str)]
            elif f_type == "week":
                return [cb for cb in items if cb.get('deadline') and cb['deadline'] <= next_week_str]
            return items

        filtered_checkboxes = filter_items(filter_type)
        
        def get_time_label(deadline):
            if not deadline: return "All Day"
            parts = deadline.split(' ')
            if len(parts) > 1:
                return parts[1]
            return "All Day"

        def create_calendar_row(cb):
            row = Gtk.ListBoxRow()
            row.add_css_class("calendar-row")
            row.checkbox_data = cb
            
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            
            # Time Column
            time_str = get_time_label(cb.get('deadline'))
            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("time-column")
            
            # Deadline Edit Handler for the time label
            gesture = Gtk.GestureClick.new()
            gesture.connect("pressed", lambda g, n, x, y: self.handle_deadline_click(x, y, cb['note'], cb['line'], time_label))
            time_label.add_controller(gesture)
            box.append(time_label)
            
            # Checkbox
            checkbox = Gtk.CheckButton()
            checkbox.set_active(cb['checked'])
            checkbox.connect("toggled", self.on_dashboard_checkbox_toggled, cb)
            box.append(checkbox)
            
            # Task Text
            label = Gtk.Label(label=cb['text'], xalign=0)
            label.set_hexpand(True)
            box.append(label)
            
            # Note Chip
            note_chip = Gtk.Label(label=cb['note'])
            note_chip.add_css_class("note-chip")
            box.append(note_chip)
            
            row.set_child(box)
            return row

        if not filtered_checkboxes:
            label = Gtk.Label(label="Add checklists with deadlines for them to show up here.")
            label.add_css_class("empty-state-label")
            self.dashboard_list.append(label)
        elif filter_type == "all":
            items_with_deadline = [cb for cb in filtered_checkboxes if cb.get('deadline')]
            items_without_deadline = [cb for cb in filtered_checkboxes if not cb.get('deadline')]
            
            sorted_with = sorted(items_with_deadline, key=lambda x: x.get('deadline', ''))
            
            current_date = None
            for cb in sorted_with:
                deadline_date = cb.get('deadline', '').split(' ')[0]
                if deadline_date != current_date:
                    current_date = deadline_date
                    try:
                        dt = datetime.datetime.strptime(deadline_date, "%Y-%m-%d")
                        header_text = dt.strftime("%A, %B %d")
                    except:
                        header_text = deadline_date
                    
                    header_label = Gtk.Label(label=header_text, xalign=0)
                    header_label.add_css_class("day-header")
                    header_row = Gtk.ListBoxRow()
                    header_row.set_child(header_label)
                    header_row.set_selectable(False)
                    self.dashboard_list.append(header_row)
                
                self.dashboard_list.append(create_calendar_row(cb))
            
            if items_without_deadline:
                header_label = Gtk.Label(label="Miscellaneous", xalign=0)
                header_label.add_css_class("day-header")
                header_row = Gtk.ListBoxRow()
                header_row.set_child(header_label)
                header_row.set_selectable(False)
                self.dashboard_list.append(header_row)
                
                for cb in items_without_deadline:
                    self.dashboard_list.append(create_calendar_row(cb))
        elif filter_type == "today":
            # Sort by time
            sorted_items = sorted(filtered_checkboxes, key=lambda x: x.get('deadline', ''))
            for cb in sorted_items:
                self.dashboard_list.append(create_calendar_row(cb))
        elif filter_type == "week":
            # Group by date
            sorted_items = sorted(filtered_checkboxes, key=lambda x: x.get('deadline', ''))
            current_date = None
            for cb in sorted_items:
                deadline_date = cb.get('deadline', '').split(' ')[0]
                if deadline_date != current_date:
                    current_date = deadline_date
                    try:
                        dt = datetime.datetime.strptime(deadline_date, "%Y-%m-%d")
                        header_text = dt.strftime("%A, %B %d")
                    except:
                        header_text = deadline_date
                    
                    header_label = Gtk.Label(label=header_text, xalign=0)
                    header_label.add_css_class("day-header")
                    header_row = Gtk.ListBoxRow()
                    header_row.set_child(header_label)
                    header_row.set_selectable(False)
                    self.dashboard_list.append(header_row)
                
                self.dashboard_list.append(create_calendar_row(cb))
        
        total = len(filtered_checkboxes)
        stats = f"{total} items" if total > 0 else "No items"
        self.win.set_title(f"Dashboard - {stats}" if total > 0 else "Dashboard")

    def on_dashboard_checkbox_toggled(self, checkbox, cb):
        checked = checkbox.get_active()
        self.notes_manager.update_checkbox(cb['note'], cb['line'], checked)
        # Re-fetch current active filter from the dashboard
        active_filter = [f for f, btn in self.dashboard_view.buttons.items() if btn.has_css_class("active")][0]
        self.refresh_dashboard(active_filter)

    def on_dashboard_item_selected(self, listbox, row):
        if not row or not hasattr(row, 'checkbox_data'):
            return
        cb = row.checkbox_data
        
        self.content_box.set_visible(True)
        self.dashboard_view.set_visible(False)
        
        notes = self.notes_manager.get_notes()
        for note in notes:
            if note.lower() == cb['note'].lower():
                row = self.note_list.get_first_child()
                while row:
                    if hasattr(row, 'note_name') and row.note_name.lower() == note.lower():
                        self.note_list.select_row(row)
                        
                        # Scroll to the specific line
                        GLib.idle_add(self.scroll_to_line, cb['line'])
                        break
                    row = row.get_next_sibling()
                break

    def scroll_to_line(self, line_num):
        # Adjust for 0-based indexing
        it = self.buffer.get_iter_at_line(line_num - 1)
        if not isinstance(it, Gtk.TextIter):
            it = it[1]
        mark = self.buffer.create_mark(None, it, True)
        self.text_view.scroll_to_mark(mark, 0.0, True, 0.5, 0.1)
        self.buffer.delete_mark(mark)
        return False

    def on_sidebar_toggled(self, button):
        visible = button.get_active()
        self.split_view.set_show_sidebar(visible)
        self.config['show_sidebar'] = visible
        self.save_config()

    def on_toolbar_toggled(self, button):
        visible = button.get_active()
        self.toolbar.set_visible(visible)
        self.config['show_toolbar'] = visible
        self.save_config()

    def show_export_dialog(self, title, body, is_error=False):
        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading=title,
            body=body
        )
        dialog.add_response("ok", "OK")
        if is_error:
            dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.present()

# ... (removed action methods) ...

    def on_click_pressed(self, gesture, n_press, x, y):
        self.handle_link_click(x, y)

    def handle_link_click(self, x, y):
        # Convert widget coordinates to buffer coordinates
        bx, by = self.text_view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(x), int(y))
        success, cursor_iter = self.text_view.get_iter_at_location(bx, by)
        if not success:
            return
        cursor_offset = cursor_iter.get_offset()

        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)

        found_link = False

        # Regex to match Markdown links [[NoteName]] or [Text](url)
        for match in re.finditer(r'\[\[([^\]]+)\]\]|\[([^\]]+)\]\(([^)]+)\)', text):
            if match.start() <= cursor_offset <= match.end():
                if match.group(1): # Internal link [[Note]]
                    self.on_link_clicked(match.group(1))
                else: # Standard [Text](url)
                    url = match.group(3)
                    if url.startswith('http'):
                        import webbrowser
                        webbrowser.open_new_tab(url)
                    else:
                        self.on_link_clicked(url.rsplit('.', 1)[0])
                return

    def on_link_clicked(self, note_name):
        row = self.note_list.get_first_child()
        while row:
            if hasattr(row, 'note_name') and row.note_name.lower() == note_name.lower():
                self.note_list.select_row(row)
                break
            row = row.get_next_sibling()

    def handle_link_click(self, x, y):
        # Convert widget coordinates to buffer coordinates
        bx, by = self.text_view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(x), int(y))
        success, cursor_iter = self.text_view.get_iter_at_location(bx, by)
        if not success:
            return
        cursor_offset = cursor_iter.get_offset()

        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)

        # Markdown links [[NoteName]] or [Text](url)
        for match in re.finditer(r'\[\[([^\]]+)\]\]|\[([^\]]+)\]\(([^)]+)\)', text):
            if match.start() <= cursor_offset <= match.end():
                if match.group(1): # Internal link [[Note]]
                    self.on_link_clicked(match.group(1))
                else: # Standard [Text](url)
                    url = match.group(3)
                    if url.startswith('http'):
                        import webbrowser
                        webbrowser.open_new_tab(url)
                    else:
                        self.on_link_clicked(url.rsplit('.', 1)[0])
                return
        
        # Regex to match raw URLs
        for match in re.finditer(r'(https?://[^\s\)]+)', text):
            if match.start() <= cursor_offset <= match.end():
                import webbrowser
                webbrowser.open_new_tab(match.group(1))
                return
        
        # Regex to match Tags
        for match in re.finditer(r'(?<!\w)#(\w+)', text):
            if match.start() <= cursor_offset <= match.end():
                self.sidebar.search_entry.set_text(match.group(0))
                self.on_search_changed(self.sidebar.search_entry)
                return

        # Regex to match Deadlines
        for match in re.finditer(r'@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)', text):
            if match.start() <= cursor_offset <= match.end():
                self.handle_deadline_click(x, y, self.current_note, cursor_iter.get_line() + 1)
                return

    def on_link_clicked(self, note_name):
        row = self.note_list.get_first_child()
        while row:
            if hasattr(row, 'note_name') and row.note_name.lower() == note_name.lower():
                self.note_list.select_row(row)
                break
            row = row.get_next_sibling()

    def update_images(self):
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
            
            if not self.current_note:
                return
            
            # Re-get text after clearing
            start, end = self.buffer.get_bounds()
            text = self.buffer.get_text(start, end, True)
            
            # 2. Find and insert images
            note_dir = Path(self.notes_manager.notes_dir).resolve()
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
                    # Remote image with async loading
                    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                    widget.add_css_class("image-container")
                    
                    img = Gtk.Image.new_from_icon_name("image-loading-symbolic")
                    img.set_pixel_size(64)
                    widget.append(img)
                    
                    label = Gtk.Label(label="Loading...")
                    label.add_css_class("image-caption")
                    widget.append(label)
                    
                    def on_image_loaded(file, result, img_widget, label_widget):
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
                        except Exception as e:
                            img_widget.set_from_icon_name("image-missing-symbolic")
                            label_widget.set_label(f"Failed to load")
                            pass

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
                        except Exception as e:
                            widget = Gtk.Label(label=f"Error: {url}")
                            widget.add_css_class("image-error")
                    else:
                        widget = Gtk.Label(label=f"Not Found: {url}")
                        widget.add_css_class("image-error")
                
                self.text_view.add_child_at_anchor(widget, anchor)
                
        finally:
            self.buffer.handler_unblock(self.changed_handler_id)
            self.is_updating_images = False

    def update_highlighting(self):
        GLib.idle_add(self._do_highlight)

    def _do_highlight(self):
        if not self.highlighter: return False
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        self.buffer.handler_block(self.changed_handler_id)
        self.highlighter.highlight(cursor_line)
        self.buffer.handler_unblock(self.changed_handler_id)
        return False

    def on_cursor_moved(self, buffer, pspec):
        self.update_highlighting()

    def on_search_shortcut(self):
        self.sidebar.search_entry.grab_focus()
        return True

    def on_escape_shortcut(self):
        if self.dashboard_view.get_visible():
            self.content_box.set_visible(True)
            self.dashboard_view.set_visible(False)
            if self.current_note:
                self.content_title.set_label(self.current_note)
            else:
                self.content_title.set_label("Tokyo Notes")
            return True
        elif self.sidebar.search_entry.has_focus():
            self.sidebar.search_entry.set_text("")
            self.text_view.grab_focus()
            return True
        return False

    def on_search_changed(self, entry):
        self.refresh_list(entry.get_text())

    def do_delayed_highlight(self):
        self.highlight_timeout_id = 0
        self.update_highlighting()
        self.update_images()
        return False

    def do_delayed_save(self):
        self.rename_timeout_id = 0
        if not self.current_note: return False
        
        start, end = self.buffer.get_bounds()
        content = self.buffer.get_text(start, end, True)
        
        # If content is empty and note is 'Untitled', don't save
        if not content.strip() and self.current_note == "Untitled":
            return False

        match = re.search(r'^# (.+)$', content, re.MULTILINE)
        if match:
            new_title = "".join([c for c in match.group(1).strip() if c.isalnum() or c in (' ', '-', '_')]).strip()
            if new_title and new_title != self.current_note:
                # Collision check
                if not (Path(self.notes_manager.notes_dir) / f"{new_title}.md").exists():
                    if self.notes_manager.rename_note(self.current_note, new_title):
                        self.current_note = new_title
                        self.content_title.set_label(self.current_note)
                        self.refresh_list() # Force immediate UI update
        
        self.notes_manager.save_note(self.current_note, content)
        return False

    def on_text_changed(self, buffer):
        if self.is_loading or not self.current_note or self.is_updating_images:
            return
        
        # Update word counts if status bar is visible
        if self.editor.status_bar.get_visible():
            self.update_stats()
        
        if self.highlight_timeout_id > 0:
            GLib.source_remove(self.highlight_timeout_id)
        self.highlight_timeout_id = GLib.timeout_add(100, self.do_delayed_highlight)
        
        if self.rename_timeout_id > 0:
            GLib.source_remove(self.rename_timeout_id)
        self.rename_timeout_id = GLib.timeout_add(1000, self.do_delayed_save)

if __name__ == "__main__":
    # Import PangoCairo for PDF export
    from gi.repository import PangoCairo
    app = TokyoNotes()
    app.run(sys.argv)
