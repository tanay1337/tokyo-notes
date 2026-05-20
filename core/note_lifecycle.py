"""Note lifecycle manager — owns open, create, delete, save, and rename logic."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core.services import build_stats, derive_display_title, patch_sidebar_row, update_note_title
from core.utils import get_snippet

if TYPE_CHECKING:
    from main import TokyoNotes


class NoteLifecycleManager:
    """Coordinates note open/create/delete/save/rename between the UI and NotesManager."""

    def __init__(self, app: "TokyoNotes") -> None:
        self.app = app

    # Startup

    def initial_load(self) -> bool:
        app = self.app
        app.refresh_list()
        notes = app.notes_manager.get_notes()
        if notes:
            most_recent = notes[0]
            if app._select_sidebar_row(most_recent):
                for lb in (app.sidebar.main_list, app.sidebar.archive_list):
                    row = lb.get_selected_row()
                    if row:
                        self.on_note_selected(lb, row)
                        break
        else:
            self.on_new_note(None)
        return False

    # Create

    def on_new_note_global(self, *args: Any) -> bool:
        self.on_new_note(None)
        self.app.text_view.grab_focus()
        return True

    def on_new_note(self, btn: Gtk.Button | None) -> None:
        app = self.app
        app._flush_pending_save()
        name = app.notes_manager.reserve_name()
        app.current_note = name
        app.nav.update_header_ui(name, is_editor=True)
        app._has_images = False
        app.buffer.handler_block(app.changed_handler_id)
        app.buffer.set_text("")
        app.buffer.handler_unblock(app.changed_handler_id)
        app.content_stack.set_visible_child_name("editor")
        app.text_view.grab_focus()

    # Open / select

    def on_note_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        app = self.app
        if not row or app.is_loading:
            return
        note_name = getattr(row, "note_name", None)
        if not note_name or note_name == app.current_note:
            return

        app._flush_pending_save()
        app.is_loading = True
        try:
            app.current_note = note_name
            app.nav.update_header_ui(app.current_note, is_editor=True)
            app.sidebar.set_active_view("editor")
            content = app.notes_manager.read_note(app.current_note)
            app._has_images = "![" in content

            app.buffer.handler_block(app.changed_handler_id)
            app.buffer.set_text(content)
            app.content_stack.set_visible_child_name("editor")

            # Scroll to top for consistent load position.
            start = app.buffer.get_start_iter()
            app.buffer.place_cursor(start)
            app.text_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)

            if app.highlighter:
                app.highlighter.highlight(start_line=0, end_line=30)
            app.buffer.handler_unblock(app.changed_handler_id)

            if hasattr(app, "_pending_highlight_id") and app._pending_highlight_id:
                GLib.source_remove(app._pending_highlight_id)
                app._pending_highlight_id = 0
            if app.highlighter and app.buffer.get_line_count() > 30:
                app._pending_highlight_id = GLib.idle_add(
                    self._highlight_chunk,
                    app.current_note,
                    30,
                )

            app.last_cursor_line = -1
            app._update_backlinks()

            if listbox == app.sidebar.main_list:
                app.sidebar.archive_list.unselect_all()
            else:
                app.sidebar.main_list.unselect_all()
        finally:
            app.is_loading = False

    def _highlight_chunk(self, expected_note: str, start_line: int) -> bool:
        app = self.app
        if not app.highlighter or app.current_note != expected_note:
            return False
        total = app.buffer.get_line_count()
        end_line = min(start_line + 50, total)
        app.buffer.handler_block(app.changed_handler_id)
        app.highlighter.highlight(start_line=start_line, end_line=end_line)
        app.buffer.handler_unblock(app.changed_handler_id)
        if end_line < total:
            GLib.idle_add(self._highlight_chunk, expected_note, end_line)
        return False

    # Navigate

    def on_link_clicked(self, note_name: str) -> None:
        self.app.content_stack.set_visible_child_name("editor")
        self.app._select_sidebar_row(note_name)

    def handle_row_click(
        self, gesture: Any, n_press: int, x: float, y: float, cb: dict
    ) -> None:
        app = self.app
        app.content_stack.set_visible_child_name("editor")
        if app._select_sidebar_row(cb["note"]):
            GLib.idle_add(self.scroll_to_line, cb["line"])

    def scroll_to_line(self, line_num: int) -> bool:
        app = self.app
        success, it = app.buffer.get_iter_at_line(line_num - 1)
        if not success:
            return False
        mark = app.buffer.create_mark(None, it, True)
        app.text_view.scroll_to_mark(mark, 0.0, True, 0.5, 0.1)
        app.buffer.delete_mark(mark)
        return False

    # Delete

    def on_delete_shortcut(self) -> bool:
        app = self.app
        main_row = app.sidebar.main_list.get_selected_row()
        arch_row = app.sidebar.archive_list.get_selected_row()
        row = main_row if (main_row and hasattr(main_row, "note_name")) else arch_row
        if row and hasattr(row, "note_name"):
            self.on_delete_action(None, GLib.Variant("s", row.note_name))
        return True

    def on_delete_action(self, action: Any, parameter: GLib.Variant) -> None:
        app = self.app
        note_name = parameter.get_string()
        content = app.notes_manager.read_note(note_name).strip()
        if not content:
            self.confirm_delete(note_name)
        else:
            dialog = Adw.MessageDialog(
                transient_for=app.win,
                heading="Delete Note?",
                body=(
                    f"Are you sure you want to delete '{note_name}'?"
                    " This action cannot be undone."
                ),
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            try:
                dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            except Exception:
                pass
            dialog.connect("response", self.on_delete_dialog_response, note_name)
            dialog.present()

    def on_delete_dialog_response(
        self, dialog: Adw.MessageDialog, response: str, note_name: str
    ) -> None:
        if response == "delete":
            self.confirm_delete(note_name)

    def confirm_delete(self, note_name: str) -> None:
        app = self.app
        # Cancel pending timers before clearing the buffer so they don't
        # re-create the deleted file.
        if app.current_note == note_name:
            for attr in ("rename_timeout_id", "sidebar_update_timeout_id"):
                tid = getattr(app, attr)
                if tid > 0:
                    GLib.source_remove(tid)
                    setattr(app, attr, 0)
        app.notes_manager.delete_note(note_name)
        app.cfg.remove_note(note_name)
        app.sidebar.maybe_exit_archive_view()
        if app.current_note == note_name:
            app.current_note = None
            app.buffer.handler_block(app.changed_handler_id)
            app.buffer.set_text("")
            app.buffer.handler_unblock(app.changed_handler_id)
            app.win.set_title("Tokyo Notes")
        app.refresh_list(app.sidebar.search_entry.get_text())

    # Live sidebar update (150 ms debounce)

    def _update_sidebar_and_stats(self) -> bool:
        """Fast debounce callback: update sidebar row and status bar in-place."""
        app = self.app
        app.sidebar_update_timeout_id = 0
        current = app.current_note
        if not current:
            return False

        start, end = app.buffer.get_bounds()
        content = app.buffer.get_text(start, end, True)
        display_title = derive_display_title(content, current)
        snippet = get_snippet(content)

        row_found = False
        for lb in (app.sidebar.main_list, app.sidebar.archive_list):
            child = lb.get_first_child()
            while child:
                if getattr(child, "note_name", None) == current:
                    patch_sidebar_row(child, title=display_title, snippet=snippet)
                    row_found = True
                    break
                child = child.get_next_sibling()
            if row_found:
                break

        if not row_found and content.strip():
            app.refresh_list(app.sidebar.search_entry.get_text())
            app._select_sidebar_row(current)

        if app.editor.status_bar.get_visible():
            app.editor.stats_label.set_label(build_stats(content))

        return False

    # Save / rename (1 000 ms debounce)

    def do_delayed_save(self) -> bool:
        app = self.app
        app.rename_timeout_id = 0
        if not app.current_note:
            return False

        start, end = app.buffer.get_bounds()
        content = app.buffer.get_text(start, end, True)

        if not content.strip():
            return False

        old_name = app.current_note
        new_name, did_rename = update_note_title(
            old_name=old_name,
            content=content,
            notes_manager=app.notes_manager,
        )

        if did_rename:
            app.current_note = new_name
            app.nav.update_header_ui(new_name, is_editor=True)

        app.notes_manager.save_note(app.current_note, content)

        # Rebuild the sidebar list so the new / renamed note appears.
        app.refresh_list(app.sidebar.search_entry.get_text())
        app._select_sidebar_row(app.current_note)

        return False

    # Text-changed coordination

    def on_text_changed(self, buffer: Gtk.TextBuffer) -> None:
        app = self.app
        if app.is_loading or not app.current_note or app.editor.is_updating_images:
            return
        app._reschedule("sidebar_update_timeout_id", 150, self._update_sidebar_and_stats)
        app._reschedule("highlight_timeout_id", 100, app.do_delayed_highlight)
        if not app._has_images:
            cursor = app.buffer.get_iter_at_mark(app.buffer.get_insert())
            cursor.set_line_offset(0)
            end_of_line = cursor.copy()
            if not end_of_line.ends_line():
                end_of_line.forward_to_line_end()
            if "![" in app.buffer.get_text(cursor, end_of_line, False):
                app._has_images = True
        if app._has_images:
            app._reschedule("image_timeout_id", 2000, app.do_delayed_images)
        app._reschedule("rename_timeout_id", 1000, self.do_delayed_save)

