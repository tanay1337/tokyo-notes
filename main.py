"""Tokyo Notes — main application entry point."""

import logging
import os
import sys
from pathlib import Path
from threading import Thread
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from core.actions import ActionsHandler
from core.config import ConfigManager
from core.crash_handler import install as install_crash_handler
from core.diagram import Diagram
from core.diagram_manager import DiagramManager
from core.highlighter import MarkdownHighlighter
from core.instance_lock import InstanceLock
from core.navigation import NavigationController
from core.note_lifecycle import NoteLifecycleManager
from core.search import SearchController
from core.shortcuts import setup_shortcuts
from core.speech import model_cached
from core.spell_checker import SpellChecker
from core.startup_checks import validate_notes_folder
from core.storage import NotesManager
from core.template_manager import TemplateManager
from core.theme_manager import ThemeManager
from core.translations import load as load_i18n
from core.translations import tr
from core.utils import (
    CB_ANY_RE,
    IS_MAC,
    confirm_destructive_dialog,
    is_entry_focused,
    set_response_suggested,
    strip_anchors_for_save,
)
from core.window_manager import WindowManager
from ui.click_dispatcher import ClickDispatcher
from ui.deadline_picker import DeadlinePicker
from ui.diagram_view import DiagramView
from ui.editor import Editor
from ui.sakura_overlay import SakuraOverlay
from ui.sidebar import Sidebar
from ui.toolbar import build_toolbar

logger = logging.getLogger(__name__)


class TokyoNotes(Adw.Application):
    def __init__(self, **kwargs) -> None:
        super().__init__(application_id="app.tokyo-notes.TokyoNotes", **kwargs)
        self.base_dir = Path(__file__).parent

        # Services (explicit, testable)
        self.cfg = ConfigManager()
        self.notes_folder: str = self.cfg.get("notes_folder")
        self.notes_manager = NotesManager(notes_dir=self.notes_folder)

        # Git versioning
        from core.versioning import GitVersionController

        self.git_controller = GitVersionController(
            notes_dir=self.notes_folder,
            executor=self._run_on_io_thread,
        )

        # Subsystem managers — order matters for startup
        self.window_manager = WindowManager(self)
        self.theme_manager = ThemeManager(self)
        self.click_dispatcher = ClickDispatcher(self)
        self.actions = ActionsHandler(self)
        self.nav = NavigationController(self)
        self.lifecycle = NoteLifecycleManager(self)
        self.search = SearchController(self.refresh_list)
        self.template_manager = TemplateManager(self)
        self.diagram_manager = DiagramManager(notes_dir=self.notes_folder)

        # Runtime state — all timeout IDs kept together for easy auditing
        self.current_note: str | None = None
        self.current_open_diagram: Diagram | None = None
        self.is_loading: bool = False
        self.highlighter: MarkdownHighlighter | None = None
        self.spell_checker: SpellChecker | None = None
        self.highlight_timeout_id: int = 0
        self.rename_timeout_id: int = 0
        self.sidebar_update_timeout_id: int = 0
        self.image_timeout_id: int = 0
        self.spell_check_timeout_id: int = 0
        self.search_timeout_id: int = 0
        self.changed_handler_id: int = 0
        self.last_cursor_line: int = -1
        self._pending_highlight_id: int = 0
        self._has_selection: bool = False
        self._has_images: bool = False
        self._buffer_mod_counter: int = 0
        self._last_sidebar_update_counter: int = -1
        self._full_pass_complete: bool = False
        self._cursor_positions: dict[str, int] = {}
        self.split_view: Adw.OverlaySplitView | None = None  # set in do_activate

        # Session state for private notes
        self._session_password_bytes: bytearray | None = None
        self._encryption_key_cache: dict[str, bytearray] = {}
        self._is_session_locked: bool = any(
            self.notes_manager.is_encrypted(n) for n in self.notes_manager.get_notes()
        )
        self._lock_timer_id: int = 0
        self._wrong_unlock_attempts: int = 0
        self._pending_encrypt_note: str | None = None
        self._pending_encrypt_folder: tuple[str, list[str]] | None = None
        self._pending_auto_encrypt_in_folder: str | None = None
        self._unlock_cooldown_id: int = 0
        self._unlock_cooldown_remaining: int = 0

        # Sync encrypted.json with actual .md.enc files on disk
        self._sync_encrypted_config()

        install_crash_handler(self)
        self._setup_actions()

    # App lifecycle

    def do_shutdown(self) -> None:
        """Flush any pending config writes and note saves before the process exits."""
        from core.encryption import shutdown_pool

        self._cancel_lock_timer()
        self._flush_pending_save()
        if (
            self.current_note
            and not self.current_note.startswith(".template:")
            and self.notes_manager.is_encrypted(self.current_note)
            and hasattr(self, "buffer")
        ):
            self.buffer.set_text("")
        self._zero_session_password()
        self.cfg.flush_immediate()
        shutdown_pool()
        logger.info("Tokyo Notes shutting down")
        Adw.Application.do_shutdown(self)

    # Git versioning

    def _init_git_repo(self) -> None:
        """Prompt user to initialize git repo and set up versioning."""
        from core.versioning import GitVersionController

        if not GitVersionController.is_git_installed():
            logger.info("git not found on system — versioning disabled")
            return

        if self.git_controller.is_repo():
            logger.info("Git repo already exists at %s", self.notes_folder)
            return

        if self.cfg.get("git_init_dismissed"):
            logger.debug("Git init previously dismissed — skipping")
            return

        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading=tr("Enable git versioning for your notes?"),
            body=tr(
                "This creates a local git repository in your notes folder"
                " to track changes over time.\n\n"
                "You will be able to browse history, view diffs,"
                " and restore previous versions of any note."
            ),
        )
        dialog.add_response("dismiss", tr("Not Now"))
        dialog.add_response("hide", tr("Don't show again"))
        dialog.add_response("enable", tr("Enable"))
        dialog.set_default_response("enable")
        dialog.set_close_response("dismiss")
        from core.utils import set_response_suggested

        set_response_suggested(dialog, "enable")

        def on_response(d: Adw.MessageDialog, response: str) -> None:
            if response == "enable":
                ok = self.git_controller.init_repo()
                if ok:
                    self.cfg.set("git_enabled", True)
                    self._show_toast(tr("Git versioning enabled"))
                    self._update_toolbar_versioning_buttons()
                else:
                    self._show_toast(tr("Failed to initialize git repository"))
            elif response == "hide":
                self.cfg.set("git_init_dismissed", True)

        dialog.connect("response", on_response)
        dialog.present()

    def _run_on_io_thread(self, fn):
        """Dispatch a callable to the thread pool executor."""
        self.notes_manager._io_executor.submit(fn)

    def _on_snapshot_clicked(self) -> None:
        """Create a manual snapshot commit."""
        if not self.git_controller.is_available():
            self._show_toast(tr("Git versioning is not enabled"))
            return

        def _do_snapshot():
            ok = self.git_controller.snapshot()
            GLib.idle_add(
                lambda: self._show_toast(
                    tr("Snapshot created") if ok else tr("No changes to snapshot")
                )
            )

        self._run_on_io_thread(_do_snapshot)

    def _on_show_history(self, note_name: str | None = None) -> None:
        """Show version history for a note."""
        name = note_name or self.current_note
        if not name or not self.git_controller.is_available():
            return
        from ui.history_popover import HistoryPopover

        popover = HistoryPopover(
            note_name=name,
            git_controller=self.git_controller,
            on_restore=self._on_restore_version,
            on_snapshot=self._on_snapshot_clicked,
            text_view=self.text_view,
            executor=self._run_on_io_thread,
        )
        parent = (
            self.history_btn
            if getattr(self, "history_btn", None) is not None
            else self.text_view
        )
        popover.set_parent(parent)
        popover.popup()

    def _on_restore_version(self, note_name: str, content: str | bytes) -> None:
        """Restore a note to a previous version."""
        self._flush_pending_save()
        if isinstance(content, bytes):
            self.notes_manager.save_encrypted(note_name, content)
        else:
            self.notes_manager.save_note(note_name, content)
        if note_name == self.current_note:
            if isinstance(content, bytes):
                self._load_encrypted_note(note_name)
            else:
                self._set_buffer_text(content)
                if self.highlighter:
                    self.highlighter.highlight()
        self._show_toast(
            tr("'{note_name}' restored to previous version").format(note_name=note_name)
        )

    def _update_toolbar_versioning_buttons(self) -> None:
        """Show/hide the versioning toolbar button based on git availability."""
        has_git = self.git_controller.is_available()
        if getattr(self, "history_btn", None) is not None:
            self.history_btn.set_visible(has_git)

    def _apply_security_mitigations(self) -> None:
        """Prevent core dumps."""
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except Exception as e:
            logger.warning("Could not disable core dumps: %s", e)

    # Session management for private notes

    def unlock_session(self, password: str) -> None:
        """Unlock private notes with the given password.

        Verification happens by deriving a key using the per-file salt from
        the first encrypted note and attempting to decrypt it.
        The password is stored in memory so keys can be derived per-note.
        """
        from core.encryption import _SALT_LEN, derive_key_async

        encrypted_notes = [
            n
            for n in self.notes_manager.get_notes()
            if self.notes_manager.is_encrypted(n)
        ]
        if not encrypted_notes:
            self._show_toast(tr("No private notes to unlock"))
            return

        first_note = encrypted_notes[0]
        try:
            ciphertext_bytes = self.notes_manager.read_encrypted_raw(first_note)
        except FileNotFoundError:
            logger.warning("Encrypted note '%s' not found on disk", first_note)
            self._show_toast(tr("Corrupted encrypted note — cannot unlock"))
            return

        password_bytes = bytearray(password.encode("utf-8"))

        def _on_key_derived(key: bytes) -> None:
            """Called from the GTK main thread (marshalled by derive_key_async)."""
            self._finish_unlock(password_bytes, key, ciphertext_bytes, first_note)

        derive_key_async(password_bytes, ciphertext_bytes[:_SALT_LEN], _on_key_derived)

    def _pre_derive_remaining_keys(
        self, password: bytearray, skip: tuple[str, ...]
    ) -> None:
        """Pre-derive keys for all encrypted notes (except *skip*) in the background."""
        from core.encryption import _SALT_LEN, derive_key_async

        for note_name in self.notes_manager.get_notes():
            if not self.notes_manager.is_encrypted(note_name) or note_name in skip:
                continue
            try:
                ciphertext_bytes = self.notes_manager.read_encrypted_raw(note_name)
            except Exception:
                logger.warning(
                    "Could not read encrypted note '%s' for key pre-derivation",
                    note_name,
                )
                continue
            salt = ciphertext_bytes[:_SALT_LEN]

            def _cache_key(n: str, ct: bytes) -> Callable[[bytes], None]:
                def _on_done(key: bytes) -> None:
                    self._set_cached_key(n, key, ct)

                return _on_done

            derive_key_async(password, salt, _cache_key(note_name, ciphertext_bytes))

    def _set_cached_key(
        self, note_name: str, key: bytes, ciphertext_bytes: bytes
    ) -> None:
        """Store a pre-derived key in the cache
        (called from main thread via idle_add)."""
        if self._is_session_locked:
            return
        if note_name in self._encryption_key_cache:
            return
        self._encryption_key_cache[note_name] = bytearray(key)

    def _finish_unlock(
        self, password: bytearray, key: bytes, ciphertext_bytes: bytes, first_note: str
    ) -> None:
        """Complete unlock on the main thread after async key derivation."""
        from core.encryption import decrypt

        try:
            decrypt(ciphertext_bytes, bytearray(key))
        except Exception as e:
            logger.warning("Wrong password: %s", e)
            self._wrong_unlock_attempts += 1
            if self._wrong_unlock_attempts >= 3:
                self._start_unlock_cooldown()

            if hasattr(self, "_unlock_dialog") and self._unlock_dialog is not None:
                self._unlock_dialog.on_verification_failed(tr("Wrong password"))
            else:
                self._show_toast(tr("Wrong password"))
            return

        self._session_password_bytes = password
        self._is_session_locked = False
        self._wrong_unlock_attempts = 0
        self._cancel_unlock_cooldown()
        self._update_sidebar_lock_state()
        self._reset_lock_timer()
        self._show_toast(tr("Private notes unlocked"))
        self.editor.set_editable(True)

        if hasattr(self, "_unlock_dialog") and self._unlock_dialog is not None:
            self._unlock_dialog.close()

        # Cache the key for the note used to verify and pre-derive all others
        self._encryption_key_cache[first_note] = bytearray(key)
        self._pre_derive_remaining_keys(password, (first_note,))

        if self.current_note and self.notes_manager.is_encrypted(self.current_note):
            self.nav.update_header_ui(self.current_note, is_editor=True)
            self.sidebar.set_active_view("editor")
            self.content_stack.set_visible_child_name("editor")
            try:
                self._load_encrypted_note(self.current_note)
            except Exception as e:
                logger.error("Failed to load encrypted note after unlock: %s", e)
        if self._pending_encrypt_note:
            note_name = self._pending_encrypt_note
            self._pending_encrypt_note = None
            try:
                self._encrypt_note(note_name)
            except Exception as e:
                logger.error(
                    "Failed to encrypt pending note '%s' after unlock: %s", note_name, e
                )
        if self._pending_encrypt_folder:
            folder, plain_notes = self._pending_encrypt_folder
            self._pending_encrypt_folder = None
            try:
                from core.services import encrypt_note_on_disk

                for note in plain_notes:
                    content, key_bytes = encrypt_note_on_disk(
                        note_name=note,
                        password=self._session_password_bytes,
                        notes_manager=self.notes_manager,
                        cfg=self.cfg,
                    )
                    self._encryption_key_cache[note] = key_bytes
                    self.sidebar.set_row_encrypted(note, True)
                self.refresh_list(self.sidebar.search_entry.get_text())
                self._show_toast(
                    tr("Encrypted {n} note(s) in '{folder}'").format(
                        n=len(plain_notes), folder=folder
                    )
                )
            except Exception as e:
                logger.error("Failed to encrypt pending folder after unlock: %s", e)
        if self._pending_auto_encrypt_in_folder:
            note_name = self._pending_auto_encrypt_in_folder
            self._pending_auto_encrypt_in_folder = None
            try:
                import os

                from core.encryption import _SALT_LEN, derive_key, encrypt

                pw = bytearray(self._session_password_bytes)
                salt = os.urandom(_SALT_LEN)
                key = derive_key(pw, salt)
                key_bytes = bytearray(key)
                ciphertext = encrypt("", key_bytes, salt)
                self.notes_manager.save_encrypted(note_name, ciphertext)
                self.cfg.mark_encrypted(note_name)
                self._encryption_key_cache[note_name] = key_bytes
                self.sidebar.set_row_encrypted(note_name, True)
                self._show_toast(tr("New note encrypted"))
            except Exception as e:
                logger.error("Failed to auto-encrypt new note after unlock: %s", e)

    def _show_unlock_popover(self) -> None:
        """Show the unlock dialog."""
        if hasattr(self, "_unlock_dialog") and self._unlock_dialog is not None:
            self._unlock_dialog.present()
            return
        from ui.unlock_popover import UnlockDialog

        dialog = UnlockDialog(self)

        def _on_dismissed(_d, response):
            self._unlock_dialog = None
            if (
                response != "unlock"
                and self._pending_auto_encrypt_in_folder
                and self._is_session_locked
            ):
                self._pending_auto_encrypt_in_folder = None
                self._show_toast(tr("Note was not encrypted. Encrypt it manually."))

        dialog.connect("response", _on_dismissed)
        self._unlock_dialog = dialog
        dialog.present()

    def lock_session(self) -> None:
        """Lock private notes, zero the key and password, clear the buffer."""
        self._cancel_lock_timer()
        self._save_current_cursor()
        if self.current_note and self.notes_manager.is_encrypted(self.current_note):
            try:
                self._save_current_encrypted_note()
            except Exception as e:
                logger.error("Failed to save encrypted note on lock: %s", e)
            if hasattr(self, "buffer"):
                self.buffer.set_text("")
        self.editor.set_editable(False)
        self._zero_session_password()
        for v in self._encryption_key_cache.values():
            v[:] = b"\x00" * len(v)
        self._encryption_key_cache.clear()
        self._is_session_locked = True
        self._update_sidebar_lock_state()
        # Re-highlight plain note buffer — GTK may drop tag visuals after
        # set_editable(False) and sidebar layout changes.
        if self.current_note and not self.notes_manager.is_encrypted(self.current_note):
            if self.highlighter:
                self.highlighter.highlight()
        self._show_toast(
            tr("Private notes locked"),
            action_label=tr("Unlock"),
            action=self._show_unlock_popover,
        )

    def _zero_session_password(self) -> None:
        """Zero out the session password bytearray before releasing it."""
        from core.encryption import zero_bytearray

        zero_bytearray(self._session_password_bytes)
        self._session_password_bytes = None

    def _cancel_lock_timer(self) -> None:
        if self._lock_timer_id:
            tid = self._lock_timer_id
            self._lock_timer_id = 0
            GLib.source_remove(tid)

    def _reset_lock_timer(self) -> None:
        """Reset the inactivity timer. Only active when an encrypted note is open."""
        self._cancel_lock_timer()
        if self._is_session_locked or self._session_password_bytes is None:
            return
        timeout_minutes = self.cfg.get("lock_timeout_minutes", 5)
        if timeout_minutes == 0:
            self._lock_timer_id = 0
            return
        self._lock_timer_id = GLib.timeout_add_seconds(
            timeout_minutes * 60,
            self._on_lock_timeout,
        )

    def _sync_encrypted_config(self) -> None:
        """Rebuild encrypted.json from actual .md.enc files on disk."""
        actual_encrypted = set()
        for p in self.notes_manager.notes_dir.glob("**/*.md.enc"):
            rel = p.relative_to(self.notes_manager.notes_dir)
            if any(part.startswith(".") for part in rel.parts[:-1]):
                continue
            stem = rel.stem
            if stem.endswith(".md"):
                stem = stem[:-3]
            actual_encrypted.add(str(rel.parent / stem))
        self.cfg.sync_encrypted_set(actual_encrypted)

    def _on_lock_timeout(self) -> bool:
        self._lock_timer_id = 0
        self.lock_session()
        return False

    def _start_unlock_cooldown(self) -> None:
        self._cancel_unlock_cooldown()
        self._unlock_cooldown_remaining = 5

        def _tick() -> bool:
            self._unlock_cooldown_remaining -= 1
            if self._unlock_cooldown_remaining <= 0:
                self._unlock_cooldown_id = 0
                self._unlock_cooldown_remaining = 0
                return False
            return True

        self._unlock_cooldown_id = GLib.timeout_add_seconds(1, _tick)

    def _cancel_unlock_cooldown(self) -> None:
        if self._unlock_cooldown_id:
            tid = self._unlock_cooldown_id
            self._unlock_cooldown_id = 0
            self._unlock_cooldown_remaining = 0
            GLib.source_remove(tid)

    def is_unlock_cooldown_active(self) -> bool:
        return self._unlock_cooldown_remaining > 0

    def get_unlock_cooldown_remaining(self) -> int:
        return self._unlock_cooldown_remaining

    def _update_sidebar_lock_state(self) -> None:
        """Update all encrypted sidebar rows to reflect the current lock state."""
        locked = self._is_session_locked
        for lb in (self.sidebar.main_list, self.sidebar.archive_list):
            child = lb.get_first_child()
            while child:
                if getattr(child, "is_encrypted", False):
                    self.sidebar.update_encrypted_row(child, locked)
                child = child.get_next_sibling()

    def _derive_encryption_key(self, note_name: str) -> tuple[bytearray, bytes, bytes]:
        """Derive encryption key for *note_name* using the stored session password.

        Returns (key_bytes, file_salt, ciphertext_bytes).
        Results are cached per note so switching back is instant.
        """
        cached = self._encryption_key_cache.get(note_name)
        if cached is not None:
            from core.encryption import _SALT_LEN

            ciphertext_bytes = self.notes_manager.read_encrypted_raw(note_name)
            file_salt = ciphertext_bytes[:_SALT_LEN]
            return cached, file_salt, ciphertext_bytes

        from core.encryption import _SALT_LEN, derive_key_from_file

        ciphertext_bytes = self.notes_manager.read_encrypted_raw(note_name)
        file_salt = ciphertext_bytes[:_SALT_LEN]
        key = derive_key_from_file(self._session_password_bytes, ciphertext_bytes)
        key_bytes = bytearray(key)
        self._encryption_key_cache[note_name] = key_bytes
        return key_bytes, file_salt, ciphertext_bytes

    def _save_current_encrypted_note(self) -> None:
        """Encrypt and save the current editor buffer content."""
        if not self.current_note or self._session_password_bytes is None:
            return
        start, end = self.buffer.get_bounds()
        plaintext = self.buffer.get_text(start, end, True)
        from core.encryption import encrypt

        key_bytes, file_salt, _ = self._derive_encryption_key(self.current_note)
        ciphertext = encrypt(plaintext, key_bytes, file_salt)
        self.notes_manager.save_encrypted(self.current_note, ciphertext)
        self.cfg.mark_encrypted(self.current_note)

    def _encrypt_note(self, note_name: str) -> None:
        """Encrypt an existing plain note using the stored session password."""
        if self._session_password_bytes is None:
            return
        from core.services import encrypt_note_on_disk

        content, key_bytes = encrypt_note_on_disk(
            note_name=note_name,
            password=self._session_password_bytes,
            notes_manager=self.notes_manager,
            cfg=self.cfg,
        )
        self._encryption_key_cache[note_name] = key_bytes
        self.sidebar.set_row_encrypted(note_name, True)
        self.current_note = note_name
        self._select_sidebar_row(note_name)
        self._set_buffer_text(content)
        if self.highlighter:
            self.highlighter.highlight()
            self._full_pass_complete = True
            self._pending_highlight_id = 0
        self._show_toast(tr("'{note_name}' is now private").format(note_name=note_name))

    def _load_encrypted_note(self, note_name: str) -> None:
        """Decrypt and load an encrypted note into the editor."""
        if self._session_password_bytes is None:
            return
        try:
            from core.encryption import decrypt

            key_bytes, _, ciphertext = self._derive_encryption_key(note_name)
            plaintext = decrypt(ciphertext, key_bytes)
            self._set_buffer_text(plaintext)

            self._restore_cursor_for_note(note_name)
            GLib.idle_add(
                lambda: (
                    self.text_view.grab_focus()
                    if not is_entry_focused(self.win.get_focus())
                    else None
                )
            )

            self._schedule_full_highlight()
        except Exception as e:
            logger.error("Failed to decrypt note '%s': %s", note_name, e)
            self._show_toast(
                tr("Failed to decrypt '{note_name}'").format(note_name=note_name)
            )
            self._set_buffer_text("")

    def _set_buffer_text(self, content: str) -> None:
        """Set editor buffer content without triggering handlers."""
        handlers_to_block = []
        if self.changed_handler_id:
            handlers_to_block.append((self.buffer, self.changed_handler_id))

        if hasattr(self, "mark_set_handler_id") and self.mark_set_handler_id:
            handlers_to_block.append((self.buffer, self.mark_set_handler_id))

        if hasattr(self, "editor") and hasattr(self.editor, "cursor_handler_id"):
            handlers_to_block.append((self.buffer, self.editor.cursor_handler_id))

        for obj, hid in handlers_to_block:
            obj.handler_block(hid)

        try:
            self._has_selection = False
            # Remove all tags to avoid Pango/btree sync issues during replacement.
            start, end = self.buffer.get_bounds()
            self.buffer.remove_all_tags(start, end)
            self.buffer.set_text(content)
        finally:
            for obj, hid in reversed(handlers_to_block):
                obj.handler_unblock(hid)

    def _save_current_cursor(self) -> None:
        if self.current_note and hasattr(self, "buffer") and not self.is_loading:
            self._cursor_positions[self.current_note] = self.buffer.get_property(
                "cursor-position"
            )

    def _restore_cursor_for_note(self, note_name: str) -> None:
        cursor_pos = self._cursor_positions.get(note_name)
        end = self.buffer.get_end_iter()
        if cursor_pos is not None and cursor_pos <= self.buffer.get_char_count():
            it = self.buffer.get_iter_at_offset(cursor_pos)
        else:
            it = end
        self.buffer.place_cursor(it)

        # Use a mark for stable scrolling across modifications (e.g. images)
        mark = self.buffer.create_mark(None, it, True)

        GLib.idle_add(self._do_scroll_to_mark, mark)

    def _do_scroll_to_mark(self, mark: Gtk.TextMark) -> bool:
        if self.buffer:
            self.text_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)
            self.buffer.delete_mark(mark)
        return False

    def _scroll_to_cursor(self) -> None:
        it = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        mark = self.buffer.create_mark(None, it, True)
        GLib.idle_add(self._do_scroll_to_mark, mark)

    def _load_encrypted_note_to_buffer(
        self, note_name: str, buffer: Gtk.TextBuffer
    ) -> None:
        """Decrypt and load an encrypted note into a specific buffer."""
        if self._session_password_bytes is None:
            return
        try:
            from core.encryption import decrypt

            key_bytes, _, ciphertext = self._derive_encryption_key(note_name)
            plaintext = decrypt(ciphertext, key_bytes)
            buffer.set_text(plaintext)
        except Exception as e:
            logger.error("Failed to decrypt note '%s': %s", note_name, e)

    def _schedule_full_highlight(self) -> None:
        """Trigger a full-parse highlight asynchronously."""
        self._full_pass_complete = False
        self._safe_source_remove("_pending_highlight_id")
        current = self.current_note
        if self.highlighter and current:
            self._pending_highlight_id = GLib.idle_add(
                self.lifecycle._highlight_chunk, current, 0
            )

    def _focus_text_view(self) -> None:
        if not is_entry_focused(self.win.get_focus()):
            self.text_view.grab_focus()

    def _show_toast(
        self, message: str, action_label: str | None = None, action=None
    ) -> None:
        """Show an Adw.Toast with optional action button."""
        toast = Adw.Toast(title=message, timeout=3)
        if action_label and action:
            toast.set_button_label(action_label)
            toast.connect("button-clicked", lambda *_: action())
        if hasattr(self, "toast_overlay"):
            self.toast_overlay.add_toast(toast)

    # Template actions

    def _on_new_from_template(self, *args) -> None:
        """Open template picker to create a new note from template."""
        self._show_template_picker_for_new_note()

    def _show_template_picker_for_new_note(self, folder: str = "") -> None:
        """Show the template picker for creating a new note.

        If *folder* is given, the note is created inside that folder.
        """
        from ui.template_picker import TemplatePicker

        def on_selected(slug: str) -> None:
            content = self.template_manager.get_template_content(slug)
            if content is None:
                return
            from core.template_manager import TemplateManager

            substituted = TemplateManager.substitute_variables(content)
            self.lifecycle.on_new_note(None)
            target_folder = folder or self.template_manager.get_template_folder(slug)
            if target_folder:
                new_name = self.notes_manager.reserve_name(f"{target_folder}/Untitled")
                self.notes_manager.rename_note(self.current_note, new_name)
                self.current_note = new_name
                self.nav.update_header_ui(new_name, is_editor=True)
            self.notes_manager.save_note(self.current_note, substituted)
            from core.services import update_note_title

            new_name, did_rename = update_note_title(
                old_name=self.current_note,
                content=substituted,
                notes_manager=self.notes_manager,
            )
            if did_rename:
                self.current_note = new_name
                self.nav.update_header_ui(new_name, is_editor=True)
            self.refresh_list()
            self._select_sidebar_row(self.current_note)
            self._set_buffer_text(substituted)
            start = self.buffer.get_start_iter()
            self.buffer.place_cursor(start)
            self.text_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)
            if self.highlighter:
                self.highlighter.highlight(start_line=0, end_line=30)
            self._focus_text_view()

        picker = TemplatePicker(
            self.template_manager.get_all_templates(),
            on_selected,
            self.text_view if hasattr(self, "text_view") else None,
        )
        if hasattr(self, "text_view"):
            strong, _weak = self.text_view.get_cursor_locations(None)
            bx, by = self.text_view.buffer_to_window_coords(
                Gtk.TextWindowType.TEXT, strong.x, strong.y
            )
            rect = Gdk.Rectangle()
            rect.x = bx
            rect.y = by
            rect.width = 1
            rect.height = 1
            picker.set_parent(self.text_view)
            picker.set_pointing_to(rect)
        else:
            picker.set_parent(self.win)
        picker.popup()

    def _on_save_as_template_action(self, action, parameter) -> None:
        """Save a note as a template."""
        note_name = parameter.get_string()
        content = self.notes_manager.read_plain(note_name)
        self._show_save_template_dialog(note_name, content)

    def _on_show_history_action(self, action, parameter) -> None:
        """Show version history for a note (GIO action handler)."""
        note_name = parameter.get_string()
        self._on_show_history(note_name)

    def _on_open_split_action(self, action, parameter) -> None:
        """Open the selected note in a split view alongside the current note."""
        note_name = parameter.get_string()
        current = self.current_note

        if not current:
            self._show_toast(tr("Open a note first"))
            return

        if note_name == current:
            self._show_toast(tr("Already open"))
            return

        from ui.split_editor import SplitEditor

        self._save_current_cursor()
        self._flush_pending_save()

        if self.split_editor is not None:
            self.split_editor._load_pane(self.split_editor.right, note_name)
            self.split_editor.right.editor.text_view.grab_focus()
            return

        self._single_editor_ref = self.editor
        self.split_editor = SplitEditor(self)
        self.split_editor.load_notes(current, note_name)
        self.content_stack.add_named(self.split_editor, "split_editor")
        self.content_stack.set_visible_child_name("split_editor")
        self.nav.update_header_ui("", is_editor=True)
        self.sidebar.set_active_view("editor")
        self._set_backlinks_visible(True)

    def _on_open_active_action(self, action, parameter) -> None:
        """Open the selected note in the active (focused) pane."""
        note_name = parameter.get_string()

        if self.split_editor is not None:
            self.split_editor.flush_saves()
            side = self.split_editor._active_side
            info = self.split_editor.left if side == "left" else self.split_editor.right
            if info.note_name == note_name:
                return
            self.split_editor._load_pane(info, note_name)
            info.editor.text_view.grab_focus()
            return

        self._save_current_cursor()
        self._flush_pending_save()
        self.current_note = note_name
        self.nav.update_header_ui(note_name, is_editor=True)
        self._has_images = False
        if self.notes_manager.is_encrypted(note_name) and not self._is_session_locked:
            self._load_encrypted_note(note_name)
        else:
            content = self.notes_manager.read_plain(note_name) or ""
            self._set_buffer_text(content)
            self._restore_cursor_for_note(note_name)
            self._schedule_full_highlight()
        self.editor.set_editable(True)
        self.content_stack.set_visible_child_name("editor")
        self.sidebar.set_active_view("editor")
        self._select_sidebar_row(note_name)
        self._focus_text_view()

    def _show_save_template_dialog(self, note_name: str, content: str) -> None:
        """Show a dialog to name and save a template."""
        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading=tr("Save as Template"),
            body=tr("Enter a name for the template (based on '{note_name}'):").format(
                note_name=note_name
            ),
        )
        entry = Gtk.Entry()
        entry.set_text(note_name)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("save", tr("Save"))
        set_response_suggested(dialog, "save")
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_save_template_response, content, entry)
        dialog.present()

    def _on_save_template_response(
        self, dialog: Adw.MessageDialog, response: str, content: str, entry: Gtk.Entry
    ) -> None:
        if response != "save":
            return
        name = entry.get_text().strip()
        if not name:
            self._show_toast(tr("Template name cannot be empty"))
            return
        slug = self.template_manager.save_as_template(name, content)
        self._show_toast(tr("Template '{slug}' saved").format(slug=slug))

    def _on_new_template(self) -> None:
        """Create a blank new template and open it in the editor."""
        self._flush_pending_save()
        slug = self.template_manager.save_as_template("New Template", "")
        self._on_edit_template(slug)

    def _on_edit_template(self, slug: str) -> None:
        """Open a template in the main editor for editing."""
        self._flush_pending_save()
        template_path = self.template_manager.templates_dir / f"{slug}.md"
        if not template_path.exists():
            return
        content = template_path.read_text(encoding="utf-8")
        self.current_note = f".template:{slug}"
        self.nav.update_header_ui(f"Template: {slug}", is_editor=True)
        self.editor.set_editable(True)
        self._set_buffer_text(content)
        start = self.buffer.get_start_iter()
        self.buffer.place_cursor(start)
        self.text_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)
        self.content_stack.set_visible_child_name("editor")
        if self.highlighter:
            self.highlighter.highlight(start_line=0, end_line=30)
        self._focus_text_view()

    def _on_restore_builtins(self) -> None:
        """Restore all built-in templates to factory defaults."""
        self.template_manager.restore_builtins()
        templates = self.template_manager.get_all_templates()
        if self.settings_view is not None:
            self.settings_view.refresh_templates(templates)
        self._show_toast(tr("Built-in templates restored"))

    def _on_delete_template(self, slug: str) -> bool:
        """Delete a template by slug. Returns True on success."""
        if self.template_manager.delete_template(slug):
            self._show_toast(tr("Template '{slug}' deleted").format(slug=slug))
            return True
        return False

    def _on_open_templates_folder(self) -> None:
        """Open the templates folder in the file manager."""
        import shutil
        import subprocess

        templates_dir = str(self.template_manager.templates_dir)
        opener = "xdg-open"
        if shutil.which("xdg-open") is None:
            opener = "open" if shutil.which("open") else None
        if opener:
            subprocess.Popen([opener, templates_dir])
        else:
            self._show_toast(
                tr("Templates folder: {templates_dir}").format(
                    templates_dir=templates_dir
                )
            )

    # GIO actions

    def _setup_actions(self) -> None:
        for name, handler in (
            ("delete", self.lifecycle.on_delete_action),
            ("pin", self.on_pin_note),
            ("unpin", self.on_unpin_note),
            ("archive", self.on_toggle_archive_note),
            ("make_private", self.on_make_private),
            ("remove_privacy", self.on_remove_privacy),
            ("save_as_template", self._on_save_as_template_action),
            ("show_history", self._on_show_history_action),
            ("open_split", self._on_open_split_action),
            ("open_active", self._on_open_active_action),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", handler)
            self.add_action(action)

        for name, handler in (
            ("new_note", self.lifecycle.on_new_note_global),
            ("new_from_template", self._on_new_from_template),
            ("new_folder", self._on_new_folder),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        for name, handler in (
            ("new_note_in_folder", self._on_new_note_in_folder),
            (
                "new_note_from_template_in_folder",
                self._on_new_note_from_template_in_folder,
            ),
            ("new_subfolder", self._on_new_subfolder),
            ("pin_folder", self._on_pin_folder),
            ("unpin_folder", self._on_unpin_folder),
            ("archive_folder", self._on_archive_folder),
            ("encrypt_folder", self._on_encrypt_folder),
            ("remove_privacy_folder", self._on_remove_privacy_folder),
            ("rename_folder", self._on_rename_folder),
            ("delete_folder", self._on_delete_folder),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", handler)
            self.add_action(action)

        # Move note action — param is "note_name|dest_folder"
        move_action = Gio.SimpleAction.new("move_note", GLib.VariantType.new("s"))
        move_action.connect("activate", self._on_move_note)
        self.add_action(move_action)

        # Move folder action — param is "src_folder|dest_parent"
        move_folder_action = Gio.SimpleAction.new(
            "move_folder", GLib.VariantType.new("s")
        )
        move_folder_action.connect("activate", self._on_move_folder)
        self.add_action(move_folder_action)

    def on_toggle_archive_note(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        note_name = parameter.get_string()
        self.cfg.toggle_archive(note_name)
        self.sidebar.maybe_exit_archive_view()
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_pin_note(self, action: Gio.SimpleAction, parameter: GLib.Variant) -> None:
        self.cfg.pin(parameter.get_string())
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_unpin_note(self, action: Gio.SimpleAction, parameter: GLib.Variant) -> None:
        self.cfg.unpin(parameter.get_string())
        self.refresh_list(self.sidebar.search_entry.get_text())

    def on_make_private(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        note_name = parameter.get_string()
        if self.notes_manager.is_encrypted(note_name):
            return
        if self._session_password_bytes is not None:
            self._encrypt_note(note_name)
        elif self._is_session_locked and self._session_password_bytes is None:
            has_any_encrypted = any(
                self.notes_manager.is_encrypted(n)
                for n in self.notes_manager.get_notes()
            )
            if not has_any_encrypted:
                self._show_setup_dialog(note_name)
            else:
                self._pending_encrypt_note = note_name
                self._show_unlock_popover()
        else:
            self._show_setup_dialog(note_name)

    def on_remove_privacy(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        note_name = parameter.get_string()
        if not self.notes_manager.is_encrypted(note_name):
            return
        self._confirm_remove_privacy(note_name)

    def _show_setup_dialog(self, note_name: str) -> None:
        from ui.setup_dialog import SetupDialog

        dialog = SetupDialog(self, note_name)
        dialog.connect("close-request", self._on_setup_dialog_closed)
        dialog.present()

    def _on_setup_dialog_closed(self, dialog: Adw.Window) -> None:
        """After setup dialog closes, encrypt remaining notes in pending folder."""
        if self._pending_encrypt_folder is None or self._session_password_bytes is None:
            self._pending_encrypt_folder = None
            return
        folder, plain_notes = self._pending_encrypt_folder
        self._pending_encrypt_folder = None
        remaining = [n for n in plain_notes if not self.notes_manager.is_encrypted(n)]
        if not remaining:
            return
        from core.services import encrypt_note_on_disk

        for note in remaining:
            try:
                content, key_bytes = encrypt_note_on_disk(
                    note_name=note,
                    password=self._session_password_bytes,
                    notes_manager=self.notes_manager,
                    cfg=self.cfg,
                )
                self._encryption_key_cache[note] = key_bytes
                self.sidebar.set_row_encrypted(note, True)
            except Exception as e:
                logger.error("Failed to encrypt '%s' after setup: %s", note, e)
        self.refresh_list(self.sidebar.search_entry.get_text())
        self._show_toast(
            tr("Encrypted {n} note(s) in '{folder}'").format(
                n=len(remaining), folder=folder
            )
        )

    def _show_password_change_dialog(self) -> None:
        from ui.password_change_dialog import PasswordChangeDialog

        dialog = PasswordChangeDialog(self)
        dialog.present()

    def _confirm_remove_privacy(self, note_name: str) -> None:
        if self._is_session_locked or self._session_password_bytes is None:
            self._select_sidebar_row(note_name)
            self._show_unlock_popover()
            return

        dialog = confirm_destructive_dialog(
            transient_for=self.win,
            heading=tr("Remove Privacy?"),
            body=tr(
                "This will save '{note_name}' as plain text."
                " The note will no longer be encrypted. Are you sure?"
            ).format(note_name=note_name),
            confirm_label=tr("Remove Privacy"),
        )
        dialog.connect("response", self._on_remove_privacy_response, note_name)
        dialog.present()

    def _on_remove_privacy_response(
        self, dialog: Adw.MessageDialog, response: str, note_name: str
    ) -> None:
        if response != "delete":
            return
        if self._session_password_bytes is None:
            return

        # Read the actual note content from disk (decrypt it), not the buffer
        from core.encryption import decrypt

        key_bytes, _, ciphertext_bytes = self._derive_encryption_key(note_name)
        content = decrypt(ciphertext_bytes, key_bytes)
        self._encryption_key_cache.pop(note_name, None)

        enc_path = self.notes_manager.notes_dir / f"{note_name}.md.enc"

        self.notes_manager.save_note(note_name, content)
        self.cfg.mark_decrypted(note_name)

        if enc_path.exists():
            from core.encryption import best_effort_overwrite

            best_effort_overwrite(enc_path)

        self.sidebar.set_row_encrypted(note_name, False)
        self.current_note = note_name
        self._select_sidebar_row(note_name)
        self._set_buffer_text(content)
        self._schedule_full_highlight()

    # Folder selection

    def on_select_folder(self, _button=None) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title(tr("Select Notes Folder"))
        # Use home directory as initial folder to avoid GTK4 bug where
        # selecting a parent of the initial folder fails silently.
        dialog.set_initial_folder(Gio.File.new_for_path(str(Path.home())))
        dialog.select_folder(self.win, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error as e:
            logger.warning("Folder selection failed or cancelled: %s", e)
            self._show_toast(tr("Folder selection cancelled"))
            return

        if not folder:
            return

        new_folder = folder.get_path()
        logger.info("Selected folder: %s (current: %s)", new_folder, self.notes_folder)
        if new_folder == self.notes_folder:
            self._show_toast(tr("Already using this folder"))
            return

        self._flush_pending_save()
        self.notes_folder = new_folder
        self.cfg.set("notes_folder", new_folder)
        self.notes_manager = NotesManager(notes_dir=new_folder)
        from core.versioning import GitVersionController

        self.git_controller = GitVersionController(
            notes_dir=new_folder,
            executor=self._run_on_io_thread,
        )
        if self.git_controller.is_repo():
            self.cfg.set("git_enabled", True)
            self._update_toolbar_versioning_buttons()

        if self.settings_view:
            self.settings_view.update_folder_path(new_folder)
            self.content_stack.remove(self.settings_view)

        if self.graph_view:
            self.content_stack.remove(self.graph_view)
        self.graph_manager = None
        self.graph_view = None

        if self.dashboard_view:
            self.content_stack.remove(self.dashboard_view)
        self.dashboard_view = None
        self.dashboard_list = None

        if self.diagram_view:
            self.content_stack.remove(self.diagram_view)
        self.diagram_view = None
        self.current_open_diagram = None
        self.diagram_manager = DiagramManager(notes_dir=new_folder)

        self.current_note = None
        self._has_images = False
        self._set_buffer_text("")
        self.win.set_title(tr("Tokyo Notes"))
        self.refresh_list()
        self._show_toast(tr("Notes folder changed"))

    # Activation / window construction

    def do_activate(self) -> None:
        # If the window already exists (second activation via D-Bus / instance
        # check), just raise it rather than building a second window.
        if hasattr(self, "win") and self.win:
            self.win.present()
            return

        self._apply_security_mitigations()
        self._build_layout()
        self._finalize_startup()

    def _build_layout(self) -> None:
        """Construct the main window layout."""
        load_i18n(self.cfg.get("language", "en"))
        self.theme_manager.setup_providers()
        self.win = self.window_manager.create_window()
        self.apply_theme(self.cfg.get("theme"))

        self._font_provider = Gtk.CssProvider()
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self._font_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )
        self._apply_font(self.cfg.get("font_family"), self.cfg.get("font_size"))

        self.split_view = Adw.OverlaySplitView()

        # Build the toggle button before the sidebar so _build_content_header
        # can pack it; connect its "toggled" signal after the sidebar exists.
        # Ordering matters: sidebar_toggle → sidebar → content_header → editor area.
        sidebar_toggle_img = Gtk.Image.new_from_file(
            str(self.base_dir / "assets" / "header" / "sidebar-toggle.svg")
        )
        sidebar_toggle_img.set_pixel_size(16)
        self.sidebar_toggle = Gtk.ToggleButton()
        self.sidebar_toggle.set_child(sidebar_toggle_img)
        self.sidebar_toggle.add_css_class("header-btn")
        self.sidebar_toggle.add_css_class("flat")
        self.sidebar_toggle.set_active(self.cfg.get("show_sidebar"))

        self.sidebar = Sidebar(
            self,
            self.lifecycle.on_new_note,
            self._on_new_from_template,
            self.nav.on_dashboard_clicked,
            self.nav.on_archived_clicked,
            self.nav.on_graph_clicked,
            self.nav.on_flashcard_clicked,
            self.nav.on_settings_clicked,
        )
        self.sidebar_toggle_handler = self.sidebar_toggle.connect(
            "toggled", self.sidebar.on_sidebar_toggled
        )
        self.sidebar.main_list.connect("row-selected", self.lifecycle.on_note_selected)
        self.sidebar.archive_list.connect(
            "row-selected", self.lifecycle.on_note_selected
        )
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
        self.flashcard_view = None
        self.split_editor = None
        self._single_editor_ref = None
        self.diagram_view = None

        overlay = self._build_content_stack()

        main_layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_layout.append(self.content_header)
        main_layout.append(overlay)
        self.split_view.set_content(main_layout)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.split_view)
        self.win.set_content(self.toast_overlay)

    def _finalize_startup(self) -> None:
        """Complete startup: show window, validate folder,
        load notes, setup shortcuts."""
        self.win.present()
        self.window_manager.setup_breakpoint()

        # Validate the notes folder after the window is shown so that any
        # recovery dialog has a parent window to attach to.
        validate_notes_folder(self)

        GLib.idle_add(self._init_git_repo)
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
            on_find_replace=self.on_find_replace_shortcut,
            on_archive=self.on_archive_shortcut,
            on_settings=self.nav.on_settings_clicked,
            on_lock=self.lock_session,
            on_new_from_template=self._on_new_from_template,
            on_quick_add=self._on_quick_add_shortcut,
            on_speech_toggle=self._on_speech_toggle,
            on_sidebar_search=self.on_sidebar_search_shortcut,
        )
        logger.info("Tokyo Notes started — notes folder: %s", self.notes_folder)

    def _build_content_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        self.content_title = Gtk.Label(label=tr("Tokyo Notes"))
        header.set_title_widget(self.content_title)
        header.pack_start(self.sidebar_toggle)

        self.help_btn = Gtk.Button(tooltip_text=tr("Keyboard shortcuts"))
        help_img = Gtk.Image.new_from_file(
            str(self.base_dir / "assets" / "header" / "help.svg")
        )
        help_img.set_pixel_size(16)
        self.help_btn.set_child(help_img)
        self.help_btn.add_css_class("header-btn")
        self.help_btn.connect("clicked", lambda _: self.show_shortcuts_dialog())
        header.pack_end(self.help_btn)

        # Back to Notes button — shown only when a secondary view is active.
        self.back_btn = Gtk.Button(tooltip_text=tr("Back to Notes"))
        back_img = Gtk.Image.new_from_file(
            str(self.base_dir / "assets" / "header" / "back.svg")
        )
        back_img.set_pixel_size(16)
        self.back_btn.set_child(back_img)
        self.back_btn.add_css_class("header-btn")
        self.back_btn.connect("clicked", lambda _: self.nav.on_escape_shortcut())
        self.back_btn.set_visible(False)
        header.pack_start(self.back_btn)

        return header

    def _build_editor_area(self) -> None:
        assets_dir = self.base_dir / "assets" / "toolbar"
        toolbar = build_toolbar(
            assets_dir,
            self.apply_format,
            on_history=lambda: self._on_show_history(self.current_note),
            get_buffer=lambda: self.buffer,
            speech_language=self.cfg.get("speech_language"),
            speech_input_device=self.cfg.get("speech_input_device"),
            on_speech_recording=self._on_speech_recording,
            on_speech_transcribing=self._on_speech_transcribing,
            on_speech_quiet_audio=self._on_speech_quiet_audio,
        )

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

        self.history_btn = getattr(self.toolbar, "_history_btn", None)
        self._speech_btn = getattr(self.toolbar, "_speech_btn", None)
        if self._speech_btn is not None:
            self._speech_btn.set_visible(self.cfg.get("speech_enabled", False))
            if self.cfg.get("speech_enabled", False):
                GLib.timeout_add_seconds(
                    1, lambda: self._provision_speech_and_download() or False
                )
        self._update_toolbar_versioning_buttons()

        self.toolbar.set_visible(self.cfg.get("show_toolbar"))
        self.editor.status_bar.set_visible(self.cfg.get("show_stats"))

        self.highlighter = MarkdownHighlighter(
            self.buffer, self.theme_manager, self.cfg.get("theme")
        )
        self.highlighter.always_show_markdown = self.cfg.get(
            "always_show_markdown", False
        )
        self.highlighter.highlight()

        # Search-highlight tag for sidebar search matches in the editor
        self._search_highlight_tag = Gtk.TextTag.new("search-highlight")
        self._search_highlight_tag.set_property("background", "#BBDEFB")
        self.buffer.get_tag_table().add(self._search_highlight_tag)
        self._sidebar_search_text: str = ""

        # Spell checker
        self.spell_checker = SpellChecker(
            language=self.cfg.get("spell_check_language", "en"),
        )
        self.highlighter.set_spell_checker(
            self.spell_checker, self.cfg.get("spell_check_enabled", True)
        )
        self.editor.highlighter = self.highlighter

        self.last_cursor_line = -1
        self._has_selection = False
        self.mark_set_handler_id = self.buffer.connect("mark-set", self._on_mark_set)

        gesture = Gtk.GestureClick.new()
        gesture.set_button(1)
        gesture.connect("pressed", self.on_click_pressed)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.text_view.add_controller(gesture)
        self.text_view.set_focus_on_click(True)

        scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_ctrl.connect("scroll", self._on_editor_scroll)
        scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.text_view.add_controller(scroll_ctrl)

        # Diagram callbacks
        self.editor._diagram_manager = self.diagram_manager
        self.editor._on_diagram_slash = self._on_insert_diagram_action
        self.editor._on_open_diagram = self._on_open_diagram_action

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

        self.backlinks_container = Gtk.Box()
        self.backlinks_container.set_halign(Gtk.Align.END)
        self.backlinks_container.set_valign(Gtk.Align.END)
        self.backlinks_container.set_margin_end(16)
        self.backlinks_container.set_margin_bottom(16)

        self.backlinks_btn = Gtk.Button()
        self.backlinks_btn.add_css_class("backlinks-fab")
        self.backlinks_btn.connect("clicked", self._show_backlinks_popover)
        self.backlinks_btn.set_visible(False)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        icon = Gtk.Image.new_from_file(
            str(self.base_dir / "assets" / "toolbar" / "link.svg")
        )
        icon.set_pixel_size(18)
        btn_box.append(icon)

        self.backlinks_count = Gtk.Label()
        self.backlinks_count.add_css_class("backlinks-count")
        self.backlinks_count.set_visible(False)
        self.backlinks_count.set_valign(Gtk.Align.CENTER)
        btn_box.append(self.backlinks_count)

        self.backlinks_btn.set_child(btn_box)
        self.backlinks_container.append(self.backlinks_btn)

        self.overlay.add_overlay(self.backlinks_container)

        return self.overlay

    # Settings / theme

    def on_settings_config_changed(self, key: str, value: Any) -> None:
        old_value = self.cfg.get(key)
        self.cfg.set(key, value)
        if old_value == value:
            return

        if key == "show_toolbar":
            self.toolbar.set_visible(value)
        elif key == "show_stats":
            self.editor.status_bar.set_visible(value)
        elif key == "show_completed" and self.dashboard_view is not None:
            self.nav.refresh_dashboard(self.dashboard_view.active_filter)
        elif key == "show_progress_rings" and self.dashboard_view is not None:
            self.nav.refresh_dashboard(self.dashboard_view.active_filter)
        elif key == "show_backlinks":
            self._update_backlinks()
        elif key == "font_family":
            self._apply_font(value, self.cfg.get("font_size"))
        elif key == "font_size":
            self._apply_font(self.cfg.get("font_family"), value)
        elif key == "git_enabled":
            self._update_toolbar_versioning_buttons()
        elif key == "language":
            self._show_language_restart_dialog()
        elif key == "spell_check_enabled":
            if self.highlighter and self.spell_checker:
                self.highlighter.set_spell_checker(self.spell_checker, enabled=value)
            self.editor.invalidate_spell_cache()
        elif key == "spell_check_language":
            if self.spell_checker:
                self.spell_checker.load_dictionary(value)
            if self.highlighter:
                self.highlighter.set_spell_checker(
                    self.spell_checker,
                    enabled=self.cfg.get("spell_check_enabled", True),
                )
            self.editor.invalidate_spell_cache()
        elif key == "always_show_markdown":
            if self.highlighter:
                self.highlighter.always_show_markdown = value
                self.highlighter.highlight()
        elif key == "speech_enabled":
            self._speech_btn = (
                getattr(self.toolbar, "_speech_btn", None)
                if hasattr(self, "toolbar")
                else None
            )
            if self._speech_btn is not None:
                self._speech_btn.set_visible(value)
            if value:
                self._provision_speech_and_download()
        elif key == "speech_input_device":
            if self._speech_btn is not None:
                self._speech_btn.update_input_device(value)
        elif key == "speech_language":
            if self._speech_btn is not None:
                self._speech_btn.update_language(value)
        elif key == "speech_rebuild":
            from core.speech_setup import remove as remove_venv

            remove_venv()
            self._provision_speech_and_download()

    def _show_language_restart_dialog(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading=tr("Restart Required"),
            body=tr(
                "The language change will take effect after restart. "
                "Restart now or later?"
            ),
        )
        dialog.add_response("later", tr("Later"))
        dialog.add_response("restart", tr("Restart Now"))
        set_response_suggested(dialog, "restart")
        dialog.set_default_response("later")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_language_restart_response)
        dialog.present()

    def _on_language_restart_response(
        self, dialog: Adw.MessageDialog, response: str
    ) -> None:
        if response == "restart":
            self._restart_app()

    def _restart_app(self) -> None:
        self.cfg.flush_immediate()
        try:
            if IS_MAC and getattr(sys, "frozen", False):
                # On macOS, bundled apps should be restarted using 'open'
                # to ensure the full bundle environment is correctly re-initialized.
                import subprocess

                app_path = str(Path(sys.executable).parents[2])
                subprocess.Popen(["open", "-n", app_path])
                self.quit()
            else:
                os.execv(sys.executable, [sys.executable, *sys.argv])
        except Exception:
            self._show_toast(
                tr("Failed to restart the application. Please restart manually.")
            )

    def _update_backlinks(self) -> None:
        """Update the backlinks button visibility and count."""
        if not self.current_note or not self.cfg.get("show_backlinks", True):
            self.backlinks_container.set_visible(False)
            return
        backlinks = self.notes_manager.get_backlinks(
            self.current_note, self.cfg.archived
        )
        if backlinks:
            self.backlinks_container.set_visible(True)
            self.backlinks_btn.set_visible(True)
            self.backlinks_count.set_label(str(len(backlinks)))
            self.backlinks_count.set_visible(True)
            self.backlinks_btn.set_tooltip_text(
                tr("{n} backlink(s)").format(n=len(backlinks))
            )
        else:
            self.backlinks_container.set_visible(False)

    def _set_backlinks_visible(self, visible: bool) -> None:
        """Show or hide the backlinks button (used when switching views)."""
        if visible:
            self._update_backlinks()
        else:
            self.backlinks_container.set_visible(False)

    def _show_backlinks_popover(self, btn: Gtk.Button) -> None:
        """Show the backlinks popover."""
        if not self.current_note:
            return
        from ui.backlinks_popover import BacklinksPopover

        backlinks = self.notes_manager.get_backlinks(
            self.current_note, self.cfg.archived
        )
        popover = BacklinksPopover(
            backlinks,
            self.lifecycle.on_link_clicked,
            self.text_view,
        )
        popover.set_parent(btn)
        popover.popup()

    def apply_theme(self, theme_name: str) -> None:
        self.theme_manager.apply_theme(theme_name)
        self.cfg.set("theme", theme_name)
        if hasattr(self, "win"):
            from core.theme_manager import is_light_theme

            if is_light_theme(theme_name):
                self.win.add_css_class("light-theme")
                self.win.remove_css_class("dark-theme")
            else:
                self.win.add_css_class("dark-theme")
                self.win.remove_css_class("light-theme")

    # Font-family ratio for UI elements that have explicit style.css sizes.
    # Values are X/12 so they scale proportionally with the base font-size.
    _SCALED_SELECTORS: dict[str, float] = {
        ".sidebar-label": 11 / 12,
        ".sidebar-snippet": 8 / 12,
        ".toolbar-btn": 10 / 12,
        ".stats-label": 9 / 12,
        ".day-header": 11 / 12,
        ".note-chip": 8 / 12,
        ".time-column": 8 / 12,
        ".view-title": 18 / 12,
        ".flashcard-text": 16 / 12,
    }

    def _apply_font(self, family: str | None, size: int | None = None) -> None:
        """Set app-wide font family/size, or clear to use style.css defaults."""
        rules = []
        if family:
            rules.append(f"font-family: '{family}'")
        if size:
            rules.append(f"font-size: {size}pt")

        css_lines: list[str] = []
        if rules:
            css_lines.append(f"window {{ {'; '.join(rules)}; }}")
        if size:
            for sel, ratio in self._SCALED_SELECTORS.items():
                css_lines.append(f"{sel} {{ font-size: {ratio:.2f}em; }}")
        self._font_provider.load_from_string("\n".join(css_lines))

        gv = getattr(self, "graph_view", None)
        if gv is not None:
            gv.update_font(family, size)

    # Formatting

    def apply_format(self, btn, prefix: str, suffix: str) -> None:
        from ui.toolbar import _DIAGRAM, _FLASHCARD

        if prefix is _DIAGRAM:
            self._show_diagram_insert_popover(btn)
            return
        if prefix is _FLASHCARD:
            self.insert_flashcard()
            return
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
        self._focus_text_view()

    def insert_flashcard(self) -> None:
        template = "```flashcard\nQuestion\n---\nAnswer\n```"
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(start, end, True)
            self.buffer.delete(start, end)
            formatted = template.replace("Question", text, 1)
            self.buffer.insert(start, formatted)
        else:
            self.buffer.insert_at_cursor(template)
        self._focus_text_view()

    # Diagram actions

    def _on_insert_diagram_action(self) -> None:
        """Create a new diagram and open the diagram editor."""
        self._save_current_cursor()
        entry = Gtk.Entry()
        entry.set_placeholder_text(tr("My Diagram"))
        entry.set_activates_default(True)
        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading=tr("New Diagram"),
            body=tr("Give your diagram a name:"),
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("ok", tr("Create"))
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")
        from core.utils import set_response_suggested

        set_response_suggested(dialog, "ok")

        def on_response(d: Adw.MessageDialog, response: str) -> None:
            if response == "ok":
                title = entry.get_text().strip() or tr("Untitled Diagram")
                diagram = Diagram.new(title=title)
                self._open_diagram_editor(diagram)
            d.close()

        dialog.connect("response", on_response)
        dialog.present()
        entry.grab_focus()

    def _show_diagram_insert_popover(self, btn: Gtk.Button) -> None:
        """Show a popover with New Diagram + list of existing diagrams."""
        self._save_current_cursor()
        titles = self.diagram_manager.list_titles()
        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_position(Gtk.PositionType.BOTTOM)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_margin_top(4)
        vbox.set_margin_bottom(4)

        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(
            b".menu-row { padding: 0 8px; margin: 2px 6px;"
            b" border-radius: 6px; }"
            b".menu-row:hover {"
            b" background: alpha(@theme_fg_color, 0.08); }"
        )

        def _menu_item(label_text: str, fn) -> None:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.get_style_context().add_class("menu-row")
            row.get_style_context().add_provider(
                style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            lbl = Gtk.Label(label=label_text)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(12)
            lbl.set_margin_end(12)
            lbl.set_margin_top(8)
            lbl.set_margin_bottom(8)
            row.append(lbl)
            evk = Gtk.GestureClick()
            evk.connect("pressed", lambda *_: (popover.popdown(), fn()))
            row.add_controller(evk)
            vbox.append(row)

        _menu_item(tr("New Diagram"), self._on_insert_diagram_action)

        if titles:
            separator = Gtk.Box()
            separator.set_size_request(-1, 1)
            separator.set_margin_top(2)
            separator.set_margin_bottom(2)
            sep_provider = Gtk.CssProvider()
            sep_provider.load_from_data(
                b"box { background: alpha(@theme_fg_color, 0.2); }"
            )
            separator.get_style_context().add_provider(
                sep_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            vbox.append(separator)

            for did, dtitle in titles:
                _menu_item(dtitle, lambda d=did: self._insert_diagram_reference(d))

        popover.set_child(vbox)
        popover.set_parent(btn)
        popover.popup()

    def _on_open_diagram_action(self, diagram_id: str) -> None:
        """Open an existing diagram by ID for editing."""
        diagram = self.diagram_manager.load(diagram_id)
        if diagram is None:
            self._show_toast(tr("Diagram not found"))
            return
        self._open_diagram_editor(diagram)

    def _open_diagram_editor(self, diagram: Diagram) -> None:
        """Switch to the diagram editor with the given diagram."""
        if self.diagram_view is None:
            self.diagram_view = DiagramView(
                diagram_manager=self.diagram_manager,
                on_save_and_insert=self._on_diagram_save_and_insert,
                on_close=self._on_diagram_close,
                on_save_only=self._on_diagram_save_only,
                on_diagram_delete=self._on_diagram_delete,
                on_title_changed=lambda t: self.nav.update_header_ui(
                    t, is_editor=False
                ),
                transient_for=self.win,
            )
            self.content_stack.add_named(self.diagram_view, "diagram")
        self.current_open_diagram = diagram
        self.diagram_view.set_diagram(diagram)
        self.content_stack.set_visible_child_name("diagram")
        self.nav.update_header_ui(diagram.title, is_editor=False)
        self._set_backlinks_visible(False)

    def _on_diagram_save_only(self, diagram: Diagram) -> None:
        """Save diagram without inserting a reference."""
        self.diagram_manager.save(diagram)
        if self.current_open_diagram:
            self.current_open_diagram = diagram

    def _insert_diagram_reference(self, did: str) -> None:
        """Insert an existing diagram reference into the current note."""
        if not self.current_note:
            return
        ref = f"\n![diagram]({did})\n"
        self.buffer.insert_at_cursor(ref)
        self._has_images = True
        self._reschedule("image_timeout_id", 200, self.do_delayed_images)
        self._schedule_full_highlight()
        self._focus_text_view()

    def _on_diagram_save_and_insert(self, diagram: Diagram) -> None:
        """Save diagram and insert its reference into the current note."""
        self.diagram_manager.save(diagram)
        self._on_diagram_close()
        self._insert_diagram_reference(diagram.id)

    def _on_diagram_close(self) -> None:
        """Close the diagram editor and return to the previous note."""
        if self.diagram_view:
            self.diagram_view.save_if_dirty()
        target = "split_editor" if self.split_editor is not None else "editor"
        self.content_stack.set_visible_child_name(target)
        title = self.current_note if self.current_note else tr("Tokyo Notes")
        self.nav.update_header_ui(title, is_editor=True)
        self._set_backlinks_visible(True)
        self.current_open_diagram = None
        self._has_images = True
        self._reschedule("image_timeout_id", 200, self.do_delayed_images)

    def _on_diagram_delete(self, diagram_id: str) -> None:
        """Delete a diagram by ID and return to editor."""
        self._show_toast(tr("Diagram deleted"))
        self.diagram_manager.delete(diagram_id)
        self._on_diagram_close()

    # Note list / sidebar

    def refresh_list(self, filter_text: str = "") -> None:
        self._sidebar_search_text = filter_text
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
        self._apply_search_highlights()

    def _apply_search_highlights(self, *, full_reset: bool = True) -> None:
        tag = self.buffer.get_tag_table().lookup("search-highlight")
        if not tag:
            return
        # Full re-highlight to reset Pango layout — prevents "byte index
        # off the end of the line" crashes from invisible-tag overlap
        # (see _on_mark_set comment at the selection handler).
        # Skipped during incremental editing (full_reset=False) since
        # highlight_line_range already left a clean Pango state.
        if full_reset and self.highlighter:
            cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            cursor_line = cursor_iter.get_line()
            self.highlighter.highlight(cursor_line=cursor_line)
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(tag, start, end)
        if not self._sidebar_search_text:
            return
        code_block_lines = (
            self.highlighter._code_block_line_set() if self.highlighter else set()
        )
        flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
        search_start = self.buffer.get_start_iter()
        while True:
            result = search_start.forward_search(self._sidebar_search_text, flags, None)
            if result is None:
                break
            m_start, m_end = result
            if m_start.get_line() not in code_block_lines:
                self.buffer.apply_tag(tag, m_start, m_end)
            search_start = m_end.copy()

    def _get_snippet(self, note_name: str) -> str:
        return self.notes_manager.get_metadata(note_name).get("snippet", "")

    def on_row_right_click(
        self, gesture, n_press, x, y, row, is_archived: bool = False
    ) -> None:
        # Folder header right-click
        if getattr(row, "_is_folder", False):
            self._show_folder_context_menu(row)
            return

        note_name = getattr(row, "note_name", None)
        if not note_name:
            return

        menu = Gio.Menu()
        if note_name in self.cfg.pinned:
            menu.append(tr("Unpin"), f"app.unpin::{note_name}")
        else:
            menu.append(tr("Pin"), f"app.pin::{note_name}")
        menu.append(
            tr("Unarchive") if is_archived else tr("Archive"),
            f"app.archive::{note_name}",
        )

        # Move to folder submenu
        current_folder = note_name.rsplit("/", 1)[0] if "/" in note_name else ""
        folders = list(self.notes_manager.get_folders())
        if current_folder or folders:
            move_section = Gio.Menu()
            move_item = Gio.MenuItem.new(tr("Move to folder"), None)
            move_submenu = Gio.Menu()
            if current_folder:
                move_submenu.append(tr("Home"), f"app.move_note::{note_name}|")
            for folder in sorted(folders):
                folder_str = str(folder)
                if folder_str == current_folder:
                    continue
                move_submenu.append(
                    folder_str, f"app.move_note::{note_name}|{folder_str}"
                )
            move_item.set_submenu(move_submenu)
            move_section.append_item(move_item)
            menu.append_section(None, move_section)

        template_section = Gio.Menu()
        template_section.append(
            tr("Save as Template"), f"app.save_as_template::{note_name}"
        )
        menu.append_section(None, template_section)

        privacy_section = Gio.Menu()
        if self.notes_manager.is_encrypted(note_name):
            privacy_section.append(
                tr("Remove privacy"), f"app.remove_privacy::{note_name}"
            )
        else:
            privacy_section.append(tr("Make private"), f"app.make_private::{note_name}")
        menu.append_section(None, privacy_section)

        if self.git_controller.is_available():
            versioning_section = Gio.Menu()
            versioning_section.append(tr("History"), f"app.show_history::{note_name}")
            menu.append_section(None, versioning_section)

        split_section = Gio.Menu()
        split_section.append(tr("Open"), f"app.open_active::{note_name}")
        split_section.append(tr("Open in Split View"), f"app.open_split::{note_name}")
        menu.append_section(None, split_section)

        danger = Gio.Menu()
        danger.append(tr("Delete"), f"app.delete::{note_name}")
        menu.append_section(None, danger)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        row_rect = Gdk.Rectangle()
        row_rect.x = 0
        row_rect.y = row.get_height()
        row_rect.width = row.get_allocated_width()
        row_rect.height = 1
        popover.set_pointing_to(row_rect)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.popup()

    def _show_folder_context_menu(self, row) -> None:
        """Show the context menu for a folder header row."""
        folder_path = getattr(row, "folder_path", "")
        if not folder_path:
            return

        is_pinned = self.cfg.is_folder_pinned(folder_path)
        notes = self.notes_manager.get_notes_in_folder(folder_path)
        has_encrypted = any(self.notes_manager.is_encrypted(n) for n in notes)

        menu = Gio.Menu()

        create_section = Gio.Menu()
        create_section.append(tr("New Note"), f"app.new_note_in_folder::{folder_path}")
        create_section.append(
            tr("New Note from Template"),
            f"app.new_note_from_template_in_folder::{folder_path}",
        )
        create_section.append(tr("New Folder"), f"app.new_subfolder::{folder_path}")
        menu.append_section(None, create_section)

        folder_section = Gio.Menu()
        folder_section.append(tr("Rename folder"), f"app.rename_folder::{folder_path}")

        # Move folder submenu
        all_folders = list(self.notes_manager.get_folders())
        folder_move_item = Gio.MenuItem.new(tr("Move to"), None)
        move_submenu = Gio.Menu()

        folder_parent = folder_path.rsplit("/", 1)[0] if "/" in folder_path else ""
        if folder_parent:
            move_submenu.append(tr("Home"), f"app.move_folder::{folder_path}|")
        for f in sorted(all_folders):
            f_str = str(f)
            if (
                f_str == folder_path
                or f_str.startswith(f"{folder_path}/")
                or f_str == folder_parent
            ):
                continue
            move_submenu.append(f_str, f"app.move_folder::{folder_path}|{f_str}")
        # Only show submenu if there are valid destinations
        if move_submenu.get_n_items():
            folder_move_item.set_submenu(move_submenu)
            folder_section.append_item(folder_move_item)

        if is_pinned:
            folder_section.append(
                tr("Unpin folder"), f"app.unpin_folder::{folder_path}"
            )
        else:
            folder_section.append(tr("Pin folder"), f"app.pin_folder::{folder_path}")
        menu.append_section(None, folder_section)

        encrypt_section = Gio.Menu()
        encrypt_section.append(tr("Encrypt all"), f"app.encrypt_folder::{folder_path}")
        if has_encrypted:
            encrypt_section.append(
                tr("Remove privacy (decrypt all)"),
                f"app.remove_privacy_folder::{folder_path}",
            )
        menu.append_section(None, encrypt_section)

        danger_section = Gio.Menu()
        danger_section.append(tr("Archive all"), f"app.archive_folder::{folder_path}")
        danger_section.append(tr("Delete folder"), f"app.delete_folder::{folder_path}")
        menu.append_section(None, danger_section)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        row_rect = Gdk.Rectangle()
        row_rect.x = 0
        row_rect.y = row.get_height()
        row_rect.width = row.get_allocated_width()
        row_rect.height = 1
        popover.set_pointing_to(row_rect)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.popup()

    def _on_pin_folder(self, action: Gio.SimpleAction, parameter: GLib.Variant) -> None:
        """Pin a folder so it appears first in the sidebar."""
        folder = parameter.get_string()
        self.cfg.pin_folder(folder)
        self.refresh_list(self.sidebar.search_entry.get_text())

    def _on_unpin_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Unpin a folder."""
        folder = parameter.get_string()
        self.cfg.unpin_folder(folder)
        self.refresh_list(self.sidebar.search_entry.get_text())

    def _on_new_note_in_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Create a new note inside the given folder."""
        folder = parameter.get_string()
        self._flush_pending_save()
        name = self.notes_manager.reserve_name(f"{folder}/Untitled")
        self.current_note = name
        self.nav.update_header_ui(name, is_editor=True)
        self._has_images = False
        self._set_buffer_text("")
        self.editor.set_editable(True)
        self.content_stack.set_visible_child_name("editor")
        self.refresh_list()
        self._select_sidebar_row(name)
        self._focus_text_view()

        # Auto-encrypt new notes in folders that already have encrypted notes
        notes_in_folder = self.notes_manager.get_notes_in_folder(folder)
        folder_has_encrypted = any(
            self.notes_manager.is_encrypted(n) for n in notes_in_folder
        )

        if self._session_password_bytes is not None and folder_has_encrypted:
            import os

            from core.encryption import _SALT_LEN, derive_key, encrypt

            pw = bytearray(self._session_password_bytes)
            salt = os.urandom(_SALT_LEN)
            key = derive_key(pw, salt)
            key_bytes = bytearray(key)
            ciphertext = encrypt("", key_bytes, salt)
            self.notes_manager.save_encrypted(name, ciphertext)
            self.cfg.mark_encrypted(name)
            self._encryption_key_cache[name] = key_bytes
            self.sidebar.set_row_encrypted(name, True)
        elif self._is_session_locked and folder_has_encrypted:
            self._pending_auto_encrypt_in_folder = name
            self._show_unlock_popover()

    def _on_new_note_from_template_in_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Open template picker to create a note inside the given folder."""
        folder = parameter.get_string()
        self._show_template_picker_for_new_note(folder=folder)

    def _on_new_subfolder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Show a dialog to create a subfolder under an existing folder."""
        parent_folder = parameter.get_string()

        dialog = Adw.MessageDialog(
            transient_for=self.props.active_window,
            heading=tr("New Subfolder"),
            body=tr("Enter a name for the new folder inside '{parent_folder}':").format(
                parent_folder=parent_folder
            ),
        )
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("create", tr("Create"))
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")
        try:
            dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        except AttributeError:
            pass

        entry = Gtk.Entry()
        entry.set_activates_default(True)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        extra.append(entry)
        dialog.set_extra_child(extra)

        def _on_response(d: Any, response: str) -> None:
            if response == "create":
                name = entry.get_text().strip()
                err = self._validate_folder_name(name)
                if err:
                    entry.set_text("")
                    self._show_toast(err)
                    return
                full_path = self.notes_manager.notes_dir / parent_folder / name
                if full_path.exists():
                    self._show_toast(
                        tr("Folder '{path}' already exists").format(
                            path=f"{parent_folder}/{name}"
                        )
                    )
                    return
                full_path.mkdir(parents=True)
                self.refresh_list(self.sidebar.search_entry.get_text())
                self._show_toast(
                    tr("Created folder '{path}'").format(path=f"{parent_folder}/{name}")
                )

        dialog.connect("response", _on_response)
        dialog.present()
        entry.grab_focus()

    def _on_new_folder(
        self, action: Gio.SimpleAction, _parameter: GLib.Variant | None
    ) -> None:
        """Show a dialog to create a new folder."""
        dialog = Adw.MessageDialog(
            transient_for=self.props.active_window,
            heading=tr("New Folder"),
            body=tr("Enter a name for the new folder:"),
        )
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("create", tr("Create"))
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")
        try:
            dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        except AttributeError:
            pass

        entry = Gtk.Entry()
        entry.set_activates_default(True)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        extra.append(entry)
        dialog.set_extra_child(extra)

        def _on_response(d: Any, response: str) -> None:
            if response == "create":
                name = entry.get_text().strip()
                err = self._validate_folder_name(name)
                if err:
                    entry.set_text("")
                    self._show_toast(err)
                    return
                folder_path = self.notes_manager.notes_dir / name
                if folder_path.exists():
                    self._show_toast(
                        tr("Folder '{name}' already exists").format(name=name)
                    )
                    return
                folder_path.mkdir(parents=True)
                self.refresh_list(self.sidebar.search_entry.get_text())
                self._show_toast(tr("Created folder '{name}'").format(name=name))

        dialog.connect("response", _on_response)
        dialog.present()
        entry.grab_focus()

    def _on_archive_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Archive every note in the given folder."""
        folder = parameter.get_string()
        notes = self.notes_manager.get_notes_in_folder(folder)
        for note in notes:
            self.cfg.toggle_archive(note)
        self.sidebar.maybe_exit_archive_view()
        self.refresh_list(self.sidebar.search_entry.get_text())
        self._show_toast(
            tr("Archived {n} note(s) in '{folder}'").format(n=len(notes), folder=folder)
        )

    def _on_encrypt_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Encrypt every plain note in the given folder."""
        folder = parameter.get_string()
        notes = self.notes_manager.get_notes_in_folder(folder)
        plain_notes = [n for n in notes if not self.notes_manager.is_encrypted(n)]
        if not plain_notes:
            self._show_toast(tr("No plain notes to encrypt in this folder"))
            return

        if self._session_password_bytes is not None:
            from core.services import encrypt_note_on_disk

            for note in plain_notes:
                content, key_bytes = encrypt_note_on_disk(
                    note_name=note,
                    password=self._session_password_bytes,
                    notes_manager=self.notes_manager,
                    cfg=self.cfg,
                )
                self._encryption_key_cache[note] = key_bytes
                self.sidebar.set_row_encrypted(note, True)
            self.refresh_list(self.sidebar.search_entry.get_text())
            self._show_toast(
                tr("Encrypted {n} note(s) in '{folder}'").format(
                    n=len(plain_notes), folder=folder
                )
            )
        elif self._is_session_locked and self._session_password_bytes is None:
            has_any_encrypted = any(
                self.notes_manager.is_encrypted(n)
                for n in self.notes_manager.get_notes()
            )
            if not has_any_encrypted:
                self._pending_encrypt_folder = (folder, plain_notes)
                self._show_setup_dialog(plain_notes[0])
            else:
                self._session_password_bytes = None
                self._pending_encrypt_folder = (folder, plain_notes)
                self._show_unlock_popover()
        else:
            self._pending_encrypt_folder = (folder, plain_notes)
            self._show_setup_dialog(plain_notes[0])

    def _on_remove_privacy_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Decrypt every encrypted note in the given folder (single batch dialog)."""
        folder = parameter.get_string()
        notes = self.notes_manager.get_notes_in_folder(folder)
        encrypted_notes = [n for n in notes if self.notes_manager.is_encrypted(n)]
        if not encrypted_notes:
            self._show_toast(tr("No encrypted notes in this folder"))
            return

        if self._is_session_locked or self._session_password_bytes is None:
            self._show_toast(tr("Unlock your session first"))
            return

        dialog = confirm_destructive_dialog(
            transient_for=self.win,
            heading=tr("Remove Privacy?"),
            body=tr(
                "This will save {n} note(s) in '{folder}' as plain text."
                " The notes will no longer be encrypted. Are you sure?"
            ).format(n=len(encrypted_notes), folder=folder),
            confirm_label=tr("Remove Privacy"),
        )
        dialog.connect(
            "response", self._on_remove_privacy_folder_response, encrypted_notes
        )
        dialog.present()

    def _on_remove_privacy_folder_response(
        self, dialog: Adw.MessageDialog, response: str, encrypted_notes: list[str]
    ) -> None:
        """Batch-decrypt all encrypted notes in a folder."""
        if response != "delete":
            return
        if self._session_password_bytes is None:
            return

        from core.encryption import best_effort_overwrite, decrypt

        for note_name in encrypted_notes:
            try:
                key_bytes, _, ciphertext_bytes = self._derive_encryption_key(note_name)
                content = decrypt(ciphertext_bytes, key_bytes)
                self._encryption_key_cache.pop(note_name, None)

                enc_path = self.notes_manager.notes_dir / f"{note_name}.md.enc"
                self.notes_manager.save_note(note_name, content)
                self.cfg.mark_decrypted(note_name)

                if enc_path.exists():
                    best_effort_overwrite(enc_path)

                self.sidebar.set_row_encrypted(note_name, False)
            except Exception as e:
                logger.error("Failed to decrypt '%s': %s", note_name, e)

        self.refresh_list(self.sidebar.search_entry.get_text())
        self._show_toast(tr("Decrypted {n} note(s)").format(n=len(encrypted_notes)))

    def _on_rename_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Show a dialog to rename a folder."""
        old_folder = parameter.get_string()
        old_name = old_folder.split("/")[-1]

        dialog = Adw.MessageDialog(
            transient_for=self.props.active_window,
            heading=tr("Rename folder"),
            body=tr("Enter a new name for '{old_name}':").format(old_name=old_name),
        )
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("rename", tr("Rename"))
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")
        try:
            dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        except AttributeError:
            pass

        entry = Gtk.Entry()
        entry.set_text(old_name)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        extra.append(entry)
        dialog.set_extra_child(extra)

        def _on_response(d: Any, response: str) -> None:
            if response == "rename":
                new_name = entry.get_text().strip()
                err = self._validate_folder_name(new_name)
                if err:
                    entry.set_text(old_name)
                    self._show_toast(err)
                    return
                self._do_rename_folder(old_folder, new_name)

        dialog.connect("response", _on_response)
        dialog.present()

    def _validate_folder_name(self, name: str) -> str | None:
        """Validate a folder name. Return error message or None."""
        if not name or not name.strip():
            return tr("Name cannot be empty")
        if "/" in name:
            return tr("Folder name cannot contain '/'")
        try:
            self.notes_manager.validate_name(f"{name}/placeholder")
        except ValueError as e:
            return str(e)
        return None

    def _do_rename_folder(self, old_folder: str, new_name: str) -> None:
        """Rename a folder on disk and refresh."""
        old_path = self.notes_manager.notes_dir / old_folder
        parent = old_path.parent
        new_path = parent / new_name
        new_folder = str(new_path.relative_to(self.notes_manager.notes_dir))

        # Capture affected notes before the filesystem rename
        old_notes = self.notes_manager.get_notes_in_folder(old_folder)

        try:
            old_path.rename(new_path)

            # Migrate folder pin
            if self.cfg.is_folder_pinned(old_folder):
                self.cfg.pinned_folders.discard(old_folder)
                self.cfg.pinned_folders.add(new_folder)
                self.cfg._save_json(
                    self.cfg.pinned_folders_path, self.cfg.pinned_folders
                )

            self.cfg.set_folder_order(
                [new_folder if f == old_folder else f for f in self.cfg.folder_order]
            )

            # Git: stage renames and commit for each moved note
            if self.git_controller.is_available():
                for old_note in old_notes:
                    new_note = old_note.replace(old_folder, new_folder, 1)
                    self.git_controller.rename_note(old_note, new_note)
                    self.git_controller.auto_commit(new_note)

            # Migrate config + encryption key cache for moved notes
            for old_note in old_notes:
                new_note = old_note.replace(old_folder, new_folder, 1)
                self.cfg.rename_note_in_config(old_note, new_note)
                if old_note in self._encryption_key_cache:
                    self._encryption_key_cache[new_note] = (
                        self._encryption_key_cache.pop(old_note)
                    )

            # Update current_note if it's inside the renamed folder
            if self.current_note is not None and self.current_note.startswith(
                f"{old_folder}/"
            ):
                self.current_note = self.current_note.replace(old_folder, new_folder, 1)
                self.win.set_title(self.current_note.replace("/", " / "))

            # Migrate sidebar expand state
            if old_folder in self.sidebar._folder_expanded:
                self.sidebar._folder_expanded[new_folder] = (
                    self.sidebar._folder_expanded.pop(old_folder)
                )

            self.refresh_list(self.sidebar.search_entry.get_text())
            self._show_toast(
                tr("Folder renamed to '{new_name}'").format(new_name=new_name)
            )
        except OSError as e:
            self._show_toast(tr("Could not rename folder: {error}").format(error=e))

    def _on_delete_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Delete a folder and all notes inside it (with confirmation)."""
        folder = parameter.get_string()
        notes = self.notes_manager.get_notes_in_folder(folder)
        body = tr(
            "Delete folder '{folder}' and its {n} note(s)?\n"
            "This action cannot be undone."
        ).format(folder=folder, n=len(notes))
        dialog = confirm_destructive_dialog(
            transient_for=self.props.active_window,
            heading=tr("Delete folder '{folder}'?").format(folder=folder),
            body=body,
            confirm_label=tr("Delete"),
        )

        def _on_response(d: Any, response: str) -> None:
            if response == "delete":
                import shutil

                folder_path = self.notes_manager.notes_dir / folder
                if folder_path.exists():
                    try:
                        for note in notes:
                            self.cfg.remove_note(note)
                            self.notes_manager._content_cache.pop(note, None)
                            self.notes_manager._metadata_cache.pop(note, None)
                            self.notes_manager._mtime_cache.pop(note, None)
                        shutil.rmtree(folder_path)

                        # Commit deletions to git so re-creating a note
                        # at any path doesn't re-link old history.
                        if self.git_controller.is_available():
                            for note in notes:
                                self.git_controller.commit_deletion(
                                    note,
                                    enc=self.notes_manager.is_encrypted(note),
                                )

                        # Clean up stale folder pin
                        self.cfg.unpin_folder(folder)

                        # If the current note is in the deleted folder, cancel
                        # pending timers and navigate away so no auto-save can
                        # re-create the folder/file.
                        was_current = (
                            self.current_note is not None
                            and self.current_note.startswith(f"{folder}/")
                        )
                        if was_current:
                            for attr in (
                                "rename_timeout_id",
                                "sidebar_update_timeout_id",
                            ):
                                self._safe_source_remove(attr)
                            self.current_note = None
                            self._set_buffer_text("")
                            self.win.set_title(tr("Tokyo Notes"))

                        self.refresh_list(self.sidebar.search_entry.get_text())
                        self._show_toast(
                            tr("Deleted folder '{folder}'").format(folder=folder)
                        )

                        if was_current:
                            remaining = self.notes_manager.get_notes()
                            if remaining:
                                self.lifecycle.initial_load()
                            else:
                                self.lifecycle.on_new_note(None)
                    except OSError as e:
                        self._show_toast(
                            tr("Could not delete folder: {error}").format(error=e)
                        )

        dialog.connect("response", _on_response)
        dialog.present()

    def _on_move_note(self, action: Gio.SimpleAction, parameter: GLib.Variant) -> None:
        """Move a note to a different folder."""
        raw = parameter.get_string()
        note_name, dest_folder = raw.split("|", 1)

        stem = note_name.rsplit("/", 1)[-1]
        new_name = f"{dest_folder}/{stem}" if dest_folder else stem

        if new_name == note_name:
            return

        old_folder = note_name.rsplit("/", 1)[0] if "/" in note_name else ""

        if not self.notes_manager.rename_note(note_name, new_name):
            self._show_toast(tr("Could not move note"))
            return

        # Recreate the source folder directory if rename_note cleaned it up
        if old_folder:
            old_dir = self.notes_manager.notes_dir / old_folder
            if not old_dir.exists():
                old_dir.mkdir(parents=True, exist_ok=True)

        self.cfg.rename_note_in_config(note_name, new_name)

        if self.git_controller.is_available():
            self.git_controller.rename_note(note_name, new_name)
            self.git_controller.auto_commit(new_name)

        if self.current_note == note_name:
            self.current_note = new_name
            self.win.set_title(new_name.replace("/", " / "))

        self.refresh_list(self.sidebar.search_entry.get_text())
        self._select_sidebar_row(new_name)
        self._show_toast(tr("Moved to '{dest}'").format(dest=dest_folder or tr("Home")))

    def _on_move_folder(
        self, action: Gio.SimpleAction, parameter: GLib.Variant
    ) -> None:
        """Move an entire folder (and its contents) to a different parent."""
        raw = parameter.get_string()
        src_folder, dest_parent = raw.split("|", 1)

        folder_name = src_folder.rsplit("/", 1)[-1]
        new_folder = f"{dest_parent}/{folder_name}" if dest_parent else folder_name

        if new_folder == src_folder:
            return

        old_path = self.notes_manager.notes_dir / src_folder
        new_path = self.notes_manager.notes_dir / new_folder

        if new_path.exists():
            self._show_toast(tr("Target folder already exists"))
            return

        # Collect affected note names before the move
        old_notes = self.notes_manager.get_notes_in_folder(src_folder)

        new_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            old_path.rename(new_path)
        except OSError as e:
            self._show_toast(tr("Could not move folder: {error}").format(error=e))
            return

        # Migrate config sets
        if self.cfg.is_folder_pinned(src_folder):
            self.cfg.pinned_folders.discard(src_folder)
            self.cfg.pinned_folders.add(new_folder)
            self.cfg._save_json(self.cfg.pinned_folders_path, self.cfg.pinned_folders)

        self.cfg.set_folder_order(
            [new_folder if f == src_folder else f for f in self.cfg.folder_order]
        )

        # Migrate sidebar expand state
        if src_folder in self.sidebar._folder_expanded:
            self.sidebar._folder_expanded[new_folder] = (
                self.sidebar._folder_expanded.pop(src_folder)
            )

        # Git: stage renames and commit for each moved note
        if self.git_controller.is_available():
            for old_note in old_notes:
                new_note = old_note.replace(src_folder, new_folder, 1)
                self.git_controller.rename_note(old_note, new_note)
                self.git_controller.auto_commit(new_note)

        # Migrate config + encryption key cache for moved notes
        for old_note in old_notes:
            new_note = old_note.replace(src_folder, new_folder, 1)
            self.cfg.rename_note_in_config(old_note, new_note)
            if old_note in self._encryption_key_cache:
                self._encryption_key_cache[new_note] = self._encryption_key_cache.pop(
                    old_note
                )

        # Update current_note if it's inside the moved folder
        if self.current_note is not None and self.current_note.startswith(
            f"{src_folder}/"
        ):
            self.current_note = self.current_note.replace(src_folder, new_folder, 1)
            self.win.set_title(self.current_note.replace("/", " / "))

        self.refresh_list(self.sidebar.search_entry.get_text())
        self._show_toast(
            tr("Moved folder to '{new_folder}'").format(new_folder=new_folder)
        )

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

    # Save helpers

    def _safe_source_remove(self, attr: str) -> None:
        """Cancel a GLib source by attr name, without warning if already removed."""
        tid = getattr(self, attr)
        if tid > 0:
            setattr(self, attr, 0)
            GLib.source_remove(tid)

    def _flush_pending_save(self) -> None:
        """Write buffered changes to disk and cancel all pending timeouts."""
        for attr in (
            "rename_timeout_id",
            "sidebar_update_timeout_id",
            "highlight_timeout_id",
            "image_timeout_id",
            "search_timeout_id",
            "_pending_highlight_id",
        ):
            self._safe_source_remove(attr)

        se = getattr(self, "split_editor", None)
        if se is not None:
            se.flush_saves()
            return

        if self.current_note and self.current_note.startswith(".template:"):
            tmpl_slug = self.current_note.split(":", 1)[1]
            start, end = self.buffer.get_bounds()
            content = self.buffer.get_text(start, end, True)
            if content:
                current_path = self.template_manager.templates_dir / f"{tmpl_slug}.md"
                if (
                    current_path.exists()
                    and current_path.read_text(encoding="utf-8") == content
                ):
                    return
                if self.template_manager.is_builtin(tmpl_slug):
                    copy_slug = self.template_manager.reserve_copy_slug(tmpl_slug)
                    copy_path = self.template_manager.templates_dir / f"{copy_slug}.md"
                    copy_path.write_text(content, encoding="utf-8")
                    self.current_note = f".template:{copy_slug}"
                    self.nav.update_header_ui(f"Template: {copy_slug}", is_editor=True)
                    tmpl_slug = copy_slug
                self.template_manager.update_template(tmpl_slug, content)
            return

        if self.current_note:
            from core.services import save_note_content

            content = strip_anchors_for_save(self.buffer)
            if content:
                try:
                    save_note_content(
                        note_name=self.current_note,
                        content=content,
                        is_encrypted=self.notes_manager.is_encrypted(self.current_note),
                        derive_encryption_key=self._derive_encryption_key,
                        notes_manager=self.notes_manager,
                        session_password_bytes=self._session_password_bytes,
                    )
                except OSError as e:
                    logger.error("Critical save failure: %s", e)
                    self.show_export_dialog(
                        tr("Save Failed"),
                        tr(
                            "Could not save note '{note}'.\n\n"
                            "Reason: {reason}\n\n"
                            "Your changes are still in memory."
                        ).format(
                            note=self.current_note,
                            reason=e.strerror or str(e),
                        ),
                        is_error=True,
                    )

    def _reschedule(self, timeout_attr: str, delay_ms: int, callback: Callable) -> None:
        """Cancel any pending GLib timeout and schedule a fresh one."""
        self._safe_source_remove(timeout_attr)
        setattr(self, timeout_attr, GLib.timeout_add(delay_ms, callback))

    # Search

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
        editor_views = ("editor", "split_editor")
        if self.content_stack.get_visible_child_name() in editor_views:
            self.editor.show_find()
            return True
        entry = self.sidebar.search_entry
        if entry.has_focus() and entry.get_text():
            entry.set_text("")
            self.refresh_list()
        else:
            entry.grab_focus()
        return True

    def on_find_replace_shortcut(self) -> bool:
        editor_views = ("editor", "split_editor")
        if self.content_stack.get_visible_child_name() in editor_views:
            self.editor.show_replace()
        return True

    def on_sidebar_search_shortcut(self) -> bool:
        self.sidebar_toggle.set_active(True)
        self.sidebar.search_entry.grab_focus()
        return True

    def show_shortcuts_dialog(self) -> bool:
        """Show the keyboard shortcuts window (Ctrl+H)."""
        win = Gtk.Window(
            transient_for=self.win,
            modal=True,
            default_width=520,
            default_height=480,
        )
        win.set_title(tr("Keyboard shortcuts"))
        win.add_css_class("shortcuts-dialog")

        esc_ctrl = Gtk.EventControllerKey.new()
        esc_ctrl.connect(
            "key-pressed",
            lambda c, k, *a: win.close() or True if k == Gdk.KEY_Escape else None,
        )
        win.add_controller(esc_ctrl)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        def _group(title: str, shortcuts: list[tuple[str, str]]) -> Gtk.ShortcutsGroup:
            group = Gtk.ShortcutsGroup(title=title, visible=True)
            for accel, desc in shortcuts:
                item = Gtk.ShortcutsShortcut(
                    accelerator=accel, title=desc, visible=True
                )
                group.append(item)
            return group

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.append(
            _group(
                tr("Navigation"),
                [
                    ("<Primary>n", tr("New note")),
                    ("<Primary><Shift>n", tr("New from template")),
                    ("<Primary>d", tr("Dashboard")),
                    ("<Primary>g", tr("Knowledge graph")),
                    ("<Primary><Shift>f", tr("Search notes")),
                    ("<Primary>f", tr("Find in editor")),
                    ("<Primary>h", tr("Find and replace in editor")),
                    ("F1", tr("This shortcuts window")),
                    ("<Primary><Shift>s", tr("Settings")),
                    ("Escape", tr("Back to editor / clear search")),
                ],
            )
        )
        content.append(
            _group(
                tr("Notes"),
                [
                    ("Delete", tr("Delete selected note")),
                    ("<Primary><Shift>p", tr("Pin / unpin note")),
                    ("<Primary><Shift>a", tr("Archive / unarchive note")),
                    ("<Primary>l", tr("Lock private notes")),
                    ("<Primary>t", tr("Quick add task")),
                    ("<Primary><Shift>t", tr("Insert timestamp")),
                    ("<Primary><Shift>z", tr("Zen mode")),
                    ("<Primary>q", tr("Quit")),
                ],
            )
        )
        content.append(
            _group(
                tr("Editor"),
                [
                    ("F3", tr("Find next")),
                    ("<Shift>F3", tr("Find previous")),
                    ("bracketleft bracketleft", tr("Open note link picker  ( [[ )")),
                    ("at", tr("Open deadline picker  ( @ )")),
                    ("braceleft braceleft", tr("Open variable picker  ( {{ )")),
                    ("Return", tr("Continue list or task on new line")),
                    ("<Control>space", tr("Toggle dictation (if enabled)")),
                ],
            )
        )

        scrolled.set_child(content)
        win.set_child(scrolled)
        win.present()
        return True

    # Dialogs

    def show_export_dialog(self, title: str, body: str, is_error: bool = False) -> None:
        # Use AlertDialog (Adw >= 1.5) when available, else MessageDialog.
        # Errors use default (neutral) button appearance — destructive style
        # is reserved for actions that destroy data, not for error messages.
        try:
            dialog = Adw.AlertDialog(heading=title, body=body)
            dialog.add_response("ok", tr("OK"))
            dialog.present(self.win)
        except AttributeError:
            dialog = Adw.MessageDialog(transient_for=self.win, heading=title, body=body)
            dialog.add_response("ok", tr("OK"))
            dialog.present()

    # Cursor / click

    def on_cursor_moved(self, buffer: Gtk.TextBuffer, _pspec: object) -> None:
        if (
            not self.highlighter
            or self.is_loading
            or self.content_stack.get_visible_child_name()
            not in ("editor", "split_editor")
        ):
            return
        if (
            self.current_note
            and not self.current_note.startswith(".template:")
            and self.notes_manager.is_encrypted(self.current_note)
        ):
            self._reset_lock_timer()
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        if cursor_line == self.last_cursor_line:
            return
        # If a delayed highlight was just scheduled by on_text_changed,
        # skip the synchronous highlight pass so fast typing stays responsive.
        # The debounced do_delayed_highlight will catch up after 100ms.
        if self.highlight_timeout_id:
            self.last_cursor_line = cursor_line
            return
        # While a selection is active, skip per-line marker passes entirely.
        # Those passes would reapply the invisible tag to heading markers mid-drag,
        # putting Pango and the btree out of sync and causing the byte-index crash.
        # _on_mark_set will do a full highlight restore once selection clears.
        if not self._has_selection and not buffer.get_has_selection():
            self.highlighter.toggle_cursor_markers(
                prev_line=self.last_cursor_line,
                curr_line=cursor_line,
            )
        self.last_cursor_line = cursor_line

    def _on_mark_set(self, buffer: Gtk.TextBuffer, _loc, mark) -> None:
        """Toggle invisible marker tags based on whether a selection is active.

        GTK crashes with 'byte index off the end of the line' when invisible-tagged
        text is present during mouse selection, because the Pango layout byte indices
        diverge from the TextBuffer byte indices for invisible spans. We work around
        this by stripping the invisible tag for the duration of any active selection
        and restoring it (via a full highlight pass) the moment the selection clears.
        """
        if not self.highlighter:
            return

        # Only act on the selection_bound mark; ignore insert and named marks.
        if mark.get_name() not in ("selection_bound", "insert"):
            return

        has_sel = buffer.get_has_selection()
        if has_sel == self._has_selection:
            return  # State unchanged — nothing to do.

        self._has_selection = has_sel

        if has_sel:
            # Selection just started: remove invisible tags so Pango and the
            # btree stay in sync while the user drags. We do this atomically
            # to avoid a visible redraw of the intermediate state.
            buffer.begin_irreversible_action()
            try:
                start, end = buffer.get_bounds()
                buffer.remove_tag_by_name("invisible", start, end)
            finally:
                buffer.end_irreversible_action()
        else:
            # Selection cleared: restore the full render (invisible markers back).
            self._do_highlight()

    def on_click_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        self._reset_lock_timer_on_activity()
        self.click_dispatcher.handle_click(x, y, gesture)

    def _on_editor_scroll(
        self, controller: Gtk.EventControllerScroll, dx: float, dy: float
    ) -> None:
        self._reset_lock_timer_on_activity()

    def _reset_lock_timer_on_activity(self) -> None:
        if not self._is_session_locked:
            self._reset_lock_timer()

    # Highlighting

    def update_highlighting(self, immediate: bool = False) -> None:
        if immediate:
            self._do_highlight()
        else:
            GLib.idle_add(self._do_highlight)

    def _do_highlight(self) -> bool:
        if not self.highlighter or self.content_stack.get_visible_child_name() not in (
            "editor",
            "split_editor",
        ):
            return False
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        self.highlighter.highlight(cursor_line=cursor_line)
        self.last_cursor_line = cursor_line
        self._apply_search_highlights()
        return False

    def do_delayed_highlight(self) -> bool:
        """Re-highlight only the current line and neighbours (incremental)."""
        self.highlight_timeout_id = 0
        if not self.highlighter or self.content_stack.get_visible_child_name() not in (
            "editor",
            "split_editor",
        ):
            return False
        if not self._full_pass_complete:
            return False
        if self.editor._image_update_running:
            return False
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        # Re-highlight current line ±1 to catch setext / list continuation effects.
        total = self.buffer.get_line_count()
        start_line = max(0, cursor_line - 1)
        end_line = min(total - 1, cursor_line + 1)
        self.buffer.handler_block(self.changed_handler_id)
        try:
            self.highlighter.highlight_line_range(
                start_line, end_line, cursor_line=cursor_line
            )
        finally:
            self.buffer.handler_unblock(self.changed_handler_id)
        self.last_cursor_line = cursor_line
        self._apply_search_highlights(full_reset=False)
        fb = self.editor.find_bar
        if fb._visible and fb._find_results:
            fb._apply_highlights()
        return False

    def do_delayed_images(self) -> bool:
        self.image_timeout_id = 0
        if self._has_images:
            self.editor.update_images(
                Path(self.notes_manager.notes_dir).resolve(),
                done_callback=lambda: (
                    self._scroll_to_cursor(),
                    self.update_highlighting(immediate=False),
                ),
            )
        return False

    # Dashboard callbacks

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
            if not self.dashboard_view.update_checkbox(cb["note"], cb["line"], checked):
                self.nav.refresh_dashboard(self.dashboard_view.active_filter)

    def _sync_checkbox_in_buffer(self, line_num: int, checked: bool) -> None:
        """Patch a checkbox line in the editor buffer to match *checked*.

        Called after update_checkbox writes the new state to disk so that the
        buffer stays in sync without triggering the debounced save again.
        """
        try:
            result = self.buffer.get_iter_at_line(line_num - 1)
            line_start = result[1] if isinstance(result, tuple) else result
        except (TypeError, IndexError):
            return
        line_end = line_start.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()
        line_text = self.buffer.get_text(line_start, line_end, False)
        new_text = CB_ANY_RE.sub(
            "[x]" if checked else "[ ]",
            line_text,
            count=1,
        )
        if new_text == line_text:
            return  # nothing to patch
        self.buffer.handler_block(self.changed_handler_id)
        try:
            self.buffer.delete(line_start, line_end)
            # Re-fetch iter after delete (line_start is now invalid)
            result = self.buffer.get_iter_at_line(line_num - 1)
            insert_iter = result[1] if isinstance(result, tuple) else result
        except (TypeError, IndexError):
            self.buffer.handler_unblock(self.changed_handler_id)
            return
        self.buffer.insert(insert_iter, new_text)
        self.buffer.handler_unblock(self.changed_handler_id)

    # Quick Add

    def on_quick_add_task(
        self, text: str, note_name: str, deadline: str | None
    ) -> None:
        """Add a task to *note_name*. Creates the note if it doesn't exist."""
        notes = self.notes_manager.get_notes()

        if note_name not in notes:
            self.notes_manager.save_note(note_name, f"# {note_name}\n")
            self.refresh_list()

        if self.notes_manager.is_encrypted(note_name):
            self._show_toast(
                tr("Cannot add to encrypted note '{note_name}'").format(
                    note_name=note_name
                )
            )
            return

        if note_name in self.cfg.archived:
            self.cfg.toggle_archive(note_name)

        content = self.notes_manager.read_plain(note_name) or ""
        if content and not content.endswith("\n"):
            content += "\n"
        line = f"- [ ] {text}"
        if deadline:
            line += f" @{deadline}"
        self.notes_manager.save_note(note_name, content + line + "\n")

        if self.current_note == note_name and not self.current_note.startswith(
            ".template:"
        ):
            self._set_buffer_text(self.notes_manager.read_plain(note_name))
            if self.highlighter:
                self.highlighter.highlight(start_line=0, end_line=30)
            self._full_pass_complete = False

        if self.dashboard_view is not None:
            self.nav.refresh_dashboard(self.dashboard_view.active_filter)

        self._show_toast(tr("Task added to {note_name}").format(note_name=note_name))

    def _on_quick_add_shortcut(self) -> bool:
        """Ctrl+T — open Quick Add popover from any view."""
        if self.dashboard_view is None:
            self.nav.on_dashboard_clicked()
        else:
            self.content_stack.set_visible_child_name("dashboard")
            self.nav.update_header_ui("Dashboard", is_editor=False)
            self.sidebar.set_active_view("dashboard")
            self._set_backlinks_visible(False)
        GLib.idle_add(self.dashboard_view.open_quick_add_popover)
        return True

    def _on_speech_toggle(self) -> bool:
        """Ctrl+Space — toggle speech-to-text dictation."""
        if not self.cfg.get("speech_enabled", False):
            return False
        btn = getattr(self, "_speech_btn", None)
        if btn is not None:
            btn.set_active(not btn.get_active())
        return True

    def _on_speech_recording(self, active: bool) -> None:
        """Show/dismiss a 'Recording…' toast."""
        if not hasattr(self, "toast_overlay"):
            return
        if active:
            toast = Adw.Toast(title=tr("Recording…"), timeout=0)
            self.toast_overlay.add_toast(toast)
            self._recording_toast = toast
        else:
            toast = getattr(self, "_recording_toast", None)
            if toast is not None:
                toast.dismiss()
                self._recording_toast = None

    def _on_speech_transcribing(self, active: bool) -> None:
        """Show/dismiss a 'Transcribing…' toast."""
        if not hasattr(self, "toast_overlay"):
            return
        if active:
            toast = Adw.Toast(title=tr("Transcribing…"), timeout=0)
            self.toast_overlay.add_toast(toast)
            self._transcribing_toast = toast
        else:
            toast = getattr(self, "_transcribing_toast", None)
            if toast is not None:
                toast.dismiss()
                self._transcribing_toast = None

    def _on_speech_quiet_audio(self) -> None:
        if not hasattr(self, "toast_overlay"):
            return
        toast = Adw.Toast(
            title=tr(
                "Audio too quiet. Check microphone or change input device in Settings."
            ),
        )
        self.toast_overlay.add_toast(toast)

    def _provision_speech_and_download(self) -> None:
        """Provision the speech venv (Linux) or skip (macOS dictation build),
        then download the model if needed."""
        import sys

        from core.speech_paths import is_available_for_build

        # macOS dictation build: deps are bundled, no provisioning needed.
        if sys.platform == "darwin" and is_available_for_build():
            self._download_speech_model()
            return

        from core.speech_setup import provision, venv_valid

        venv_ok = venv_valid()

        if not venv_ok:
            dialog = Adw.MessageDialog(
                transient_for=self.win,
                heading=tr("Setting Up Dictation"),
                body=tr("Creating speech environment (~150 MB download)…"),
            )
            dialog.add_response("cancel", tr("Cancel"))
            dialog.set_close_response("cancel")
            dialog.present()

            def on_stdout(line: str) -> None:
                GLib.idle_add(lambda: dialog.set_body(tr("Setting up: %s") % line[:60]))

            def do_provision():
                try:
                    provision(on_stdout=on_stdout)
                    GLib.idle_add(lambda: self._on_venv_provisioned(dialog))
                except Exception as e:
                    logger.error("Venv provision failed: %s", e)
                    GLib.idle_add(lambda: dialog.close())

            dialog.connect(
                "response", lambda d, r: d.close() if r == "cancel" else None
            )
            Thread(target=do_provision, daemon=True).start()
        else:
            self._download_speech_model()

    def _on_venv_provisioned(self, dialog: Adw.MessageDialog) -> None:
        dialog.close()
        self._show_toast(tr("Dictation venv ready"))
        self._download_speech_model()

    def _download_speech_model(self) -> None:
        """Download the speech model with a progress dialog."""
        from core.speech import download_model_with_progress

        if model_cached():
            return

        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading=tr("Downloading Speech Model"),
            body=tr("Downloading speech recognition model (~145 MB)…"),
        )
        dialog.add_response("cancel", tr("Cancel"))
        dialog.set_close_response("cancel")
        dialog.present()

        def on_progress(current: int, total: int) -> None:
            GLib.idle_add(
                lambda: dialog.set_body(
                    tr("Downloading speech model: %d / %d MB")
                    % (current // (1024 * 1024), total // (1024 * 1024))
                )
            )

        def download():
            try:
                download_model_with_progress(on_progress=on_progress)
                GLib.idle_add(lambda: self._on_speech_model_downloaded(dialog))
            except Exception as e:
                logger.error("Speech model download failed: %s", e)
                GLib.idle_add(lambda: dialog.close())

        dialog.connect(
            "response",
            lambda d, r: d.close() if r == "cancel" else None,
        )
        Thread(target=download, daemon=True).start()

    def _on_speech_model_downloaded(self, dialog: Adw.MessageDialog) -> None:
        dialog.close()
        self._show_toast(tr("Dictation ready"))

    # Deadline picker

    def handle_deadline_click(
        self,
        x: float,
        y: float,
        note_name: str | None = None,
        line_num: int | None = None,
        widget: Gtk.Widget | None = None,
    ) -> None:
        picker = DeadlinePicker(
            lambda deadline: self._apply_deadline_update(note_name, line_num, deadline),
            has_deadline=True,
        )
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        picker.set_parent(self.text_view)
        picker.set_pointing_to(rect)
        picker.popup()

    def handle_snooze(
        self, note_name: str, line_num: int, deadline: str | None
    ) -> None:
        """Snooze a task by updating its deadline."""
        self._apply_deadline_update(note_name, line_num, deadline)

    def _apply_deadline_update(
        self,
        note_name: str | None,
        line_num: int | None,
        deadline: str | None,
    ) -> None:
        if not note_name or not line_num:
            return
        self.notes_manager.update_deadline(note_name, line_num, deadline)
        if self.dashboard_view is not None:
            self.nav.refresh_dashboard(self.dashboard_view.active_filter)
        self.refresh_list(self.sidebar.search_entry.get_text())
        if self.current_note == note_name:
            # Re-read just the updated line from cache (no full disk read).
            content = self.notes_manager.read_plain(note_name)
            lines = content.split("\n")
            if 0 < line_num <= len(lines):
                self._update_deadline_line_in_buffer(line_num, lines[line_num - 1])

    def _update_deadline_line_in_buffer(self, line_num: int, new_line: str) -> None:
        """Replace the deadline line in the editor buffer in-place."""
        try:
            result = self.buffer.get_iter_at_line(line_num - 1)
            start_iter = result[1] if isinstance(result, tuple) else result
        except (TypeError, IndexError):
            return
        end_iter = start_iter.copy()
        if not end_iter.ends_line():
            end_iter.forward_to_line_end()
        self.buffer.handler_block(self.changed_handler_id)
        try:
            self.buffer.delete(start_iter, end_iter)
            self.buffer.insert(start_iter, new_line)
        finally:
            self.buffer.handler_unblock(self.changed_handler_id)
        self.update_highlighting()


def main() -> int:
    """Run the Tokyo Notes application and return a process exit code."""
    from core.logging_setup import configure_logging

    configure_logging()

    lock = InstanceLock()
    if not lock.acquire():
        # Another instance is already running — print a clear message and exit.
        # We avoid spinning up a second GTK application loop here since the
        # overhead outweighs the benefit of a GUI dialog that the user may
        # not even see (e.g. when launched from a script or file manager).
        print(
            "Tokyo Notes is already running. Check your taskbar or system tray.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        app = TokyoNotes()
        exit_code = app.run(sys.argv)
    except KeyboardInterrupt:
        print("\nTokyo Notes: interrupted by user.", file=sys.stderr)
        exit_code = 130
    finally:
        lock.release()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
