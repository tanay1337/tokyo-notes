"""Note lifecycle manager — owns open, create, delete, save, and rename logic."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core.services import (
    build_stats,
    clean_title,
    derive_display_title,
    patch_sidebar_row,
    update_note_title,
)
from core.translations import tr
from core.utils import (
    confirm_destructive_dialog,
    get_snippet,
    is_entry_focused,
    split_note_path,
    strip_anchors_for_save,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from main import TokyoNotes


class NoteLifecycleManager:
    """Coordinates note open/create/delete/save/rename
    between the UI and NotesManager."""

    def __init__(self, app: TokyoNotes) -> None:
        self.app = app

    # Startup

    def initial_load(self) -> bool:
        app = self.app
        notes = app.notes_manager.get_notes(sort_by_mtime=True)
        if notes:
            most_recent = notes[0]
            # Expand the folder tree containing the most recent note so
            # _select_sidebar_row can find its row in the listbox.
            folder, _ = split_note_path(most_recent)
            if folder:
                parts = folder.split("/")
                for i in range(len(parts)):
                    ancestor = "/".join(parts[: i + 1])
                    app.sidebar._folder_expanded[ancestor] = True
        app.refresh_list()
        notes = app.notes_manager.get_notes(sort_by_mtime=True)
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
        if not is_entry_focused(self.app.win.get_focus()):
            self.app.text_view.grab_focus()
        return True

    def on_new_note(self, btn: Gtk.Button | None) -> None:
        app = self.app
        app._flush_pending_save()
        name = app.notes_manager.reserve_name()
        app.current_note = name
        app.nav.update_header_ui(name, is_editor=True)
        app._has_images = False
        app._set_buffer_text("")
        app.editor.set_editable(True)
        app.content_stack.set_visible_child_name("editor")
        app.refresh_list()
        app._select_sidebar_row(name)
        if not is_entry_focused(app.win.get_focus()):
            app.text_view.grab_focus()

    # Open / select

    def on_note_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        app = self.app
        if not row or app.is_loading:
            return
        note_name = getattr(row, "note_name", None)
        if not note_name:
            return

        app._save_current_cursor()

        # In split mode, load into the focused pane
        if app.split_editor is not None:
            app._flush_pending_save()
            app.split_editor.load_note_into_focused_pane(note_name)
            return

        if note_name == app.current_note:
            if app.notes_manager.is_encrypted(note_name) and app._is_session_locked:
                app._show_unlock_popover()
            return

        if app.notes_manager.is_encrypted(note_name) and app._is_session_locked:
            app._flush_pending_save()
            app._set_buffer_text("")
            app.editor.set_editable(False)
            app.current_note = note_name
            app._select_sidebar_row(note_name)
            app._show_unlock_popover()
            return

        app._flush_pending_save()
        app.is_loading = True
        app.editor.set_editable(True)
        try:
            app.current_note = note_name
            app.nav.update_header_ui(app.current_note, is_editor=True)
            app.sidebar.set_active_view("editor")

            if (
                app.notes_manager.is_encrypted(app.current_note)
                and app._session_password_bytes is not None
            ):
                try:
                    app.content_stack.set_visible_child_name("editor")
                    app._load_encrypted_note(app.current_note)
                    content = app.buffer.get_text(*app.buffer.get_bounds(), True)
                    app.last_cursor_line = -1
                    if (
                        hasattr(app, "_toolbar_tag_buttons")
                        and app._toolbar_tag_buttons
                    ):
                        for btn in app._toolbar_tag_buttons.values():
                            btn.remove_css_class("active")
                    app._toolbar_active_tags = set()
                    app._update_backlinks()
                    if listbox == app.sidebar.main_list:
                        app.sidebar.archive_list.unselect_all()
                    else:
                        app.sidebar.main_list.unselect_all()
                    return
                except Exception as e:
                    logger.error(
                        "Failed to decrypt note '%s' on selection: %s",
                        app.current_note,
                        e,
                    )
                    content = ""
            else:
                content = app.notes_manager.read_plain(app.current_note)

            app._has_images = "![" in content

            app.editor.close_pickers()
            app.editor._last_image_text_hash = ""
            app.editor.clear_images()
            app._set_buffer_text(content)
            app.content_stack.set_visible_child_name("editor")

            # Start warming the pixbuf cache in the background so that the
            # first update_images() render (fired after highlighting completes)
            # finds already-decoded pixbufs and only has to attach widgets.
            if app._has_images:
                notes_dir = Path(app.notes_manager.notes_dir).resolve()
                app.editor._warm_image_cache(notes_dir)

            if not app._sidebar_search_text:
                app._restore_cursor_for_note(app.current_note)

            if app.highlighter:
                app.highlighter.highlight(start_line=0, end_line=30)

            app._full_pass_complete = False
            app._safe_source_remove("_pending_highlight_id")
            if app.highlighter:
                app._pending_highlight_id = GLib.idle_add(
                    self._highlight_chunk,
                    app.current_note,
                    0,
                )

            app.last_cursor_line = -1
            if hasattr(app, "_toolbar_tag_buttons") and app._toolbar_tag_buttons:
                for btn in app._toolbar_tag_buttons.values():
                    btn.remove_css_class("active")
            app._toolbar_active_tags = set()
            app._update_backlinks()

            if listbox == app.sidebar.main_list:
                app.sidebar.archive_list.unselect_all()
            else:
                app.sidebar.main_list.unselect_all()
        finally:
            app.is_loading = False
        app._update_toolbar_active_state(app.buffer)

        # Schedule initial image render if the loaded note contains images.
        if app._has_images:
            app._reschedule("image_timeout_id", 200, app.do_delayed_images)

    def _highlight_chunk(self, expected_note: str, start_line: int) -> bool:
        app = self.app
        if not app.highlighter or app.current_note != expected_note:
            app._pending_highlight_id = 0
            return False
        total = app.buffer.get_line_count()
        end_line = min(start_line + 50, total)
        app.buffer.handler_block(app.changed_handler_id)
        try:
            app.highlighter.highlight(start_line=start_line, end_line=end_line)
        finally:
            app.buffer.handler_unblock(app.changed_handler_id)
        if end_line < total:
            app._pending_highlight_id = GLib.idle_add(
                self._highlight_chunk, expected_note, end_line
            )
        else:
            app._full_pass_complete = True
            app._pending_highlight_id = 0
            if app._sidebar_search_text:
                app._apply_search_highlights()
            # Schedule progressive spell check now that the highlight is done.
            # This runs 50 lines per idle tick so the note is immediately usable.
            if app.highlighter and app.highlighter.spell_check_enabled:
                GLib.idle_add(self._spell_check_chunk, expected_note, 0)
        return False

    def _spell_check_chunk(self, expected_note: str, start_line: int) -> bool:
        """Spell-check 50 lines per idle tick, chaining until the note is done.

        Returns GLib.SOURCE_REMOVE (False) always — the chain is continued by
        scheduling the next call explicitly, not by returning True.
        """
        app = self.app
        if app.current_note != expected_note:
            return False  # note switched — abort
        if not app.highlighter or not app.highlighter.spell_check_enabled:
            return False
        total = app.buffer.get_line_count()
        if start_line >= total:
            return False

        end_line = min(start_line + 50, total)
        code_block_lines = app.highlighter._code_block_line_set()
        app.highlighter._spell_check_pass(start_line, end_line, code_block_lines)

        if end_line < total:
            GLib.idle_add(self._spell_check_chunk, expected_note, end_line)
        return False

    # Navigate

    def on_link_clicked(self, note_name: str) -> None:
        app = self.app
        if app._select_sidebar_row(note_name):
            app.sidebar.set_active_view("editor")
            app._set_backlinks_visible(True)
        elif "/" in note_name:
            parts = note_name.split("/")
            for i in range(1, len(parts)):
                app.sidebar._folder_expanded["/".join(parts[:i])] = True
            app.refresh_list()
            if app._select_sidebar_row(note_name):
                app.sidebar.set_active_view("editor")
                app._set_backlinks_visible(True)
            else:
                app.content_stack.set_visible_child_name("editor")
        elif app.cfg.get("create_on_link_click", True):
            app.content_stack.set_visible_child_name("editor")
            try:
                app._flush_pending_save()
                name = app.notes_manager.reserve_name(note_name)
                app.current_note = name
                app.nav.update_header_ui(name, is_editor=True)
                app._has_images = False
                app._set_buffer_text("")
                app.editor.set_editable(True)
                app.refresh_list()
                app._select_sidebar_row(name)
            except ValueError:
                logger.exception("Failed to create note from link")

    def handle_row_click(
        self, gesture: Any, n_press: int, x: float, y: float, cb: dict
    ) -> None:
        app = self.app
        app.content_stack.set_visible_child_name("editor")
        note_name = cb["note"]
        if not app._select_sidebar_row(note_name):
            if "/" in note_name:
                parts = note_name.split("/")
                for i in range(1, len(parts)):
                    app.sidebar._folder_expanded["/".join(parts[:i])] = True
                app.refresh_list()
                if not app._select_sidebar_row(note_name):
                    return
            else:
                return
        # Flush pending editor save so the disk and buffer match before
        # we re-read the note to resolve the checkbox's current line.
        if app.current_note == note_name:
            app._flush_pending_save()
        content = app.notes_manager.read_plain(note_name)
        current_line = app.notes_manager._resolve_in_content(
            content, cb["line"], cb.get("text", "")
        )
        GLib.idle_add(self.scroll_to_line, current_line)

    def scroll_to_line(self, line_num: int) -> bool:
        app = self.app
        try:
            result = app.buffer.get_iter_at_line(line_num - 1)
            it = result[1] if isinstance(result, tuple) else result
        except (TypeError, IndexError):
            return False
        mark = app.buffer.create_mark(None, it, True)
        app.text_view.scroll_to_mark(mark, 0.0, True, 0.5, 0.1)
        app.buffer.delete_mark(mark)
        return False

    # Delete

    def on_delete_shortcut(self) -> bool:
        app = self.app
        if app.text_view.has_focus():
            return False
        main_row = app.sidebar.main_list.get_selected_row()
        arch_row = app.sidebar.archive_list.get_selected_row()
        row = main_row if (main_row and hasattr(main_row, "note_name")) else arch_row
        if row and hasattr(row, "note_name"):
            self.on_delete_action(None, GLib.Variant("s", row.note_name))
        return True

    def on_delete_action(self, action: Any, parameter: GLib.Variant) -> None:
        app = self.app
        note_name = parameter.get_string()
        content = app.notes_manager.read_plain(note_name).strip()
        if not content:
            self.confirm_delete(note_name)
        else:
            dialog = confirm_destructive_dialog(
                transient_for=app.win,
                heading=tr("Delete Note?"),
                body=tr(
                    "Are you sure you want to delete '{note_name}'?"
                    " This action cannot be undone."
                ).format(note_name=note_name),
            )
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
                app._safe_source_remove(attr)
        is_enc = app.notes_manager.is_encrypted(note_name)
        app.notes_manager.delete_note(note_name)
        app.cfg.remove_note(note_name)

        if app.cfg.get("git_enabled", False):

            def _do_git_del():
                app.git_controller.commit_deletion(note_name, enc=is_enc)

            app._run_on_io_thread(_do_git_del)
        app.sidebar.maybe_exit_archive_view()
        was_current = app.current_note == note_name
        if was_current:
            app.current_note = None
            app._set_buffer_text("")
            app.win.set_title(tr("Tokyo Notes"))
        app.refresh_list(app.sidebar.search_entry.get_text())
        if was_current:
            remaining = app.notes_manager.get_notes()
            if remaining:
                self.initial_load()
            else:
                self.on_new_note(None)

    # Live sidebar update (150 ms debounce)

    def _update_sidebar_and_stats(self) -> bool:
        """Debounce callback: update sidebar row and status bar in-place."""
        app = self.app
        app.sidebar_update_timeout_id = 0
        current = app.current_note
        if not current:
            return False

        if app._buffer_mod_counter == app._last_sidebar_update_counter:
            return False

        app._last_sidebar_update_counter = app._buffer_mod_counter
        start, end = app.buffer.get_bounds()
        content = app.buffer.get_text(start, end, True)

        if not app._has_images and "![" in content:
            app._has_images = True

        lines = content.split("\n")
        head = "\n".join(lines[:10])
        display_title = derive_display_title(head, current)
        snippet = get_snippet(head)

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
            if current in app.notes_manager.get_notes():
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

        content = strip_anchors_for_save(app.buffer)

        if not content.strip():
            return False

        if app.current_note.startswith(".template:"):
            tmpl_slug = app.current_note.split(":", 1)[1]

            current_path = app.template_manager.templates_dir / f"{tmpl_slug}.md"
            if (
                current_path.exists()
                and current_path.read_text(encoding="utf-8") == content
            ):
                return False

            if app.template_manager.is_builtin(tmpl_slug):
                copy_slug = app.template_manager.reserve_copy_slug(tmpl_slug)
                (app.template_manager.templates_dir / f"{copy_slug}.md").write_text(
                    content, encoding="utf-8"
                )
                app.current_note = f".template:{copy_slug}"
                app.nav.update_header_ui(
                    tr("Template: {slug}").format(slug=copy_slug), is_editor=True
                )
                tmpl_slug = copy_slug

            new_title = derive_display_title(content, "")
            if new_title and new_title != tmpl_slug:
                new_slug = clean_title(new_title).lower().replace(" ", "-")
                new_slug = "".join(c for c in new_slug if c.isalnum() or c in "-_")
                if new_slug and new_slug != tmpl_slug:
                    templates_dir = app.template_manager.templates_dir
                    old_path = templates_dir / f"{tmpl_slug}.md"
                    new_path = templates_dir / f"{new_slug}.md"
                    counter = 1
                    base_slug = new_slug
                    while new_path.exists():
                        new_slug = f"{base_slug}-{counter}"
                        new_path = templates_dir / f"{new_slug}.md"
                        counter += 1
                    if old_path.exists():
                        old_path.rename(new_path)
                    new_path.write_text(content, encoding="utf-8")
                    app.current_note = f".template:{new_slug}"
                    app.nav.update_header_ui(
                        tr("Template: {slug}").format(slug=new_slug), is_editor=True
                    )
                else:
                    app.template_manager.update_template(tmpl_slug, content)
            else:
                app.template_manager.update_template(tmpl_slug, content)
            return False

        old_name = app.current_note

        from core.services import save_note_content

        save_note_content(
            note_name=app.current_note,
            content=content,
            is_encrypted=app.notes_manager.is_encrypted(app.current_note),
            derive_encryption_key=app._derive_encryption_key,
            notes_manager=app.notes_manager,
            session_password_bytes=app._session_password_bytes,
            on_done=lambda: self._finish_save(old_name, content),
        )

        return False

    def _finish_save(self, old_name: str, content: str) -> None:
        """Post-save callback on the main thread after async I/O."""
        app = self.app
        new_name, did_rename = update_note_title(
            old_name=old_name,
            content=content,
            notes_manager=app.notes_manager,
        )

        if did_rename:
            app.current_note = new_name
            app.nav.update_header_ui(new_name, is_editor=True)
            if app.notes_manager.is_encrypted(new_name):
                app.cfg.encrypted.discard(old_name)
                app.cfg.encrypted.add(new_name)
                app.cfg.sync_encrypted_set(app.cfg.encrypted)
            # Structure changed (name changed) — full rebuild needed so the
            # row appears under the correct sorted position and filename.
            app.refresh_list(app.sidebar.search_entry.get_text())
            app._select_sidebar_row(app.current_note)
        else:
            # Title or snippet may have changed but the note was not renamed,
            # so its position in the list and its folder are unchanged.
            # Update the existing row in-place instead of rebuilding every row.
            start, end = app.buffer.get_bounds()
            _head_content = app.buffer.get_text(start, end, True)
            _head = "\n".join(_head_content.split("\n")[:10])
            app.sidebar.update_row(
                new_name,
                derive_display_title(_head, new_name),
                get_snippet(_head),
            )
            if not app.sidebar.search_entry.has_focus():
                app._select_sidebar_row(app.current_note)

        self._maybe_git_commit(new_name, old_name if did_rename else None)

    def _maybe_git_commit(self, note_name: str, old_name: str | None = None) -> None:
        """Auto-commit to git if versioning is enabled."""
        app = self.app
        if not app.cfg.get("git_enabled") or not app.cfg.get("git_auto_commit"):
            return
        gc = app.git_controller
        if not gc.is_available():
            return

        def _do_git():
            if old_name and old_name != note_name:
                gc.rename_note(old_name, note_name)
            gc.auto_commit(note_name)

        app._run_on_io_thread(_do_git)

    def do_delayed_spell_check(self) -> bool:
        """Re-check spelling for lines around the cursor (500 ms debounce)."""
        app = self.app
        app.spell_check_timeout_id = 0
        if not app.highlighter or not app.highlighter.spell_check_enabled:
            return False
        cursor_iter = app.buffer.get_iter_at_mark(app.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        total = app.buffer.get_line_count()
        start = max(0, cursor_line - 5)
        end = min(total, cursor_line + 6)
        code_block_lines = app.highlighter._code_block_line_set()
        app.highlighter._spell_check_pass(start, end, code_block_lines)
        return False

    # Text-changed coordination

    def on_text_changed(self, buffer: Gtk.TextBuffer) -> None:
        app = self.app
        if app.is_loading or not app.current_note:
            return
        app._buffer_mod_counter += 1
        app._reset_lock_timer_on_activity()
        app._reschedule(
            "sidebar_update_timeout_id", 150, self._update_sidebar_and_stats
        )
        app._reschedule("highlight_timeout_id", 100, app.do_delayed_highlight)
        app._reschedule("spell_check_timeout_id", 500, self.do_delayed_spell_check)
        if not app.editor._image_update_running:
            app._reschedule("image_timeout_id", 2000, app.do_delayed_images)
        app._reschedule("rename_timeout_id", 1000, self.do_delayed_save)
