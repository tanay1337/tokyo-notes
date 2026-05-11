"""Tokyo Notes — main application entry point."""
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from core.actions import ActionsHandler
from core.config import ConfigManager
from core.crash_handler import install as install_crash_handler
from core.highlighter import MarkdownHighlighter
from core.instance_lock import InstanceLock
from core.navigation import NavigationController
from core.note_lifecycle import NoteLifecycleManager
from core.search import SearchController
from core.shortcuts import setup_shortcuts
from core.startup_checks import validate_notes_folder
from core.storage import NotesManager
from core.theme_manager import ThemeManager
from core.window_manager import WindowManager
from ui.click_dispatcher import ClickDispatcher
from ui.deadline_picker import DeadlinePicker
from ui.editor import Editor
from ui.sakura_overlay import SakuraOverlay
from ui.sidebar import Sidebar
from ui.toolbar import build_toolbar

logger = logging.getLogger(__name__)


class TokyoNotes(Adw.Application):
    def __init__(self, **kwargs) -> None:
        super().__init__(application_id="com.example.TokyoNotes", **kwargs)
        self.base_dir = Path(__file__).parent

        # Subsystem managers (order matters — cfg must come before notes_manager)
        self.cfg = ConfigManager()
        self.notes_folder: str = self.cfg.get("notes_folder")
        self.notes_manager = NotesManager(notes_dir=self.notes_folder)

        self.window_manager = WindowManager(self)
        self.theme_manager = ThemeManager(self)
        self.click_dispatcher = ClickDispatcher(self)
        self.actions = ActionsHandler(self)
        self.nav = NavigationController(self)
        self.lifecycle = NoteLifecycleManager(self)
        self.search = SearchController(self.refresh_list)

        # Runtime state — all timeout IDs kept together for easy auditing
        self.current_note: str | None = None
        self.is_loading: bool = False
        self.highlighter: MarkdownHighlighter | None = None
        self.highlight_timeout_id: int = 0
        self.rename_timeout_id: int = 0
        self.sidebar_update_timeout_id: int = 0
        self.image_timeout_id: int = 0
        self.search_timeout_id: int = 0
        self.changed_handler_id: int = 0
        self.last_cursor_line: int = -1

        install_crash_handler(self)
        self._setup_actions()

    # ------------------------------------------------------------------ #
    # App lifecycle
    # ------------------------------------------------------------------ #

    def do_shutdown(self) -> None:
        """Flush any pending config writes and note saves before the process exits."""
        self.cfg.flush_immediate()
        self.notes_manager.wait_until_empty()
        logger.info("Tokyo Notes shutting down")
        # PyGObject's Gio.Application.do_shutdown() takes no arguments beyond self.
        Adw.Application.do_shutdown(self)

    # ------------------------------------------------------------------ #
    # GIO actions
    # ------------------------------------------------------------------ #

    def _setup_actions(self) -> None:
        for name, handler in (
            ("delete",  self.lifecycle.on_delete_action),
            ("pin",     self.on_pin_note),
            ("unpin",   self.on_unpin_note),
            ("archive", self.on_toggle_archive_note),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", handler)
            self.add_action(action)

    def on_toggle_archive_note(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        note_name = parameter.get_string()
        self.cfg.toggle_archive(note_name)
        self.sidebar.maybe_exit_archive_view()
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_pin_note(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        self.cfg.pin(parameter.get_string())
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_unpin_note(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        self.cfg.unpin(parameter.get_string())
        self.refresh_list(self.sidebar.search_entry.get_text())

    # ------------------------------------------------------------------ #
    # Folder selection
    # ------------------------------------------------------------------ #

    def on_select_folder(self, button) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Notes Folder")
        if Path(self.notes_folder).exists():
            dialog.set_initial_folder(
                Gio.File.new_for_path(str(Path(self.notes_folder).absolute()))
            )
        dialog.select_folder(self.win, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # user cancelled

        if not folder:
            return

        new_folder = folder.get_path()
        if new_folder == self.notes_folder:
            return

        self.notes_folder = new_folder
        self.cfg.set("notes_folder", new_folder)
        self.notes_manager = NotesManager(notes_dir=new_folder)

        if self.settings_view:
            self.settings_view.update_folder_path(new_folder)

        self.current_note = None
        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.set_text("")
        self.buffer.handler_unblock(self.changed_handler_id)
        self.win.set_title("Tokyo Notes")
        self.refresh_list()

    # ------------------------------------------------------------------ #
    # Activation / window construction
    # ------------------------------------------------------------------ #

    def do_activate(self) -> None:
        # If the window already exists (second activation via D-Bus / instance
        # check), just raise it rather than building a second window.
        if hasattr(self, "win") and self.win:
            self.win.present()
            return

        self.theme_manager.setup_providers()
        self.win = self.window_manager.create_window()
        self.apply_theme(self.cfg.get("theme"))

        self.split_view = Adw.OverlaySplitView()
        self.win.set_content(self.split_view)

        # Build the toggle button before the sidebar so the header bar wiring
        # can reference it; connect the toggled signal after the sidebar exists.
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.set_active(self.cfg.get("show_sidebar"))

        self.sidebar = Sidebar(
            self,
            self.lifecycle.on_new_note,
            self.nav.on_dashboard_clicked,
            self.nav.on_archived_clicked,
            self.nav.on_graph_clicked,
        )
        self.sidebar_toggle_handler = self.sidebar_toggle.connect(
            "toggled", self.sidebar.on_sidebar_toggled
        )
        self.sidebar.main_list.connect("row-selected", self.lifecycle.on_note_selected)
        self.sidebar.archive_list.connect("row-selected", self.lifecycle.on_note_selected)
        self.split_view.set_sidebar(self.sidebar)

        self.content_header = self._build_content_header()
        self.split_view.set_show_sidebar(self.sidebar_toggle.get_active())

        self._build_editor_area()

        # Lazy views — created on first navigation to keep startup fast.
        self.settings_view = None
        self.graph_view = None
        self.graph_manager = None
        self.dashboard_view = None
        self.dashboard_list = None

        overlay = self._build_content_stack()

        main_layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_layout.append(self.content_header)
        main_layout.append(overlay)
        self.split_view.set_content(main_layout)

        self.win.present()
        self.window_manager.setup_breakpoint()

        # Validate the notes folder after the window is shown so that any
        # recovery dialog has a parent window to attach to.
        validate_notes_folder(self)

        GLib.idle_add(self.lifecycle.initial_load)
        setup_shortcuts(
            self.win,
            self.lifecycle.on_new_note_global,
            self.nav.on_dashboard_clicked,
            self.nav.on_graph_clicked,
            self.on_search_shortcut,
            self.nav.on_escape_shortcut,
            self.lifecycle.on_delete_shortcut,
            self.actions.on_insert_timestamp,
            self.actions.on_zen_mode,
            self.quit,
            on_help=self.show_shortcuts_dialog,
            on_pin=self.on_pin_shortcut,
            on_archive=self.on_archive_shortcut,
            on_settings=self.nav.on_settings_clicked,
        )
        logger.info("Tokyo Notes started — notes folder: %s", self.notes_folder)

    def _build_content_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        self.content_title = Gtk.Label(label="Tokyo Notes")
        header.set_title_widget(self.content_title)
        header.pack_start(self.sidebar_toggle)

        self.pdf_btn = Gtk.Button(
            icon_name="document-save-symbolic", tooltip_text="Export to PDF"
        )
        self.pdf_btn.connect("clicked", self.actions.on_export_pdf)
        header.pack_end(self.pdf_btn)

        self.settings_btn = Gtk.Button(
            icon_name="emblem-system-symbolic", tooltip_text="Settings"
        )
        self.settings_btn.connect("clicked", self.nav.on_settings_clicked)
        header.pack_end(self.settings_btn)

        # Back to Notes button — shown only when a secondary view is active.
        self.back_btn = Gtk.Button(
            icon_name="go-previous-symbolic", tooltip_text="Back to Notes"
        )
        self.back_btn.connect("clicked", lambda _: self.nav.on_escape_shortcut())
        self.back_btn.set_visible(False)
        header.pack_start(self.back_btn)

        return header

    def _build_editor_area(self) -> None:
        assets_dir = self.base_dir / "assets" / "toolbar"
        toolbar = build_toolbar(assets_dir, self.apply_format)

        self.editor = Editor(
            self.lifecycle.on_text_changed,
            self.on_cursor_moved,
            self.actions.on_paste_clipboard,
            toolbar,
            self.notes_manager.get_notes,
        )
        self.buffer = self.editor.buffer
        self.text_view = self.editor.text_view
        self.toolbar = self.editor.toolbar
        self.changed_handler_id = self.editor.changed_handler_id

        self.toolbar.set_visible(self.cfg.get("show_toolbar"))
        self.editor.status_bar.set_visible(self.cfg.get("show_stats"))

        self.highlighter = MarkdownHighlighter(self.buffer, self.cfg.get("theme"))
        self.highlighter.highlight()

        self.last_cursor_line = -1

        gesture = Gtk.GestureClick.new()
        gesture.set_button(1)
        gesture.connect("pressed", self.on_click_pressed)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.text_view.add_controller(gesture)
        self.text_view.set_focus_on_click(True)

    def _build_content_stack(self) -> Gtk.Overlay:
        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(200)
        self.content_stack.set_vexpand(True)
        self.content_stack.add_named(self.editor, "editor")

        self.overlay = Gtk.Overlay()
        self.sakura_overlay = SakuraOverlay()
        self.overlay.set_child(self.content_stack)
        self.overlay.add_overlay(self.sakura_overlay)
        return self.overlay

    # ------------------------------------------------------------------ #
    # Settings / theme
    # ------------------------------------------------------------------ #

    def on_settings_config_changed(self, key: str, value: Any) -> None:
        self.cfg.set(key, value)
        if key == "show_toolbar":
            self.toolbar.set_visible(value)
        elif key == "show_stats":
            self.editor.status_bar.set_visible(value)

    def apply_theme(self, theme_name: str) -> None:
        self.theme_manager.apply_theme(theme_name)
        self.cfg.set("theme", theme_name)
        if hasattr(self, "win"):
            if "light" in theme_name:
                self.win.add_css_class("light-theme")
                self.win.remove_css_class("dark-theme")
            else:
                self.win.add_css_class("dark-theme")
                self.win.remove_css_class("light-theme")

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #

    def apply_format(self, btn, prefix: str, suffix: str) -> None:
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(start, end, True)
            self.buffer.delete(start, end)
            is_block = not suffix and prefix.rstrip() != prefix
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

    # ------------------------------------------------------------------ #
    # Note list / sidebar
    # ------------------------------------------------------------------ #

    def refresh_list(self, filter_text: str = "") -> None:
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

    def on_row_right_click(
        self, gesture, n_press, x, y, row, is_archived: bool = False
    ) -> None:
        note_name = getattr(row, "note_name", None)
        if not note_name:
            return

        menu = Gio.Menu()
        menu.append("Delete", f"app.delete::{note_name}")
        if note_name in self.cfg.pinned:
            menu.append("Unpin", f"app.unpin::{note_name}")
        else:
            menu.append("Pin", f"app.pin::{note_name}")
        menu.append(
            "Unarchive" if is_archived else "Archive",
            f"app.archive::{note_name}",
        )

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        popover.set_pointing_to(Gdk.Rectangle(x=x, y=y, width=1, height=1))
        popover.popup()

    def _select_sidebar_row(self, note_name: str) -> bool:
        """Select the sidebar row matching *note_name* (case-insensitive).

        Returns True if a row was found and selected.
        """
        name_lower = note_name.lower()
        for list_box in (self.sidebar.main_list, self.sidebar.archive_list):
            row = list_box.get_first_child()
            while row:
                if getattr(row, "note_name", "").lower() == name_lower:
                    list_box.select_row(row)
                    return True
                row = row.get_next_sibling()
        return False

    # ------------------------------------------------------------------ #
    # Save helpers
    # ------------------------------------------------------------------ #

    def _flush_pending_save(self) -> None:
        """Write buffered changes to disk immediately before switching notes.

        Also cancels the sidebar-update timer so a stale callback cannot
        corrupt the row for the next note.
        """
        for attr in ("rename_timeout_id", "sidebar_update_timeout_id"):
            tid = getattr(self, attr)
            if tid > 0:
                GLib.source_remove(tid)
                setattr(self, attr, 0)

        if self.current_note:
            start, end = self.buffer.get_bounds()
            content = self.buffer.get_text(start, end, True)
            if content.strip():
                self.notes_manager.save_note(self.current_note, content)

    def _reschedule(self, timeout_attr: str, delay_ms: int, callback: Callable) -> None:
        """Cancel any pending GLib timeout stored in *timeout_attr* and reschedule."""
        current_id = getattr(self, timeout_attr)
        if current_id > 0:
            GLib.source_remove(current_id)
        setattr(self, timeout_attr, GLib.timeout_add(delay_ms, callback))

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self.search.on_search_changed(entry)

    def _current_sidebar_note(self) -> str | None:
        """Return the note_name of the currently selected sidebar row, if any."""
        for lb in (self.sidebar.main_list, self.sidebar.archive_list):
            row = lb.get_selected_row()
            if row and hasattr(row, "note_name"):
                return row.note_name
        return None

    def on_pin_shortcut(self) -> bool:
        """Ctrl+Shift+P — toggle pin on the currently selected note."""
        name = self._current_sidebar_note()
        if name:
            if name in self.cfg.pinned:
                self.cfg.unpin(name)
            else:
                self.cfg.pin(name)
            self.refresh_list(self.sidebar.search_entry.get_text())
        return True

    def on_archive_shortcut(self) -> bool:
        """Ctrl+Shift+A — toggle archive on the currently selected note."""
        name = self._current_sidebar_note()
        if name:
            self.cfg.toggle_archive(name)
            self.sidebar.maybe_exit_archive_view()
            self.refresh_list(self.sidebar.search_entry.get_text())
        return True

    def on_search_shortcut(self) -> bool:
        entry = self.sidebar.search_entry
        if entry.has_focus() and entry.get_text():
            # Ctrl+F when search already focused and has text: clear it.
            entry.set_text("")
            self.refresh_list()
        else:
            entry.grab_focus()
        return True

    def show_shortcuts_dialog(self) -> bool:
        """Show a keyboard shortcuts reference dialog (Ctrl+H)."""
        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading="Keyboard Shortcuts",
            body=(
                "Ctrl+N           New note\n"
                "Ctrl+D           Dashboard\n"
                "Ctrl+G           Knowledge graph\n"
                "Ctrl+F           Search  (press again to clear)\n"
                "Ctrl+H           This help dialog\n"
                "Ctrl+Q           Quit\n"
                "Escape           Back to editor / clear search\n"
                "Delete           Delete selected note\n"
                "Ctrl+Shift+P     Pin / unpin note\n"
                "Ctrl+Shift+A     Archive / unarchive note\n"
                "Ctrl+Shift+S     Settings\n"
                "Ctrl+Shift+T     Insert timestamp\n"
                "Ctrl+Shift+Z     Zen mode\n"
                "\n"
                "In editor:\n"
                "[[               Open note link picker\n"
                "@                Open deadline picker\n"
                "Enter            Continue list / task list"
            ),
        )
        dialog.add_response("ok", "OK")
        dialog.present()
        return True

    # ------------------------------------------------------------------ #
    # Dialogs
    # ------------------------------------------------------------------ #

    def show_export_dialog(self, title: str, body: str, is_error: bool = False) -> None:
        dialog = Adw.MessageDialog(transient_for=self.win, heading=title, body=body)
        dialog.add_response("ok", "OK")
        if is_error:
            dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.present()

    # ------------------------------------------------------------------ #
    # Cursor / click
    # ------------------------------------------------------------------ #

    def on_cursor_moved(self, buffer: Gtk.TextBuffer, pspec: object) -> None:
        if (
            not self.highlighter
            or self.is_loading
            or self.content_stack.get_visible_child_name() != "editor"
        ):
            return
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        if cursor_line == self.last_cursor_line:
            return
        self.buffer.handler_block(self.changed_handler_id)
        if self.last_cursor_line != -1:
            self.highlighter.highlight(
                start_line=self.last_cursor_line,
                end_line=self.last_cursor_line + 1,
            )
        self.highlighter.highlight(
            start_line=cursor_line,
            end_line=cursor_line + 1,
            cursor_line=cursor_line,
        )
        self.buffer.handler_unblock(self.changed_handler_id)
        self.last_cursor_line = cursor_line

    def on_click_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        self.click_dispatcher.handle_click(x, y)

    # ------------------------------------------------------------------ #
    # Highlighting
    # ------------------------------------------------------------------ #

    def update_highlighting(self, immediate: bool = False) -> None:
        if immediate:
            self._do_highlight()
        else:
            GLib.idle_add(self._do_highlight)

    def _do_highlight(self) -> bool:
        if (
            not self.highlighter
            or self.content_stack.get_visible_child_name() != "editor"
        ):
            return False
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        self.buffer.handler_block(self.changed_handler_id)
        self.highlighter.highlight(cursor_line=cursor_line)
        self.buffer.handler_unblock(self.changed_handler_id)
        self.last_cursor_line = cursor_line
        return False

    def do_delayed_highlight(self) -> bool:
        self.highlight_timeout_id = 0
        self.update_highlighting()
        return False

    def do_delayed_images(self) -> bool:
        self.image_timeout_id = 0
        start, end = self.buffer.get_bounds()
        if "![" in self.buffer.get_text(start, end, False):
            self.editor.update_images(Path(self.notes_manager.notes_dir).resolve())
        return False

    # ------------------------------------------------------------------ #
    # Dashboard callbacks
    # ------------------------------------------------------------------ #

    def on_dashboard_item_selected(
        self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow
    ) -> None:
        # Row-click gestures handle navigation; this signal is intentionally unused.
        pass

    def on_dashboard_deadline_click(self, cb: dict, x: float, y: float) -> None:
        self.handle_deadline_click(x, y, cb["note"], cb["line"])

    def on_dashboard_checkbox_toggled(self, cb: dict, checked: bool) -> None:
        # Flush any pending debounced save for this note before modifying it
        # on disk. Without this, the debounced save fires after update_checkbox
        # and overwrites the toggle with the old buffer content.
        if self.current_note == cb["note"] and self.rename_timeout_id > 0:
            self._flush_pending_save()

        self.notes_manager.update_checkbox(cb["note"], cb["line"], checked)

        # If the toggled note is open in the editor, patch the buffer line
        # in place so it stays consistent with the disk without scheduling
        # another save cycle.
        if self.current_note == cb["note"]:
            self._sync_checkbox_in_buffer(cb["line"], checked)

        if checked and self.cfg.get("sakura_effect"):
            self.sakura_overlay.start_celebration()
        if self.dashboard_view is not None:
            self.nav.refresh_dashboard(self.dashboard_view.active_filter)

    def _sync_checkbox_in_buffer(self, line_num: int, checked: bool) -> None:
        """Patch a checkbox line in the editor buffer to match *checked*.

        Called after update_checkbox writes the new state to disk so that the
        buffer stays in sync without triggering the debounced save again.
        """
        import re as _re
        success, line_start = self.buffer.get_iter_at_line(line_num - 1)
        if not success:
            return
        line_end = line_start.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()
        line_text = self.buffer.get_text(line_start, line_end, False)
        new_text = _re.sub(
            r"\[[ xX]\]",
            "[x]" if checked else "[ ]",
            line_text,
            count=1,
        )
        if new_text == line_text:
            return  # nothing to patch
        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.delete(line_start, line_end)
        # Re-fetch iter after delete (line_start is now invalid)
        success, insert_iter = self.buffer.get_iter_at_line(line_num - 1)
        if success:
            self.buffer.insert(insert_iter, new_text)
        self.buffer.handler_unblock(self.changed_handler_id)

    # ------------------------------------------------------------------ #
    # Deadline picker
    # ------------------------------------------------------------------ #

    def handle_deadline_click(
        self,
        x: float,
        y: float,
        note_name: str | None = None,
        line_num: int | None = None,
        widget: Gtk.Widget | None = None,
    ) -> None:
        picker = DeadlinePicker(
            lambda deadline: self._apply_deadline_update(note_name, line_num, deadline)
        )
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        picker.set_parent(widget or self.text_view)
        picker.set_pointing_to(rect)
        picker.popup()

    def _apply_deadline_update(
        self,
        note_name: str | None,
        line_num: int | None,
        deadline: str,
    ) -> None:
        if not note_name or not line_num:
            return
        self.notes_manager.update_deadline(note_name, line_num, deadline)
        if self.dashboard_view is not None:
            self.nav.refresh_dashboard(self.dashboard_view.active_filter)
        self.refresh_list(self.sidebar.search_entry.get_text())
        if self.current_note == note_name:
            self._update_deadline_line_in_buffer(note_name, line_num)

    def _update_deadline_line_in_buffer(self, note_name: str, line_num: int) -> None:
        success, start_iter = self.buffer.get_iter_at_line(line_num - 1)
        if not success:
            return
        end_iter = start_iter.copy()
        if not end_iter.ends_line():
            end_iter.forward_to_line_end()
        new_line = self.notes_manager.read_note(note_name).split("\n")[line_num - 1]
        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.delete(start_iter, end_iter)
        self.buffer.insert(start_iter, new_line)
        self.buffer.handler_unblock(self.changed_handler_id)
        self.update_highlighting()


if __name__ == "__main__":
    from core.logging_setup import configure_logging
    configure_logging()

    lock = InstanceLock()
    if not lock.acquire():
        # A second instance started — show a minimal error and exit.
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw as _Adw
        _app = _Adw.Application(application_id="com.example.TokyoNotes.Lock")
        def _on_activate(_app):
            dialog = _Adw.MessageDialog(
                heading="Tokyo Notes is already running",
                body="Only one instance can run at a time.",
            )
            dialog.add_response("ok", "OK")
            dialog.connect("response", lambda *_: _app.quit())
            dialog.present()
        _app.connect("activate", _on_activate)
        _app.run(sys.argv)
        lock.release()
        sys.exit(0)

    try:
        app = TokyoNotes()
        exit_code = app.run(sys.argv)
    finally:
        lock.release()

    sys.exit(exit_code)
