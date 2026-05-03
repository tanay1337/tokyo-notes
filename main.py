import datetime
import re
import sys
import threading
import webbrowser
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

try:
    from gi.repository import PangoCairo
except ImportError:
    PangoCairo = None

from core.actions import ActionsHandler
from core.config import ConfigManager
from core.graph_manager import GraphManager
from core.highlighter import MarkdownHighlighter
from core.shortcuts import setup_shortcuts
from core.storage import NotesManager
from core.utils import create_empty_state_widget, escape_xml, format_markdown_inline
from mcp_server import run_mcp_server
from ui.dashboard import Dashboard
from ui.deadline_picker import DeadlinePicker
from ui.editor import Editor
from ui.graph_view import GraphView
from ui.sakura_overlay import SakuraOverlay
from ui.settings import SettingsView
from ui.sidebar import Sidebar
from ui.toolbar import build_toolbar

_CLICK_PATTERNS = [
    ("wiki", re.compile(r"\[\[([^\]]+)\]\]")),
    ("mdlink", re.compile(r"(!?)\[([^\]]+)\]\(([^)]+)\)")),
    ("url", re.compile(r"https?://[^\s\)]+")),
    ("tag", re.compile(r"(?<!\w)#(\w+)")),
    ("deadline", re.compile(r"@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)")),
]


class TokyoNotes(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id="com.example.TokyoNotes", **kwargs)
        self.base_dir = Path(__file__).parent
        self.actions = ActionsHandler(self)

        self.cfg = ConfigManager()
        self.notes_folder = self.cfg.get("notes_folder")

        self.notes_manager = NotesManager(notes_dir=self.notes_folder)
        self.current_note = None
        self.is_loading = False
        self.highlighter = None
        self.highlight_timeout_id = 0
        self.rename_timeout_id = 0
        self.search_timeout_id = 0
        self.changed_handler_id = 0
        self.image_timeout_id = 0
        self.last_cursor_line = -1

        # Start AI Bridge if enabled
        if self.cfg.get("mcp_server_enabled", False):
            port = self.cfg.get("mcp_server_port", 8999)
            threading.Thread(target=run_mcp_server, args=(port,), daemon=True).start()

        # Actions
        self.setup_actions()

    def on_toggle_archive_note(self, action, parameter):
        note_name = parameter.get_string()
        self.cfg.toggle_archive(note_name)
        # If archive becomes empty while viewing it, switch back to main
        if (
            not self.cfg.archived
            and self.sidebar.stack.get_visible_child_name() == "archive"
        ):
            self.sidebar.stack.set_visible_child_name("main")
            self.sidebar.archived_nav_btn.set_label("Archived Notes")
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_pin_note(self, action, parameter):
        note_name = parameter.get_string()
        self.cfg.pin(note_name)
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_unpin_note(self, action, parameter):
        note_name = parameter.get_string()
        self.cfg.unpin(note_name)
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

        archive_action = Gio.SimpleAction.new("archive", GLib.VariantType.new("s"))
        archive_action.connect("activate", self.on_toggle_archive_note)
        self.add_action(archive_action)

    def on_select_folder(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Notes Folder")
        if Path(self.notes_folder).exists():
            dialog.set_initial_folder(
                Gio.File.new_for_path(str(Path(self.notes_folder).absolute()))
            )
        dialog.select_folder(self.win, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                new_folder = folder.get_path()
                if new_folder != self.notes_folder:
                    self.notes_folder = new_folder
                    self.cfg.set("notes_folder", new_folder)
                    self.notes_manager = NotesManager(notes_dir=new_folder)
                    if self.settings_view:
                        self.settings_view.update_folder_path(new_folder)
                    self.refresh_list()
                    if self.current_note:
                        self.buffer.handler_block(self.changed_handler_id)
                        self.buffer.set_text("")
                        self.buffer.handler_unblock(self.changed_handler_id)
                        self.current_note = None
                        self.win.set_title("Tokyo Notes")
        except GLib.Error:
            pass  # User cancelled

    def do_activate(self):
        # CSS Providers
        self.theme_provider = Gtk.CssProvider()
        self.style_provider = Gtk.CssProvider()

        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, self.theme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            Gtk.StyleContext.add_provider_for_display(
                display, self.style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        # Main Window
        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_title("Tokyo Notes")
        self.win.set_default_size(1000, 700)

        # Set App Icon
        if display:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            icon_theme.add_search_path(str(self.base_dir / "assets"))
            self.win.set_icon_name("tokyo_notes_icon")

        # Initial Theme
        self.apply_theme(self.cfg.get("theme"))

        # Split View
        self.split_view = Adw.OverlaySplitView()
        self.win.set_content(self.split_view)

        # Sidebar
        self.sidebar = Sidebar(
            self.on_new_note,
            self.on_search_changed,
            self.on_dashboard_clicked,
            self.on_archived_clicked,
            self.on_graph_clicked,
        )

        self.sidebar.main_list.connect("row-selected", self.on_note_selected)
        self.sidebar.archive_list.connect("row-selected", self.on_note_selected)

        self.split_view.set_sidebar(self.sidebar)

        # Content Header
        self.content_header = Adw.HeaderBar()
        self.content_title = Gtk.Label(label="Tokyo Notes")
        self.content_header.set_title_widget(self.content_title)

        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.set_active(self.cfg.get("show_sidebar"))
        self.sidebar_toggle_handler = self.sidebar_toggle.connect(
            "toggled", self.on_sidebar_toggled
        )
        self.content_header.pack_start(self.sidebar_toggle)
        self.split_view.set_show_sidebar(self.sidebar_toggle.get_active())

        # PDF Export Button
        self.pdf_btn = Gtk.Button(
            icon_name="document-save-symbolic", tooltip_text="Export to PDF"
        )
        self.pdf_btn.connect("clicked", self.actions.on_export_pdf)
        self.content_header.pack_end(self.pdf_btn)

        self.copy_btn = Gtk.Button(
            icon_name="edit-copy-symbolic", tooltip_text="Copy as Markdown"
        )
        self.copy_btn.connect("clicked", self.actions.on_copy_markdown)
        self.content_header.pack_end(self.copy_btn)

        # Settings Button
        self.settings_btn = Gtk.Button(
            icon_name="emblem-system-symbolic", tooltip_text="Settings"
        )
        self.settings_btn.connect("clicked", self.on_settings_clicked)
        self.content_header.pack_end(self.settings_btn)

        # Editor and Toolbar
        assets_dir = self.base_dir / "assets" / "toolbar"
        toolbar = build_toolbar(assets_dir, self.apply_format)

        self.editor = Editor(
            self.on_text_changed,
            self.on_cursor_moved,
            self.actions.on_paste_clipboard,
            toolbar,
            lambda: self.notes_manager.get_notes(),
        )
        self.buffer = self.editor.buffer
        self.text_view = self.editor.text_view
        self.toolbar = self.editor.toolbar
        self.changed_handler_id = self.editor.changed_handler_id

        self.toolbar.set_visible(self.cfg.get("show_toolbar"))

        # Apply Stats Visibility
        self.editor.status_bar.set_visible(self.cfg.get("show_stats"))

        self.highlighter = MarkdownHighlighter(self.buffer, self.cfg.get("theme"))
        self.highlighter.highlight()

        self.image_anchors = []
        self.last_cursor_line = -1
        gesture = Gtk.GestureClick.new()
        gesture.set_button(1)
        gesture.connect("pressed", self.on_click_pressed)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.text_view.add_controller(gesture)

        self.text_view.set_focus_on_click(True)

        # Dashboard View (Lazy Loaded)

        # Settings and Graph Views (Lazy Initialized)
        self.settings_view = None
        self.graph_view = None
        self.graph_manager = None  # Lazy-loaded on first graph click

        # Stack for content switching
        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(200)
        self.content_stack.set_vexpand(True)
        self.content_stack.add_named(self.editor, "editor")

        # Overlay for Sakura Celebration
        self.overlay = Gtk.Overlay()
        self.sakura_overlay = SakuraOverlay()
        self.overlay.set_child(self.content_stack)
        self.overlay.add_overlay(self.sakura_overlay)

        main_layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_layout.append(self.content_header)
        main_layout.append(self.overlay)
        self.split_view.set_content(main_layout)

        # Show window immediately for perceived startup speed
        self.win.present()

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

        # Initial Load (Deferred to idle)
        GLib.idle_add(self.initial_load)

        # Add Keyboard Shortcuts
        setup_shortcuts(
            self.win,
            self.on_new_note_global,
            self.on_dashboard_clicked,
            self.on_graph_clicked,
            self.on_search_shortcut,
            self.on_escape_shortcut,
            self.on_delete_shortcut,
            self.actions.on_insert_timestamp,
            self.actions.on_zen_mode,
            self.quit,
        )

    def initial_load(self):
        self.refresh_list()
        notes = self.notes_manager.get_notes()
        if notes:
            # Select the most recent note
            most_recent = notes[0]
            # Need to iterate through sidebar rows to select the right one
            for list_box in (self.sidebar.main_list, self.sidebar.archive_list):
                row = list_box.get_first_child()
                while row:
                    if getattr(row, "note_name", "") == most_recent:
                        list_box.select_row(row)
                        self.on_note_selected(list_box, row)
                        return False
                    row = row.get_next_sibling()
        else:
            self.on_new_note(None)
        return False

    def on_delete_shortcut(self):
        # Determine which note is currently selected
        note_name = None

        # Check sidebar lists
        main_row = self.sidebar.main_list.get_selected_row()
        archive_row = self.sidebar.archive_list.get_selected_row()

        if main_row and hasattr(main_row, "note_name"):
            note_name = main_row.note_name
        elif archive_row and hasattr(archive_row, "note_name"):
            note_name = archive_row.note_name

        if note_name:
            # Create a GLib.Variant for the action
            param = GLib.Variant("s", note_name)
            self.on_delete_action(None, param)
        return True

    def on_settings_config_changed(self, key, value):
        self.cfg.set(key, value)
        if key == "show_toolbar":
            self.toolbar.set_visible(value)
        elif key == "show_stats":
            self.editor.status_bar.set_visible(value)

    def update_stats(self):
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)

        char_count = len(text)
        word_count = len(text.split())
        read_time = max(1, word_count // 200)

        self.editor.stats_label.set_label(
            f"Words: {word_count} | Chars: {char_count} | Read: {read_time}m"
        )

    def apply_format(self, btn, prefix, suffix):
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(start, end, True)
            self.buffer.delete(start, end)
            is_block = (
                not suffix and prefix.rstrip() != prefix
            )  # ends with space → block
            if is_block and "\n" in text:
                formatted = "\n".join(prefix + line for line in text.split("\n"))
            else:
                formatted = f"{prefix}{text}{suffix}"
            self.buffer.insert(start, formatted)
        else:
            self.buffer.insert_at_cursor(f"{prefix}{suffix}")
            if suffix:
                cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
                cursor_iter.backward_chars(len(suffix))
                self.buffer.place_cursor(cursor_iter)
        self.text_view.grab_focus()

    def refresh_list(self, filter_text=""):
        all_notes = self.notes_manager.get_notes(filter_text)
        main_notes = [n for n in all_notes if not self.cfg.is_archived(n)]
        self.sidebar.populate(
            main_notes=main_notes,
            pinned=self.cfg.pinned,
            archived_notes=self.cfg.archived,
            on_right_click=self.on_row_right_click,
            snippet_fn=self._get_snippet,
            base_dir=self.base_dir,
            filter_text=filter_text,
        )

    def _get_snippet(self, note_name: str) -> str:
        return self.notes_manager.get_metadata(note_name).get("snippet", "")

    def on_row_right_click(self, gesture, n_press, x, y, row, is_archived=False):

        note_name = getattr(row, "note_name", None)
        if not note_name:
            return

        menu = Gio.Menu()
        menu.append("Delete", f"app.delete::{note_name}")
        if note_name in self.cfg.pinned:
            menu.append("Unpin", f"app.unpin::{note_name}")
        else:
            menu.append("Pin", f"app.pin::{note_name}")

        if is_archived:
            menu.append("Unarchive", f"app.archive::{note_name}")
        else:
            menu.append("Archive", f"app.archive::{note_name}")

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
                body=f"Are you sure you want to delete '{note_name}'? This action cannot be undone.",
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
        self.cfg.remove_note(note_name)

        # Switch back to main if last archived note deleted
        if (
            not self.cfg.archived
            and self.sidebar.stack.get_visible_child_name() == "archive"
        ):
            self.sidebar.stack.set_visible_child_name("main")
            self.sidebar.archived_nav_btn.set_label("Archived Notes")

        if self.current_note == note_name:
            self.current_note = None
            self.buffer.handler_block(self.changed_handler_id)
            self.buffer.set_text("")
            self.buffer.handler_unblock(self.changed_handler_id)
            self.win.set_title("Tokyo Notes")
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_new_note_global(self, *args):
        self.on_new_note(None)
        # Ensure focus returns to the text view after creating a new note
        self.text_view.grab_focus()
        return True

    def on_new_note(self, btn):
        # 1. Save the note we're leaving
        if self.rename_timeout_id > 0:
            GLib.source_remove(self.rename_timeout_id)
            self.rename_timeout_id = 0
        if self.current_note:
            start, end = self.buffer.get_bounds()
            content = self.buffer.get_text(start, end, True)
            if content.strip():
                self.notes_manager.save_note(self.current_note, content)

        # 2. Set up the new note
        name = self.notes_manager.create_note()
        self.current_note = name
        self.update_header_ui(name, is_editor=True)

        # 3. Block handler so set_text("") doesn't schedule a save
        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.set_text("")
        self.buffer.handler_unblock(self.changed_handler_id)

        self.refresh_list()
        self.content_stack.set_visible_child_name("editor")
        self.text_view.grab_focus()

    def on_note_selected(self, listbox, row):
        if not row or self.is_loading:
            return

        note_name = getattr(row, "note_name", None)
        if not note_name or note_name == self.current_note:
            return

        # Flush pending save for the note we're leaving
        if self.rename_timeout_id > 0:
            GLib.source_remove(self.rename_timeout_id)
            self.rename_timeout_id = 0
            if self.current_note:
                start, end = self.buffer.get_bounds()
                content = self.buffer.get_text(start, end, True)
                if content.strip():
                    self.notes_manager.save_note(self.current_note, content)

        self.is_loading = True
        self.current_note = note_name
        self.update_header_ui(self.current_note, is_editor=True)
        content = self.notes_manager.read_note(self.current_note)

        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.set_text(content)

        # Switch to editor view FIRST for perceived speed
        self.content_stack.set_visible_child_name("editor")

        # Show first 30 lines immediately (sync, fast)
        if self.highlighter:
            self.highlighter.highlight(start_line=0, end_line=30)

        self.buffer.handler_unblock(self.changed_handler_id)

        # Background highlight for the rest
        if self.highlighter and self.buffer.get_line_count() > 30:
            GLib.idle_add(lambda: self._finish_highlighting() or False)

        self.is_loading = False
        self.last_cursor_line = -1

        # Deselect in the other list if necessary
        if listbox == self.sidebar.main_list:
            self.sidebar.archive_list.unselect_all()
        else:
            self.sidebar.main_list.unselect_all()

    def _finish_highlighting(self):
        """Finish highlighting remaining lines after initial load."""
        if self.highlighter and self.current_note:
            self.highlighter.highlight(start_line=30)
        return False

    def on_archived_clicked(self, btn):
        if self.sidebar.stack.get_visible_child_name() == "archive":
            self.sidebar.stack.set_visible_child_name("main")
            self.sidebar.archived_nav_btn.set_label("Archived Notes")
        else:
            self.sidebar.stack.set_visible_child_name("archive")
            self.sidebar.archived_nav_btn.set_label("Back to Notes")

    def on_dashboard_clicked(self, button=None):
        # Lazy create Dashboard view on first access
        if not hasattr(self, 'dashboard_view') or self.dashboard_view is None:
            self.dashboard_view = Dashboard(
                self.on_dashboard_item_selected,
                self.on_dashboard_checkbox_toggled,
                self.on_dashboard_deadline_click,
                self.handle_row_click,
                self.on_dashboard_empty,
                self.refresh_dashboard,
                default_filter="today",
            )
            self.dashboard_list = self.dashboard_view.dashboard_list
            self.content_stack.add_named(self.dashboard_view, "dashboard")

        checkboxes = self.notes_manager.get_all_checkboxes(exclude=self.cfg.archived)
        unchecked = [cb for cb in checkboxes if not cb["checked"]]

        today = datetime.date.today()

        today_str = today.isoformat()
        next_week_str = (today + datetime.timedelta(days=7)).isoformat()

        has_today = any(
            cb.get("deadline") and cb["deadline"].startswith(today_str)
            for cb in unchecked
        )
        has_week = any(
            cb.get("deadline") and cb["deadline"] <= next_week_str for cb in unchecked
        )

        default_filter = "today"
        if not has_today:
            default_filter = "week"
            if not has_week:
                default_filter = "all"

        self.dashboard_view.update_active_filter(default_filter)
        self.refresh_dashboard(default_filter)
        self.content_stack.set_visible_child_name("dashboard")
        self.update_header_ui("Dashboard", is_editor=False)

    def on_dashboard_header_clicked(self, gesture, n_press, x, y, note_name):
        self.content_stack.set_visible_child_name("editor")
        self.update_header_ui(note_name, is_editor=True)
        for list_box in (self.sidebar.main_list, self.sidebar.archive_list):
            row = list_box.get_first_child()
            while row:
                if getattr(row, "note_name", "").lower() == note_name.lower():
                    list_box.select_row(row)
                    return
                row = row.get_next_sibling()

    def on_graph_clicked(self):
        # Lazy create graph manager and view
        if not self.graph_manager:
            self.graph_manager = GraphManager(self.notes_manager)
        
        if not self.graph_view:
            self.graph_view = GraphView(
                self.graph_manager.get_graph_data(self.cfg.archived),
                self.on_link_clicked,
            )
            self.content_stack.add_named(self.graph_view, "graph")

        self.graph_view.update_data(
            self.graph_manager.get_graph_data(self.cfg.archived)
        )

        self.content_stack.set_visible_child_name("graph")
        self.update_header_ui("Knowledge Graph", is_editor=False)

    def on_settings_clicked(self, btn):
        if not self.settings_view:
            self.settings_view = SettingsView(
                self.apply_theme,
                self.on_settings_config_changed,
                self.on_select_folder,
                {
                    "notes_folder": self.cfg.get("notes_folder"),
                    "show_toolbar": self.cfg.get("show_toolbar"),
                    "show_stats": self.cfg.get("show_stats"),
                    "sakura_effect": self.cfg.get("sakura_effect"),
                    "mcp_server_enabled": self.cfg.get("mcp_server_enabled"),
                    "mcp_server_port": self.cfg.get("mcp_server_port"),
                    "theme": self.cfg.get("theme"),
                },
            )

            self.content_stack.add_named(self.settings_view, "settings")

        self.content_stack.set_visible_child_name("settings")
        self.update_header_ui("Settings", is_editor=False)

    def apply_theme(self, theme_name):
        theme_path = self.base_dir / "themes" / f"{theme_name}.css"
        if theme_path.exists():
            self.theme_provider.load_from_path(str(theme_path))
            # Reload style.css on top of theme variables
            style_path = self.base_dir / "style.css"
            if style_path.exists():
                self.style_provider.load_from_path(str(style_path))

            self.cfg.set("theme", theme_name)

            # Update highlighter colors
            if self.highlighter:
                self.highlighter.update_theme(theme_name)

            # Set color scheme based on theme
            style_manager = Adw.StyleManager.get_default()
            if "light" in theme_name:
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
                if hasattr(self, "win"):
                    self.win.add_css_class("light-theme")
                    self.win.remove_css_class("dark-theme")
            else:
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                if hasattr(self, "win"):
                    self.win.add_css_class("dark-theme")
                    self.win.remove_css_class("light-theme")

    def handle_deadline_click(self, x, y, note_name=None, line_num=None, widget=None):
        """Helper to launch DeadlinePicker."""

        def on_date_selected(deadline):
            if note_name and line_num:
                self.notes_manager.update_deadline(note_name, line_num, deadline)
                self.refresh_dashboard()
                self.refresh_list(self.sidebar.search_entry.get_text())
                if self.current_note == note_name:
                    # Update only the specific line
                    start_iter = self.highlighter.get_iter_at_line(line_num - 1)
                    end_iter = start_iter.copy()
                    if not end_iter.ends_line():
                        end_iter.forward_to_line_end()

                    new_line_content = self.notes_manager.read_note(note_name).split(
                        "\n"
                    )[line_num - 1]

                    self.buffer.handler_block(self.changed_handler_id)
                    self.buffer.delete(start_iter, end_iter)
                    self.buffer.insert(start_iter, new_line_content)
                    self.buffer.handler_unblock(self.changed_handler_id)
                    self.update_highlighting()

        picker = DeadlinePicker(on_date_selected)

        # Position the picker precisely at the click coordinates
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1

        picker.set_parent(self.text_view if widget is None else widget)
        picker.set_pointing_to(rect)
        picker.popup()

    def on_dashboard_deadline_click(self, cb, x, y):
        """Called from Dashboard rows."""
        self.handle_deadline_click(x, y, cb["note"], cb["line"])

    def refresh_dashboard(self, filter_type="today"):
        checkboxes = self.notes_manager.get_all_checkboxes(exclude=self.cfg.archived)
        count = self.dashboard_view.populate(checkboxes, filter_type)
        title = f"Dashboard — {count} items" if count else "Dashboard"
        self.win.set_title(title)

    def on_dashboard_empty(self, filter_type):
        msg = f"No tasks for {filter_type}."
        if filter_type == "all":
            msg = "No tasks found."
        widget = create_empty_state_widget(msg, self.base_dir)
        self.dashboard_list.append(widget)

    def on_dashboard_checkbox_toggled(self, cb, checked):
        self.notes_manager.update_checkbox(cb["note"], cb["line"], checked)

        if checked and self.cfg.get("sakura_effect"):
            self.sakura_overlay.start_celebration()

        self.refresh_dashboard(self.dashboard_view.active_filter)

    def _select_sidebar_row(self, note_name: str) -> bool:
        """Select the sidebar row matching note_name (case-insensitive).
        Returns True if found, False otherwise."""
        name_lower = note_name.lower()
        for list_box in (self.sidebar.main_list, self.sidebar.archive_list):
            row = list_box.get_first_child()
            while row:
                if getattr(row, "note_name", "").lower() == name_lower:
                    list_box.select_row(row)
                    return True
                row = row.get_next_sibling()
        return False

    def _flush_pending_save(self) -> None:
        """Immediately write any pending note content to disk."""
        if self.rename_timeout_id > 0:
            GLib.source_remove(self.rename_timeout_id)
            self.rename_timeout_id = 0
        if self.current_note:
            start, end = self.buffer.get_bounds()
            content = self.buffer.get_text(start, end, True)
            if content.strip():
                self.notes_manager.save_note(self.current_note, content)

    def _maybe_exit_archive_view(self) -> None:
        """If the archive is now empty and we're viewing it, switch back to main."""
        if (
            not self.cfg.archived
            and self.sidebar.stack.get_visible_child_name() == "archive"
        ):
            self.sidebar.stack.set_visible_child_name("main")
            self.sidebar.archived_nav_btn.set_label("Archived Notes")

    def on_dashboard_item_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Reserved: called when a dashboard row is selected.
        Currently unused; row-level navigation is handled via row click gestures."""

    def handle_row_click(self, gesture, n_press, x, y, cb):
        self.content_stack.set_visible_child_name("editor")
        for list_box in (self.sidebar.main_list, self.sidebar.archive_list):
            sidebar_row = list_box.get_first_child()
            while sidebar_row:
                if getattr(sidebar_row, "note_name", "").lower() == cb["note"].lower():
                    list_box.select_row(sidebar_row)
                    GLib.idle_add(self.scroll_to_line, cb["line"])
                    return
                sidebar_row = sidebar_row.get_next_sibling()

    def scroll_to_line(self, line_num: int):
        success, it = self.buffer.get_iter_at_line(line_num - 1)
        if not success:
            return False
        mark = self.buffer.create_mark(None, it, True)
        self.text_view.scroll_to_mark(mark, 0.0, True, 0.5, 0.1)
        self.buffer.delete_mark(mark)
        return False

    def on_sidebar_toggled(self, button):
        visible = button.get_active()
        self.split_view.set_show_sidebar(visible)
        self.cfg.set("show_sidebar", visible)

    def show_export_dialog(self, title, body, is_error=False):
        dialog = Adw.MessageDialog(transient_for=self.win, heading=title, body=body)
        dialog.add_response("ok", "OK")
        if is_error:
            dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.present()

    def on_link_clicked(self, note_name):
        self.content_stack.set_visible_child_name("editor")
        for list_box in (self.sidebar.main_list, self.sidebar.archive_list):
            row = list_box.get_first_child()
            while row:
                if getattr(row, "note_name", "").lower() == note_name.lower():
                    list_box.select_row(row)
                    return
                row = row.get_next_sibling()

    def update_highlighting(self, immediate=False):
        if immediate:
            self._do_highlight()
        else:
            GLib.idle_add(self._do_highlight)

    def _do_highlight(self):
        # Skip if not in editor view
        if not self.highlighter or self.content_stack.get_visible_child_name() != "editor":
            return False
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        self.buffer.handler_block(self.changed_handler_id)
        self.highlighter.highlight(cursor_line=cursor_line)
        self.buffer.handler_unblock(self.changed_handler_id)
        self.last_cursor_line = cursor_line
        return False

    def on_cursor_moved(self, buffer, pspec):
        # Skip if not in editor view or no highlighter
        if not self.highlighter or self.is_loading or self.content_stack.get_visible_child_name() != "editor":
            return

        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()

        if cursor_line != self.last_cursor_line:
            self.buffer.handler_block(self.changed_handler_id)

            # Re-highlight the line the cursor LEFT (to restore invisible tags)
            if self.last_cursor_line != -1:
                self.highlighter.highlight(
                    start_line=self.last_cursor_line, end_line=self.last_cursor_line + 1
                )

            # Highlight the line the cursor ENTERED (to show dim tags)
            self.highlighter.highlight(
                start_line=cursor_line,
                end_line=cursor_line + 1,
                cursor_line=cursor_line,
            )

            self.buffer.handler_unblock(self.changed_handler_id)
            self.last_cursor_line = cursor_line

    def update_header_ui(self, title, is_editor=True):
        if is_editor:
            self.content_title.set_label(title)
            self.pdf_btn.set_visible(True)
            self.copy_btn.set_visible(True)
        else:
            self.content_title.set_markup(f"<b>{title}</b>")
            self.pdf_btn.set_visible(False)
            self.copy_btn.set_visible(False)

    def on_search_shortcut(self):
        self.sidebar.search_entry.grab_focus()
        return True

    def on_escape_shortcut(self):
        current_page = self.content_stack.get_visible_child_name()
        if current_page in ["dashboard", "graph", "settings"]:
            self.content_stack.set_visible_child_name("editor")
            if self.current_note:
                self.update_header_ui(self.current_note, is_editor=True)
            else:
                self.update_header_ui("Tokyo Notes", is_editor=True)
            return True
        elif self.sidebar.search_entry.has_focus():
            self.sidebar.search_entry.set_text("")
            self.text_view.grab_focus()
            return True
        return False

    def on_click_pressed(self, gesture, n_press, x, y):
        self.handle_link_click(x, y)

    def handle_link_click(self, x, y):
        bx, by = self.text_view.window_to_buffer_coords(
            Gtk.TextWindowType.TEXT, int(x), int(y)
        )
        success, cursor_iter = self.text_view.get_iter_at_location(bx, by)
        if not success:
            return

        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_end = cursor_iter.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()

        line_text = self.buffer.get_text(line_start, line_end, True)
        click_col = cursor_iter.get_line_offset()

        for kind, pattern in _CLICK_PATTERNS:
            for m in pattern.finditer(line_text):
                if m.start() <= click_col <= m.end():
                    self._dispatch_click(kind, m, x, y, cursor_iter)
                    return

    def _dispatch_click(self, kind, match, x, y, cursor_iter):
        if kind == "wiki":
            self.on_link_clicked(match.group(1))
        elif kind == "mdlink":
            url = match.group(3)
            if url.startswith("http"):
                webbrowser.open_new_tab(url)
            else:
                self.on_link_clicked(url.rsplit(".", 1)[0])
        elif kind == "url":
            webbrowser.open_new_tab(match.group(0))
        elif kind == "tag":
            self.sidebar.search_entry.set_text(match.group(0))
            self.on_search_changed(self.sidebar.search_entry)
        elif kind == "deadline":
            self.handle_deadline_click(
                x, y, self.current_note, cursor_iter.get_line() + 1
            )

    def on_search_changed(self, entry):
        if self.search_timeout_id:
            GLib.source_remove(self.search_timeout_id)

        self.search_timeout_id = GLib.timeout_add(
            150, self.do_delayed_search, entry.get_text()
        )

    def do_delayed_search(self, text):
        self.search_timeout_id = 0
        self.refresh_list(text)
        return False

    def do_delayed_highlight(self):
        self.highlight_timeout_id = 0
        self.update_highlighting()
        return False

    def do_delayed_images(self):
        self.image_timeout_id = 0
        start, end = self.buffer.get_bounds()
        if "![" in self.buffer.get_text(start, end, False):
            note_dir = Path(self.notes_manager.notes_dir).resolve()
            self.editor.update_images(note_dir)
        return False

    def do_delayed_save(self):
        self.rename_timeout_id = 0
        if not self.current_note:
            return False

        start, end = self.buffer.get_bounds()
        content = self.buffer.get_text(start, end, True)

        # Empty-note guard check file existence
        note_path = Path(self.notes_manager.notes_dir) / f"{self.current_note}.md"
        if not content.strip() and not note_path.exists():
            return False

        match = re.search(r"^# (.+)$", content, re.MULTILINE)
        if match:
            new_title = "".join(
                [
                    c
                    for c in match.group(1).strip()
                    if c.isalnum() or c in (" ", "-", "_")
                ]
            ).strip()
            if new_title and new_title != self.current_note:
                # Collision check
                if not (
                    Path(self.notes_manager.notes_dir) / f"{new_title}.md"
                ).exists():
                    if self.notes_manager.rename_note(self.current_note, new_title):
                        self.current_note = new_title
                        self.content_title.set_label(self.current_note)
                        self.refresh_list()  # Rename requires UI update

        self.notes_manager.save_note(self.current_note, content)
        return False

    def on_text_changed(self, buffer):
        if self.is_loading or not self.current_note or self.editor.is_updating_images:
            return

        # Update word counts if status bar is visible
        if self.editor.status_bar.get_visible():
            self.update_stats()

        if self.highlight_timeout_id > 0:
            GLib.source_remove(self.highlight_timeout_id)
        self.highlight_timeout_id = GLib.timeout_add(100, self.do_delayed_highlight)

        if self.image_timeout_id > 0:
            GLib.source_remove(self.image_timeout_id)
        self.image_timeout_id = GLib.timeout_add(2000, self.do_delayed_images)

        if self.rename_timeout_id > 0:
            GLib.source_remove(self.rename_timeout_id)
        self.rename_timeout_id = GLib.timeout_add(1000, self.do_delayed_save)


if __name__ == "__main__":
    app = TokyoNotes()
    app.run(sys.argv)
