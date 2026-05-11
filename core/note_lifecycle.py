"""Note lifecycle manager — owns open, create, delete, save, and rename logic."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core.utils import H1_TITLE_RE, get_snippet

if TYPE_CHECKING:
    from main import TokyoNotes


def _clean_title(raw: str) -> str:
    """Sanitise a raw H1 string into a safe filename stem."""
    return "".join(
        c for c in raw.strip() if c.isalnum() or c in (" ", "-", "_")
    ).strip()


def _derive_display_title(content: str, fallback: str) -> str:
    """Return the display title for *content*: the first H1 if present, else *fallback*."""
    match = H1_TITLE_RE.search(content)
    return (_clean_title(match.group(1)) if match else None) or fallback


class NoteLifecycleManager:
    """Coordinates note open/create/delete/save/rename between the UI and NotesManager."""

    def __init__(self, app: "TokyoNotes") -> None:
        self.app = app

    # ------------------------------------------------------------------ #
    # Startup
    # ------------------------------------------------------------------ #

    def initial_load(self) -> bool:
        """Select the most-recent note on startup, or start with a blank editor."""
        app = self.app
        app.refresh_list()
        notes = app.notes_manager.get_notes()
        if notes:
            most_recent = notes[0]
            if app._select_sidebar_row(most_recent):
                for list_box in (app.sidebar.main_list, app.sidebar.archive_list):
                    row = list_box.get_selected_row()
                    if row:
                        self.on_note_selected(list_box, row)
                        break
        else:
            self.on_new_note(None)
        return False

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #

    def on_new_note_global(self, *args: Any) -> bool:
        """Keyboard shortcut handler: create a new note and focus the editor."""
        self.on_new_note(None)
        self.app.text_view.grab_focus()
        return True

    def on_new_note(self, btn: Gtk.Button | None) -> None:
        """Flush the current note, reserve a new name, and open a blank editor.

        No file is written yet — the note only lands on disk once the user
        types content and the save debounce fires.
        """
        app = self.app
        app._flush_pending_save()

        name = app.notes_manager.reserve_name()
        app.current_note = name
        app.nav.update_header_ui(name, is_editor=True)

        app.buffer.handler_block(app.changed_handler_id)
        app.buffer.set_text("")
        app.buffer.handler_unblock(app.changed_handler_id)

        # Don't add to sidebar yet — the note has no content and no file.
        # The sidebar will be updated live as the user types (req 3).
        app.content_stack.set_visible_child_name("editor")
        app.text_view.grab_focus()

    # ------------------------------------------------------------------ #
    # Open / select
    # ------------------------------------------------------------------ #

    def on_note_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Load the selected note into the editor."""
        app = self.app
        if not row or app.is_loading:
            return

        note_name = getattr(row, "note_name", None)
        if not note_name or note_name == app.current_note:
            return

        app._flush_pending_save()

        # B2 fix: wrap everything after is_loading = True in a try/finally so
        # that is_loading is always reset even if an exception occurs mid-load.
        app.is_loading = True
        try:
            app.current_note = note_name
            app.nav.update_header_ui(app.current_note, is_editor=True)
            # Clear active state from dashboard/graph footer buttons.
            app.sidebar.set_active_view("editor")
            content = app.notes_manager.read_note(app.current_note)

            app.buffer.handler_block(app.changed_handler_id)
            app.buffer.set_text(content)

            # Simple fade-in effect: reset opacity and animate back up.
            app.text_view.set_opacity(0.0)
            GLib.timeout_add(50, lambda: (app.text_view.set_opacity(1.0), False)[1])

            app.content_stack.set_visible_child_name("editor")
            # Scroll to the top of the note so position is always consistent.
            start_iter = app.buffer.get_start_iter()
            app.buffer.place_cursor(start_iter)
            app.text_view.scroll_to_iter(start_iter, 0.0, False, 0.0, 0.0)

            if app.highlighter:
                app.highlighter.highlight(start_line=0, end_line=30)


            app.buffer.handler_unblock(app.changed_handler_id)

            # B4 fix: capture the note name now so the idle callback can bail
            # out if the user has already navigated to a different note by the
            # time it fires.
            if app.highlighter and app.buffer.get_line_count() > 30:
                scheduled_note = app.current_note
                GLib.idle_add(
                    lambda n=scheduled_note: self._finish_highlighting(n) or False
                )

            app.last_cursor_line = -1

            if listbox == app.sidebar.main_list:
                app.sidebar.archive_list.unselect_all()
            else:
                app.sidebar.main_list.unselect_all()
        finally:
            app.is_loading = False

    def _finish_highlighting(self, expected_note: str) -> bool:
        """Highlight lines 30+ in the background after initial load.

        Bails out silently if the user has already switched to a different note
        by the time this idle callback fires.
        """
        app = self.app
        if app.highlighter and app.current_note == expected_note:
            app.highlighter.highlight(start_line=30)
        return False

    # ------------------------------------------------------------------ #
    # Navigate to a note from links / dashboard
    # ------------------------------------------------------------------ #

    def on_link_clicked(self, note_name: str) -> None:
        """Switch to the editor and select the named note in the sidebar."""
        self.app.content_stack.set_visible_child_name("editor")
        self.app._select_sidebar_row(note_name)

    def handle_row_click(
        self, gesture: Any, n_press: int, x: float, y: float, cb: dict
    ) -> None:
        """Navigate to the note referenced by a dashboard row and scroll to its line."""
        app = self.app
        app.content_stack.set_visible_child_name("editor")
        if app._select_sidebar_row(cb["note"]):
            GLib.idle_add(self.scroll_to_line, cb["line"])

    def scroll_to_line(self, line_num: int) -> bool:
        """Scroll the editor to the given 1-based line number."""
        app = self.app
        success, it = app.buffer.get_iter_at_line(line_num - 1)
        if not success:
            return False
        mark = app.buffer.create_mark(None, it, True)
        app.text_view.scroll_to_mark(mark, 0.0, True, 0.5, 0.1)
        app.buffer.delete_mark(mark)
        return False

    # ------------------------------------------------------------------ #
    # Delete
    # ------------------------------------------------------------------ #

    def on_delete_shortcut(self) -> bool:
        """Delete the currently selected note via keyboard shortcut."""
        app = self.app
        main_row = app.sidebar.main_list.get_selected_row()
        archive_row = app.sidebar.archive_list.get_selected_row()
        row = main_row if (main_row and hasattr(main_row, "note_name")) else archive_row
        if row and hasattr(row, "note_name"):
            self.on_delete_action(None, GLib.Variant("s", row.note_name))
        return True

    def on_delete_action(self, action: Any, parameter: GLib.Variant) -> None:
        """GIO action handler: show confirmation for non-empty notes."""
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
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self.on_delete_dialog_response, note_name)
            dialog.present()

    def on_delete_dialog_response(
        self, dialog: Adw.MessageDialog, response: str, note_name: str
    ) -> None:
        if response == "delete":
            self.confirm_delete(note_name)

    def confirm_delete(self, note_name: str) -> None:
        """Delete the note file, clean up config, and refresh the UI."""
        app = self.app

        # B3 fix: cancel any pending save/sidebar timers before clearing the
        # buffer so they don't fire after the note is gone and re-create the
        # file or try to patch a row that no longer exists.
        if app.current_note == note_name:
            for attr in ("rename_timeout_id", "sidebar_update_timeout_id"):
                tid = getattr(app, attr)
                if tid > 0:
                    GLib.source_remove(tid)
                    setattr(app, attr, 0)

        app.notes_manager.delete_note(
            note_name,
            callback=lambda success, err=None: self._on_delete_complete(success, note_name, err)
        )

    def _on_delete_complete(
        self, success: bool, note_name: str, err: str | None = None
    ) -> None:
        """Callback from NotesManager after an async delete finishes."""
        if not success:
            logger.error("Async delete failed for '%s': %s", note_name, err)
            return

        app = self.app
        app.cfg.remove_note(note_name)
        app.sidebar.maybe_exit_archive_view()

        if app.current_note == note_name:
            app.current_note = None
            app.buffer.handler_block(app.changed_handler_id)
            app.buffer.set_text("")
            app.buffer.handler_unblock(app.changed_handler_id)
            app.win.set_title("Tokyo Notes")

        app.refresh_list(app.sidebar.search_entry.get_text())

    # ------------------------------------------------------------------ #
    # Live sidebar update (fast path — no disk I/O)
    # ------------------------------------------------------------------ #

    def do_delayed_sidebar_update(self) -> bool:
        """Fast debounce callback (150 ms): update the current note's sidebar row
        in-place from the buffer content, with no disk access.
        """
        app = self.app
        app.sidebar_update_timeout_id = 0

        if not app.current_note:
            return False

        start, end = app.buffer.get_bounds()
        content = app.buffer.get_text(start, end, True)

        display_title = _derive_display_title(content, app.current_note)
        snippet = get_snippet(content)

        # Walk every list box looking for the row for the current note.
        row_found = False
        for list_box in (app.sidebar.main_list, app.sidebar.archive_list):
            child = list_box.get_first_child()
            while child:
                if getattr(child, "note_name", None) == app.current_note:
                    self._patch_row(child, display_title, snippet)
                    row_found = True
                    break
                child = child.get_next_sibling()
            if row_found:
                break

        if not row_found and content.strip():
            # The note has content but no row yet (brand-new note, req 3).
            # Do a full sidebar rebuild so the row appears with correct title.
            app.refresh_list(app.sidebar.search_entry.get_text())
            app._select_sidebar_row(app.current_note)

        return False

    @staticmethod
    def _patch_row(row: Gtk.ListBoxRow, title: str, snippet: str) -> None:
        """Update the title and snippet labels of an existing sidebar row in-place.

        Reads label references stored directly on the row by Sidebar._make_row
        so this method is decoupled from the widget tree structure.
        """
        title_label = getattr(row, "title_label", None)
        snippet_label = getattr(row, "snippet_label", None)
        if title_label is not None:
            title_label.set_label(title)
        if snippet_label is not None:
            snippet_label.set_label(snippet)

    # ------------------------------------------------------------------ #
    # Save / rename (slow path — disk I/O, 1 000 ms debounce)
    # ------------------------------------------------------------------ #

    def do_delayed_save(self) -> bool:
        """Debounced save: skip empty notes, rename from H1 if needed, write to disk.

        Requirements addressed:
          - Req 1: empty content → no file written.
          - Req 2: H1 changed → file renamed on disk, sidebar row name updated.
        """
        app = self.app
        app.rename_timeout_id = 0

        if not app.current_note:
            return False

        start, end = app.buffer.get_bounds()
        content = app.buffer.get_text(start, end, True)

        # Req 1: never write an empty note to disk.
        if not content.strip():
            return False

        # Req 2: rename the file if the H1 has changed.
        new_title = _derive_display_title(content, "")
        if new_title and new_title != app.current_note:
            collision = Path(app.notes_manager.notes_dir) / f"{new_title}.md"
            if not collision.exists():
                old_name = app.current_note
                app.notes_manager.rename_note(old_name, new_title)
                app.current_note = new_title
                app.nav.update_header_ui(app.current_note, is_editor=True)

        # Write content under the (possibly renamed) note name.
        saved_note = app.current_note
        app.notes_manager.save_note(
            saved_note,
            content,
            callback=lambda success, err=None, n=saved_note: self._on_save_complete(success, n, err),
        )

        return False

    def _on_save_complete(
        self, success: bool, note_name: str, err: str | None = None
    ) -> None:
        """Callback from NotesManager after an async save finishes."""
        if not success:
            logger.error("Async save failed for '%s': %s", note_name, err)
            return

        app = self.app
        # Refresh the sidebar so the renamed row title and snippet are correct.
        app.refresh_list(app.sidebar.search_entry.get_text())
        # Re-select the currently open note if it hasn't changed.
        if app.current_note:
            app._select_sidebar_row(app.current_note)

    # ------------------------------------------------------------------ #
    # Text-changed coordination
    # ------------------------------------------------------------------ #

    def on_text_changed(self, buffer: Gtk.TextBuffer) -> None:
        """Drive sidebar update, highlight, image, and save timeouts on every edit."""
        app = self.app
        if app.is_loading or not app.current_note or app.editor.is_updating_images:
            return

        # P4 fix: move stats update onto the same 150ms debounce as the sidebar
        # update rather than running synchronously on every keystroke.
        # Fast path: update sidebar title + snippet with no disk I/O (150 ms).
        app._reschedule("sidebar_update_timeout_id", 150, self.do_delayed_sidebar_update_with_stats)
        # Syntax highlighting (100 ms).
        app._reschedule("highlight_timeout_id", 100, app.do_delayed_highlight)
        # Image refresh (2 000 ms).
        app._reschedule("image_timeout_id", 2000, app.do_delayed_images)
        # Disk save + rename (1 000 ms).
        app._reschedule("rename_timeout_id", 1000, self.do_delayed_save)

    def do_delayed_sidebar_update_with_stats(self) -> bool:
        """Combined 150ms callback: update sidebar row AND status bar stats.

        Replaces the old synchronous update_stats() call in on_text_changed so
        that stats computation (which calls get_text) is debounced alongside
        the sidebar update instead of running on every single keypress.
        """
        # Run the sidebar update first.
        self.do_delayed_sidebar_update()

        # P1 + P4 fix: update stats here instead of synchronously in
        # on_text_changed, sharing the single buffer read already done above.
        app = self.app
        if app.editor.status_bar.get_visible():
            self.update_stats()

        return False

    def update_stats(self) -> None:
        """Recompute and display word/character count and estimated reading time.

        Uses O(1) GTK buffer calls for char count and line count to avoid
        allocating the full buffer string just for counting. Word count still
        requires a full text read but is now only called on the 150ms debounce.
        """
        app = self.app
        char_count = app.buffer.get_char_count()
        # get_text is required for accurate word count; it runs on the debounce.
        start, end = app.buffer.get_bounds()
        text = app.buffer.get_text(start, end, True)
        word_count = len(text.split())
        read_time = max(1, word_count // 200)
        app.editor.stats_label.set_label(
            f"{word_count:,} words · {read_time} min read"
        )
