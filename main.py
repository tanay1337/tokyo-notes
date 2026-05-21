"""Tokyo Notes — main application entry point."""
import logging
import sys
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
from core.template_manager import TemplateManager
from core.theme_manager import ThemeManager
from core.window_manager import WindowManager
from ui.click_dispatcher import ClickDispatcher
from ui.deadline_picker import DeadlinePicker
from ui.editor import Editor
from ui.sakura_overlay import SakuraOverlay
from ui.sidebar import Sidebar
from core.utils import CB_ANY_RE
from ui.toolbar import build_toolbar

logger = logging.getLogger(__name__)


class TokyoNotes(Adw.Application):
    def __init__(self, **kwargs) -> None:
        super().__init__(application_id="com.example.TokyoNotes", **kwargs)
        self.base_dir = Path(__file__).parent

        # Services (explicit, testable)
        self.cfg = ConfigManager()
        self.notes_folder: str = self.cfg.get("notes_folder")
        self.notes_manager = NotesManager(notes_dir=self.notes_folder)

        # Subsystem managers — order matters for startup
        self.window_manager = WindowManager(self)
        self.theme_manager = ThemeManager(self)
        self.click_dispatcher = ClickDispatcher(self)
        self.actions = ActionsHandler(self)
        self.nav = NavigationController(self)
        self.lifecycle = NoteLifecycleManager(self)
        self.search = SearchController(self.refresh_list)
        self.template_manager = TemplateManager(self)

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
        self._pending_highlight_id: int = 0
        self._has_images: bool = False
        self.split_view: object = None  # set in do_activate

        # Session state for private notes
        self._session_key: bytearray | None = None
        self._session_password_bytes: bytearray | None = None
        self._is_session_locked: bool = any(
            self.notes_manager.is_encrypted(n) for n in self.notes_manager.get_notes()
        )
        self._lock_timer_id: int = 0
        self._wrong_unlock_attempts: int = 0
        self._pending_encrypt_note: str | None = None
        self._unlock_cooldown_id: int = 0
        self._unlock_cooldown_remaining: int = 0

        # Sync encrypted.json with actual .md.enc files on disk
        self._sync_encrypted_config()

        install_crash_handler(self)
        self._setup_actions()

    # App lifecycle

    def do_shutdown(self) -> None:
        """Flush any pending config writes and note saves before the process exits."""
        self._cancel_lock_timer()
        self._flush_pending_save()
        if self.current_note and self.notes_manager.is_encrypted(self.current_note):
            self.buffer.set_text("")
        self._zero_session_key()
        self._zero_session_password()
        self.cfg.flush_immediate()
        logger.info("Tokyo Notes shutting down")
        Adw.Application.do_shutdown(self)

    def _apply_security_mitigations(self) -> None:
        """Prevent core dumps and attempt to lock process memory."""
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except Exception as e:
            logger.warning("Could not disable core dumps: %s", e)

        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            result = libc.mlockall(1 | 2)
            if result != 0:
                errno = ctypes.get_errno()
                logger.warning("mlockall failed (errno=%d) — continuing anyway", errno)
        except Exception as e:
            logger.warning("Could not lock memory pages: %s", e)

    # Session management for private notes

    def unlock_session(self, password: str) -> None:
        """Unlock private notes with the given password.

        Verification happens by deriving a key using the per-file salt from
        the first encrypted note and attempting to decrypt it.
        The password is stored in memory so keys can be derived per-note.
        """
        from core.encryption import derive_key_from_file, decrypt

        encrypted_notes = [
            n for n in self.notes_manager.get_notes()
            if self.notes_manager.is_encrypted(n)
        ]
        if not encrypted_notes:
            self._show_toast("No private notes to unlock")
            return

        first_note = encrypted_notes[0]
        try:
            raw = self.notes_manager.read_note(first_note)
            ciphertext_bytes = raw.encode("latin-1")
            key = derive_key_from_file(password, ciphertext_bytes)
            decrypt(ciphertext_bytes, bytearray(key))
        except Exception as e:
            logger.warning("Wrong password: %s", e)
            self._wrong_unlock_attempts += 1
            if self._wrong_unlock_attempts >= 3:
                self._start_unlock_cooldown()
            self._show_toast("Wrong password")
            return

        self._session_password_bytes = bytearray(password.encode("utf-8"))
        self._is_session_locked = False
        self._wrong_unlock_attempts = 0
        self._cancel_unlock_cooldown()
        self._update_sidebar_lock_state()
        self._reset_lock_timer()
        self._show_toast("Private notes unlocked")
        self.editor.set_editable(True)
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
                logger.error("Failed to encrypt pending note '%s' after unlock: %s", note_name, e)

    def _show_unlock_popover(self) -> None:
        """Show the unlock dialog."""
        from ui.unlock_popover import UnlockDialog
        dialog = UnlockDialog(self)
        dialog.present()

    def lock_session(self) -> None:
        """Lock private notes, zero the key and password, clear the buffer."""
        self._cancel_lock_timer()
        if self.current_note and self.notes_manager.is_encrypted(self.current_note):
            try:
                self._save_current_encrypted_note()
            except Exception as e:
                logger.error("Failed to save encrypted note on lock: %s", e)
            self.buffer.set_text("")
        self.editor.set_editable(False)
        self._zero_session_key()
        self._zero_session_password()
        self._is_session_locked = True
        self._update_sidebar_lock_state()
        self._show_toast("Private notes locked", action_label="Unlock", action=self._show_unlock_popover)

    def _zero_session_key(self) -> None:
        """Zero out the session key bytearray before releasing it."""
        if self._session_key is not None:
            for i in range(len(self._session_key)):
                self._session_key[i] = 0
            self._session_key = None

    def _zero_session_password(self) -> None:
        """Zero out the session password bytearray before releasing it."""
        if self._session_password_bytes is not None:
            for i in range(len(self._session_password_bytes)):
                self._session_password_bytes[i] = 0
            self._session_password_bytes = None

    def _cancel_lock_timer(self) -> None:
        if self._lock_timer_id:
            GLib.source_remove(self._lock_timer_id)
            self._lock_timer_id = 0

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
        actual_encrypted = {
            Path(p.name).stem
            for p in self.notes_manager.notes_dir.glob("*.md.enc")
        }
        stale = self.cfg.encrypted - actual_encrypted
        missing = actual_encrypted - self.cfg.encrypted
        if stale:
            for name in stale:
                self.cfg.encrypted.discard(name)
            self.cfg._save_json(self.cfg.encrypted_path, self.cfg.encrypted)
        if missing:
            for name in missing:
                self.cfg.encrypted.add(name)
            self.cfg._save_json(self.cfg.encrypted_path, self.cfg.encrypted)

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
            GLib.source_remove(self._unlock_cooldown_id)
            self._unlock_cooldown_id = 0
            self._unlock_cooldown_remaining = 0

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

    def _save_current_encrypted_note(self) -> None:
        """Encrypt and save the current editor buffer content."""
        if not self.current_note or self._session_password_bytes is None:
            return
        start, end = self.buffer.get_bounds()
        plaintext = self.buffer.get_text(start, end, True)
        from core.encryption import derive_key_from_file, encrypt, _SALT_LEN
        raw = self.notes_manager.read_note(self.current_note)
        ciphertext_bytes = raw.encode("latin-1")
        file_salt = ciphertext_bytes[:_SALT_LEN]
        password = self._session_password_bytes.decode("utf-8")
        key = derive_key_from_file(password, ciphertext_bytes)
        key_bytes = bytearray(key)
        ciphertext = encrypt(plaintext, key_bytes, file_salt)
        self.notes_manager.save_note(self.current_note, ciphertext.decode("latin-1"), encrypt=True)
        self.cfg.mark_encrypted(self.current_note)

    def _encrypt_note(self, note_name: str) -> None:
        """Encrypt an existing plain note using the stored session password."""
        if self._session_password_bytes is None:
            return
        content = self.notes_manager.read_note(note_name)
        from core.encryption import encrypt, secure_delete, derive_key
        import os
        from core.encryption import _SALT_LEN
        salt = os.urandom(_SALT_LEN)
        password = self._session_password_bytes.decode("utf-8")
        key = derive_key(password, salt)
        key_bytes = bytearray(key)
        ciphertext = encrypt(content, key_bytes, salt)
        plain_path = self.notes_manager.notes_dir / f"{note_name}.md"
        self.notes_manager.save_note(note_name, ciphertext.decode("latin-1"), encrypt=True)
        self.cfg.mark_encrypted(note_name)
        if plain_path.exists():
            secure_delete(plain_path)
        self.notes_manager._content_cache.pop(note_name, None)
        self.notes_manager._metadata_cache.pop(note_name, None)
        self.refresh_list()
        if self.current_note == note_name:
            self.buffer.handler_block(self.changed_handler_id)
            self.buffer.set_text(content)
            self.buffer.handler_unblock(self.changed_handler_id)
        self._show_toast(f"'{note_name}' is now private")

    def _load_encrypted_note(self, note_name: str) -> None:
        """Decrypt and load an encrypted note into the editor."""
        if self._session_password_bytes is None:
            return
        try:
            raw = self.notes_manager.read_note(note_name)
            ciphertext = raw.encode("latin-1")
            from core.encryption import derive_key_from_file, decrypt
            password = self._session_password_bytes.decode("utf-8")
            key = derive_key_from_file(password, ciphertext)
            plaintext = decrypt(ciphertext, bytearray(key))
            self.buffer.handler_block(self.changed_handler_id)
            self.buffer.set_text(plaintext)
            self.buffer.handler_unblock(self.changed_handler_id)

            start = self.buffer.get_start_iter()
            self.buffer.place_cursor(start)
            self.text_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)

            if self.highlighter:
                self.highlighter.highlight(start_line=0, end_line=30)
            if self._pending_highlight_id:
                GLib.source_remove(self._pending_highlight_id)
                self._pending_highlight_id = 0
            if self.highlighter and self.buffer.get_line_count() > 30:
                self._pending_highlight_id = GLib.idle_add(
                    self.lifecycle._highlight_chunk, note_name, 30
                )
        except Exception as e:
            logger.error("Failed to decrypt note '%s': %s", note_name, e)
            self._show_toast(f"Failed to decrypt '{note_name}'")
            self.buffer.handler_block(self.changed_handler_id)
            self.buffer.set_text("")
            self.buffer.handler_unblock(self.changed_handler_id)

    def _show_toast(self, message: str, action_label: str | None = None, action=None) -> None:
        """Show an Adw.Toast with optional action button."""
        toast = Adw.Toast(title=message, timeout=3)
        if action_label and action:
            toast.set_button_label(action_label)
            toast.connect("button-clicked", lambda *_: action())
        if hasattr(self, "toast_overlay"):
            self.toast_overlay.add_toast(toast)

    # Template actions

    def _on_new_from_template_global(self, action=None, parameter=None) -> None:
        """Ctrl+Shift+N: open template picker to create a new note from template."""
        self._show_template_picker_for_new_note()

    def _on_new_from_template_action(self, action, parameter) -> None:
        """Menu action: open template picker to create a new note from template."""
        self._show_template_picker_for_new_note()

    def _on_new_from_template(self, btn: Gtk.Button | None = None) -> None:
        """Sidebar button: open template picker to create a new note from template."""
        self._show_template_picker_for_new_note()

    def _show_template_picker_for_new_note(self) -> None:
        """Show the template picker for creating a new note."""
        from ui.template_picker import TemplatePicker

        def on_selected(slug: str) -> None:
            content = self.template_manager.get_template_content(slug)
            if content is None:
                return
            from core.template_manager import TemplateManager
            substituted = TemplateManager.substitute_variables(content)
            self.lifecycle.on_new_note(None)
            self.buffer.handler_block(self.changed_handler_id)
            self.buffer.set_text(substituted)
            self.buffer.handler_unblock(self.changed_handler_id)
            start = self.buffer.get_start_iter()
            self.buffer.place_cursor(start)
            self.text_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)
            if self.highlighter:
                self.highlighter.highlight(start_line=0, end_line=30)
            self.text_view.grab_focus()

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
        content = self.notes_manager.read_note(note_name)
        self._show_save_template_dialog(note_name, content)

    def _show_save_template_dialog(self, note_name: str, content: str) -> None:
        """Show a dialog to name and save a template."""
        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading="Save as Template",
            body=f"Enter a name for the template (based on '{note_name}'):",
        )
        entry = Gtk.Entry()
        entry.set_text(note_name)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        try:
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        except Exception:
            pass
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
            self._show_toast("Template name cannot be empty")
            return
        slug = self.template_manager.save_as_template(name, content)
        self._show_toast(f"Template '{slug}' saved")

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
        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.set_text(content)
        self.buffer.handler_unblock(self.changed_handler_id)
        start = self.buffer.get_start_iter()
        self.buffer.place_cursor(start)
        self.text_view.scroll_to_iter(start, 0.0, False, 0.0, 0.0)
        self.content_stack.set_visible_child_name("editor")
        if self.highlighter:
            self.highlighter.highlight(start_line=0, end_line=30)
        self.text_view.grab_focus()

    def _on_delete_template(self, slug: str) -> None:
        """Delete a template by slug."""
        self.template_manager.delete_template(slug)
        self._show_toast(f"Template '{slug}' deleted")

    def _on_open_templates_folder(self) -> None:
        """Open the templates folder in the file manager."""
        import subprocess
        import shutil
        templates_dir = str(self.template_manager.templates_dir)
        opener = "xdg-open"
        if shutil.which("xdg-open") is None:
            opener = "open" if shutil.which("open") else None
        if opener:
            subprocess.Popen([opener, templates_dir])
        else:
            self._show_toast(f"Templates folder: {templates_dir}")

    # GIO actions

    def _setup_actions(self) -> None:
        for name, handler in (
            ("delete",        self.lifecycle.on_delete_action),
            ("pin",           self.on_pin_note),
            ("unpin",         self.on_unpin_note),
            ("archive",       self.on_toggle_archive_note),
            ("make_private",  self.on_make_private),
            ("remove_privacy", self.on_remove_privacy),
            ("save_as_template", self._on_save_as_template_action),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", handler)
            self.add_action(action)

        for name, handler in (
            ("new_note",      self.lifecycle.on_new_note_global),
            ("new_from_template", self._on_new_from_template_global),
        ):
            action = Gio.SimpleAction.new(name, None)
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
                self.notes_manager.is_encrypted(n) for n in self.notes_manager.get_notes()
            )
            if not has_any_encrypted:
                self._is_session_locked = False
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
        dialog.present()

    def _show_password_change_dialog(self) -> None:
        from ui.password_change_dialog import PasswordChangeDialog
        dialog = PasswordChangeDialog(self)
        dialog.present()

    def _confirm_remove_privacy(self, note_name: str) -> None:
        if self._is_session_locked or self._session_password_bytes is None:
            self._select_sidebar_row(note_name)
            self._show_unlock_popover()
            return

        dialog = Adw.MessageDialog(
            transient_for=self.win,
            heading="Remove Privacy?",
            body=(
                f"This will save '{note_name}' as plain text. "
                "The note will no longer be encrypted. Are you sure?"
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove Privacy")
        try:
            dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        except Exception:
            pass
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_privacy_response, note_name)
        dialog.present()

    def _on_remove_privacy_response(
        self, dialog: Adw.MessageDialog, response: str, note_name: str
    ) -> None:
        if response != "remove":
            return
        if self._session_password_bytes is None:
            return

        # Read the actual note content from disk (decrypt it), not the buffer
        raw = self.notes_manager.read_note(note_name)
        from core.encryption import derive_key_from_file, decrypt
        ciphertext_bytes = raw.encode("latin-1")
        password = self._session_password_bytes.decode("utf-8")
        key = derive_key_from_file(password, ciphertext_bytes)
        content = decrypt(ciphertext_bytes, bytearray(key))

        plain_path = self.notes_manager.notes_dir / f"{note_name}.md"
        enc_path = self.notes_manager.notes_dir / f"{note_name}.md.enc"

        self.notes_manager.save_note(note_name, content)
        self.cfg.mark_decrypted(note_name)

        if enc_path.exists():
            from core.encryption import secure_delete
            secure_delete(enc_path)

        self.refresh_list()
        if self.current_note == note_name:
            self.buffer.handler_block(self.changed_handler_id)
            self.buffer.set_text(content)
            self.buffer.handler_unblock(self.changed_handler_id)

    # Folder selection

    def on_select_folder(self, _button=None) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Notes Folder")
        # Use home directory as initial folder to avoid GTK4 bug where
        # selecting a parent of the initial folder fails silently.
        dialog.set_initial_folder(
            Gio.File.new_for_path(str(Path.home()))
        )
        dialog.select_folder(self.win, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error as e:
            logger.warning("Folder selection failed or cancelled: %s", e)
            self._show_toast("Folder selection cancelled")
            return

        if not folder:
            return

        new_folder = folder.get_path()
        logger.info("Selected folder: %s (current: %s)", new_folder, self.notes_folder)
        if new_folder == self.notes_folder:
            self._show_toast("Already using this folder")
            return

        self.notes_folder = new_folder
        self.cfg.set("notes_folder", new_folder)
        self.notes_manager = NotesManager(notes_dir=new_folder)

        if self.settings_view:
            self.settings_view.update_folder_path(new_folder)

        self.current_note = None
        self._has_images = False
        self.buffer.handler_block(self.changed_handler_id)
        self.buffer.set_text("")
        self.buffer.handler_unblock(self.changed_handler_id)
        self.win.set_title("Tokyo Notes")
        self.refresh_list()
        self._show_toast("Notes folder changed")

    # Activation / window construction

    def do_activate(self) -> None:
        # If the window already exists (second activation via D-Bus / instance
        # check), just raise it rather than building a second window.
        if hasattr(self, "win") and self.win:
            self.win.present()
            return

        self._apply_security_mitigations()

        self.theme_manager.setup_providers()
        self.win = self.window_manager.create_window()
        self.apply_theme(self.cfg.get("theme"))

        self.split_view = Adw.OverlaySplitView()

        # Build the toggle button before the sidebar so _build_content_header
        # can pack it; connect its "toggled" signal after the sidebar exists.
        # Ordering matters: sidebar_toggle → sidebar → content_header → editor area.
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.set_active(self.cfg.get("show_sidebar"))

        self.sidebar = Sidebar(
            self,
            self.lifecycle.on_new_note,
            self._on_new_from_template,
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

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.split_view)
        self.win.set_content(self.toast_overlay)

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
            on_lock=self.lock_session,
            on_new_from_template=self._on_new_from_template_global,
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

        scroll_ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.connect("scroll", self._on_editor_scroll)
        scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        self.text_view.add_controller(scroll_ctrl)

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
        icon = Gtk.Image.new_from_file(str(self.base_dir / "assets" / "toolbar" / "link.svg"))
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
        self.cfg.set(key, value)
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
            self.backlinks_btn.set_tooltip_text(f"{len(backlinks)} backlink(s)")
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
            if "light" in theme_name:
                self.win.add_css_class("light-theme")
                self.win.remove_css_class("dark-theme")
            else:
                self.win.add_css_class("dark-theme")
                self.win.remove_css_class("light-theme")

    # Formatting

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

    # Note list / sidebar

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
        if note_name in self.cfg.pinned:
            menu.append("Unpin", f"app.unpin::{note_name}")
        else:
            menu.append("Pin", f"app.pin::{note_name}")
        menu.append(
            "Unarchive" if is_archived else "Archive",
            f"app.archive::{note_name}",
        )

        template_section = Gio.Menu()
        template_section.append("Save as Template", f"app.save_as_template::{note_name}")
        menu.append_section(None, template_section)

        privacy_section = Gio.Menu()
        if self.notes_manager.is_encrypted(note_name):
            privacy_section.append("Remove privacy", f"app.remove_privacy::{note_name}")
        else:
            privacy_section.append("Make private", f"app.make_private::{note_name}")
        menu.append_section(None, privacy_section)

        danger = Gio.Menu()
        danger.append("Delete", f"app.delete::{note_name}")
        menu.append_section(None, danger)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        row_rect = Gdk.Rectangle()
        row_rect.x      = 0
        row_rect.y      = row.get_height()
        row_rect.width  = row.get_allocated_width()
        row_rect.height = 1
        popover.set_pointing_to(row_rect)
        popover.set_position(Gtk.PositionType.BOTTOM)
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

    # Save helpers

    def _flush_pending_save(self) -> None:
        """Write buffered changes to disk and cancel all pending timeouts."""
        for attr in (
            "rename_timeout_id",
            "sidebar_update_timeout_id",
            "highlight_timeout_id",
            "image_timeout_id",
            "search_timeout_id",
        ):
            tid = getattr(self, attr)
            if tid > 0:
                GLib.source_remove(tid)
                setattr(self, attr, 0)

        if self.current_note and self.current_note.startswith(".template:"):
            tmpl_slug = self.current_note.split(":", 1)[1]
            start, end = self.buffer.get_bounds()
            content = self.buffer.get_text(start, end, True)
            if content:
                self.template_manager.update_template(tmpl_slug, content)
            return

        if self.current_note:
            start, end = self.buffer.get_bounds()
            content = self.buffer.get_text(start, end, True)
            if content:
                if self.notes_manager.is_encrypted(self.current_note):
                    if self._session_password_bytes is not None:
                        try:
                            from core.encryption import derive_key_from_file, encrypt, _SALT_LEN
                            raw = self.notes_manager.read_note(self.current_note)
                            ciphertext_bytes = raw.encode("latin-1")
                            file_salt = ciphertext_bytes[:_SALT_LEN]
                            password = self._session_password_bytes.decode("utf-8")
                            key = derive_key_from_file(password, ciphertext_bytes)
                            key_bytes = bytearray(key)
                            ciphertext = encrypt(content, key_bytes, file_salt)
                            self.notes_manager.save_note(self.current_note, ciphertext.decode("latin-1"), encrypt=True)
                        except Exception as e:
                            logger.error("Failed to encrypt note '%s' on save: %s", self.current_note, e)
                    else:
                        logger.warning("Skipping save of encrypted note '%s' — session locked", self.current_note)
                else:
                    self.notes_manager.save_note(self.current_note, content)

    def _reschedule(self, timeout_attr: str, delay_ms: int, callback: Callable) -> None:
        """Cancel any pending GLib timeout and schedule a fresh one."""
        current_id = getattr(self, timeout_attr)
        if current_id > 0:
            GLib.source_remove(current_id)
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
        entry = self.sidebar.search_entry
        if entry.has_focus() and entry.get_text():
            # Ctrl+F when search already focused and has text: clear it.
            entry.set_text("")
            self.refresh_list()
        else:
            entry.grab_focus()
        return True

    def show_shortcuts_dialog(self) -> bool:
        """Show the keyboard shortcuts window (Ctrl+H)."""
        win = Gtk.ShortcutsWindow(transient_for=self.win, modal=True)

        section = Gtk.ShortcutsSection(visible=True)

        def _group(title: str, shortcuts: list[tuple[str, str]]) -> Gtk.ShortcutsGroup:
            group = Gtk.ShortcutsGroup(title=title, visible=True)
            for accel, desc in shortcuts:
                item = Gtk.ShortcutsShortcut(
                    accelerator=accel, title=desc, visible=True
                )
                group.append(item)
            return group

        section.append(_group("Navigation", [
            ("<Primary>n", "New note"),
            ("<Primary><Shift>n", "New from template"),
            ("<Primary>d", "Dashboard"),
            ("<Primary>g", "Knowledge graph"),
            ("<Primary>f", "Search  (press again to clear)"),
            ("<Primary>h", "This shortcuts window"),
            ("<Primary><Shift>s", "Settings"),
            ("Escape", "Back to editor / clear search"),
        ]))
        section.append(_group("Notes", [
            ("<Primary>Delete", "Delete selected note"),
            ("<Primary><Shift>p", "Pin / unpin note"),
            ("<Primary><Shift>a", "Archive / unarchive note"),
            ("<Primary>l", "Lock private notes"),
            ("<Primary><Shift>t", "Insert timestamp"),
            ("<Primary><Shift>z", "Zen mode"),
            ("<Primary>q", "Quit"),
        ]))
        section.append(_group("Editor", [
            ("bracketleft bracketleft", "Open note link picker  ( [[ )"),
            ("at", "Open deadline picker  ( @ )"),
            ("braceleft braceleft", "Open variable picker  ( {{ )"),
            ("Return", "Continue list or task on new line"),
        ]))

        win.add_section(section)
        win.present()
        return True

    # Dialogs

    def show_export_dialog(self, title: str, body: str, is_error: bool = False) -> None:
        # Use AlertDialog (Adw >= 1.5) when available, else MessageDialog.
        # Errors use default (neutral) button appearance — destructive style
        # is reserved for actions that destroy data, not for error messages.
        try:
            dialog = Adw.AlertDialog(heading=title, body=body)
            dialog.add_response("ok", "OK")
            dialog.present(self.win)
        except AttributeError:
            dialog = Adw.MessageDialog(transient_for=self.win, heading=title, body=body)
            dialog.add_response("ok", "OK")
            dialog.present()

    # Cursor / click

    def on_cursor_moved(self, buffer: Gtk.TextBuffer, _pspec: object) -> None:
        if (
            not self.highlighter
            or self.is_loading
            or self.content_stack.get_visible_child_name() != "editor"
        ):
            return
        if self.current_note and self.notes_manager.is_encrypted(self.current_note):
            self._reset_lock_timer()
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        if cursor_line == self.last_cursor_line:
            return
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
        self.last_cursor_line = cursor_line

    def on_click_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        self._reset_lock_timer_on_activity()
        self.click_dispatcher.handle_click(x, y)

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
        if (
            not self.highlighter
            or self.content_stack.get_visible_child_name() != "editor"
        ):
            return False
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        self.highlighter.highlight(cursor_line=cursor_line)
        self.last_cursor_line = cursor_line
        return False

    def do_delayed_highlight(self) -> bool:
        """Re-highlight only the current line and neighbours (incremental)."""
        self.highlight_timeout_id = 0
        if not self.highlighter or self.content_stack.get_visible_child_name() != "editor":
            return False
        cursor_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        cursor_line = cursor_iter.get_line()
        # Re-highlight current line ±1 to catch setext / list continuation effects.
        total = self.buffer.get_line_count()
        start_line = max(0, cursor_line - 1)
        end_line = min(total - 1, cursor_line + 1)
        self.buffer.handler_block(self.changed_handler_id)
        self.highlighter.highlight_line_range(start_line, end_line, cursor_line=cursor_line)
        self.buffer.handler_unblock(self.changed_handler_id)
        self.last_cursor_line = cursor_line
        return False

    def do_delayed_images(self) -> bool:
        self.image_timeout_id = 0
        if self._has_images:
            self.editor.update_images(Path(self.notes_manager.notes_dir).resolve())
        return False

    # Dashboard callbacks

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
        success, line_start = self.buffer.get_iter_at_line(line_num - 1)
        if not success:
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
        self.buffer.delete(line_start, line_end)
        # Re-fetch iter after delete (line_start is now invalid)
        success, insert_iter = self.buffer.get_iter_at_line(line_num - 1)
        if success:
            self.buffer.insert(insert_iter, new_text)
        self.buffer.handler_unblock(self.changed_handler_id)

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
            content = self.notes_manager.read_note(note_name)
            lines = content.split("\n")
            if 0 < line_num <= len(lines):
                self._update_deadline_line_in_buffer(line_num, lines[line_num - 1])

    def _update_deadline_line_in_buffer(self, line_num: int, new_line: str) -> None:
        """Replace the deadline line in the editor buffer in-place."""
        success, start_iter = self.buffer.get_iter_at_line(line_num - 1)
        if not success:
            return
        end_iter = start_iter.copy()
        if not end_iter.ends_line():
            end_iter.forward_to_line_end()
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
        # Another instance is already running — print a clear message and exit.
        # We avoid spinning up a second GTK application loop here since the
        # overhead outweighs the benefit of a GUI dialog that the user may
        # not even see (e.g. when launched from a script or file manager).
        print(
            "Tokyo Notes is already running. "
            "Check your taskbar or system tray.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        app = TokyoNotes()
        exit_code = app.run(sys.argv)
    finally:
        lock.release()

    sys.exit(exit_code)

