"""Password change dialog with two-phase re-encryption."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from core.utils import ErrorLabelMixin

if TYPE_CHECKING:
    import concurrent.futures

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from core.utils import assess_password_strength

if TYPE_CHECKING:
    from main import TokyoNotes


class PasswordChangeDialog(ErrorLabelMixin, Adw.Window):
    """Dialog to change the master password, re-encrypting all notes.

    Two-phase re-encryption (crash-safe):
    1. Decrypt each note with old key → write to .enc.new
    2. Only after all succeed: atomic rename .enc.new → .enc
    3. On any failure: delete all .enc.new, show error, leave notes intact
    """

    def __init__(self, app: TokyoNotes) -> None:
        super().__init__(transient_for=app.win, modal=True)
        self.set_default_size(420, 360)
        self.set_title("Change Password")
        self.app = app
        self._is_active = True
        self.connect("destroy", lambda *_: setattr(self, "_is_active", False))

        self._build_ui()

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(400)
        clamp.set_tightening_threshold(300)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(16)
        box.set_margin_bottom(24)

        heading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        title_label = Gtk.Label(label="Change Password", xalign=0)
        title_label.add_css_class("title-2")
        heading_box.append(title_label)

        desc_label = Gtk.Label(
            label=(
                "This will re-encrypt all your private notes. "
                "Do not close the app during this process."
            ),
            xalign=0,
            wrap=True,
        )
        desc_label.add_css_class("dim-label")
        heading_box.append(desc_label)

        box.append(heading_box)

        self._old_entry = Gtk.Entry()
        self._old_entry.set_placeholder_text("Current password")
        self._old_entry.set_visibility(False)
        self._old_entry.set_hexpand(True)
        self._old_entry.connect("activate", lambda *_: self._new_entry.grab_focus())
        box.append(self._old_entry)

        self._new_entry = Gtk.Entry()
        self._new_entry.set_placeholder_text("New password")
        self._new_entry.set_visibility(False)
        self._new_entry.set_hexpand(True)
        self._new_entry.connect("changed", self._on_password_changed)
        self._new_entry.connect("activate", lambda *_: self._confirm_entry.grab_focus())
        box.append(self._new_entry)

        self._confirm_entry = Gtk.Entry()
        self._confirm_entry.set_placeholder_text("Confirm new password")
        self._confirm_entry.set_visibility(False)
        self._confirm_entry.set_hexpand(True)
        self._confirm_entry.connect("activate", self._on_change_clicked)
        box.append(self._confirm_entry)

        self._strength_label = Gtk.Label(xalign=0)
        self._strength_label.add_css_class("caption")
        box.append(self._strength_label)

        self._error_label = Gtk.Label(xalign=0)
        self._error_label.add_css_class("error-label")
        self._error_label.set_visible(False)
        box.append(self._error_label)

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_visible(False)
        box.append(self._progress_bar)

        self._progress_label = Gtk.Label(xalign=0)
        self._progress_label.add_css_class("caption")
        self._progress_label.set_visible(False)
        box.append(self._progress_label)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        self._cancel_btn = Gtk.Button(label="Cancel", hexpand=True)
        self._cancel_btn.add_css_class("pill")
        self._cancel_btn.connect("clicked", lambda *_: self.close())
        btn_row.append(self._cancel_btn)

        self._change_btn = Gtk.Button(label="Change Password", hexpand=True)
        self._change_btn.add_css_class("suggested-action")
        self._change_btn.add_css_class("pill")
        self._change_btn.connect("clicked", self._on_change_clicked)
        btn_row.append(self._change_btn)

        box.append(btn_row)

        clamp.set_child(box)
        toolbar.set_content(clamp)

        GLib.idle_add(lambda: (self._old_entry.grab_focus(), False)[1])

    def _on_password_changed(self, *_args) -> None:
        password = self._new_entry.get_text()
        strength = assess_password_strength(password)
        if strength["color"]:
            self._strength_label.set_markup(
                f'<span foreground="{strength["color"]}">{strength["label"]}</span>'
            )
        else:
            self._strength_label.set_label("")

    def _on_change_clicked(self, *_args) -> None:
        old_password = self._old_entry.get_text()
        new_password = self._new_entry.get_text()
        confirm = self._confirm_entry.get_text()

        self._hide_error()

        if not old_password:
            self._show_error("Current password is required.")
            return
        if not new_password:
            self._show_error("New password is required.")
            return
        if new_password != confirm:
            self._show_error("New passwords do not match.")
            return
        if len(new_password) < 8:
            self._show_error("New password is too short (min 8 characters).")
            return

        self._set_ui_sensitive(False)
        GLib.idle_add(self._do_change, old_password, new_password)

    def _do_change(self, old_password: str, new_password: str) -> None:
        import os

        from core.encryption import _SALT_LEN, decrypt, derive_key, encrypt, get_pool

        encrypted_notes = [
            name
            for name in self.app.notes_manager.get_notes()
            if self.app.notes_manager.is_encrypted(name)
        ]

        if not encrypted_notes:
            self._show_error("No private notes to re-encrypt.")
            self._set_ui_sensitive(True)
            return

        # Verify old password by decrypting the first encrypted note
        # using its per-file salt
        try:
            ciphertext_bytes = self.app.notes_manager.read_encrypted_raw(
                encrypted_notes[0]
            )
            old_key = derive_key(old_password, ciphertext_bytes[:_SALT_LEN])
            old_key_bytes = bytearray(old_key)
            decrypt(ciphertext_bytes, old_key_bytes)
        except Exception:
            self._show_error("Current password is incorrect.")
            self._set_ui_sensitive(True)
            return

        total = len(encrypted_notes)
        self._progress_bar.set_visible(True)
        self._progress_label.set_visible(True)

        new_password_bytes = bytearray(new_password.encode("utf-8"))
        new_files: list[str] = []
        new_files_lock = threading.Lock()
        pool = get_pool()
        futures: list[concurrent.futures.Future] = []
        error_holder: list[str] = []

        def _re_encrypt_one(name: str) -> str | None:
            """Re-encrypt a single note on a worker thread. Returns name or raises."""
            ct = self.app.notes_manager.read_encrypted_raw(name)
            file_salt = ct[:_SALT_LEN]
            ok = derive_key(old_password, file_salt)
            okb = bytearray(ok)
            plaintext = decrypt(ct, okb)
            new_salt = os.urandom(_SALT_LEN)
            nk = derive_key(new_password_bytes, new_salt)
            nkb = bytearray(nk)
            re_enc = encrypt(plaintext, nkb, new_salt)
            new_path = self.app.notes_manager.notes_dir / f"{name}.md.enc.new"
            new_path.write_bytes(re_enc)
            return name

        def _on_one_done(future: concurrent.futures.Future) -> None:
            """Called from pool thread; marshal result to main thread."""
            if not self._is_active:
                return
            GLib.idle_add(
                self._on_re_encrypt_result,
                future,
                total,
                new_files,
                new_files_lock,
                error_holder,
            )

        for name in encrypted_notes:
            future = pool.submit(_re_encrypt_one, name)
            future.add_done_callback(_on_one_done)
            futures.append(future)

        self._pending_futures = futures
        self._pending_error_holder = error_holder
        self._pending_new_files_lock = new_files_lock
        self._pending_new_files = new_files
        self._pending_new_password_bytes = new_password_bytes
        self._pending_encrypted_notes = encrypted_notes
        # Progress is driven by _on_re_encrypt_result callbacks

    def _on_re_encrypt_result(
        self,
        future: concurrent.futures.Future,
        total: int,
        new_files: list[str],
        new_files_lock: threading.Lock,
        error_holder: list[str],
    ) -> None:
        """Called on the main thread when one note re-encryption finishes."""
        if error_holder:
            return  # already failed, ignore

        try:
            name = future.result()
        except Exception as e:
            error_holder.append(str(e))
            self._cleanup_new_files(new_files)
            self._show_error(f"Failed to re-encrypt: {e}")
            self._set_ui_sensitive(True)
            self._pending_futures = None
            return

        with new_files_lock:
            new_files.append(name)
            done = len(new_files)

        self._progress_bar.set_fraction((done + 0.5) / total)
        self._progress_label.set_label(f"Re-encrypting {done}/{total}: {name}")

        if done < total:
            return

        # All notes done → Phase 2 on main thread
        self._progress_bar.set_fraction(0.9)
        self._progress_label.set_label("Finalizing…")

        try:
            for n in new_files:
                np = self.app.notes_manager.notes_dir / f"{n}.md.enc.new"
                ep = self.app.notes_manager.notes_dir / f"{n}.md.enc"
                np.replace(ep)
        except Exception as e:
            self._cleanup_new_files(new_files)
            self._show_error(f"Failed to finalize re-encryption: {e}")
            self._set_ui_sensitive(True)
            self._pending_futures = None
            return

        # Zero old password, then update with new one
        self.app._zero_session_password()
        self.app._session_password_bytes = self._pending_new_password_bytes

        # Zero and clear encryption key cache
        for v in self.app._encryption_key_cache.values():
            v[:] = b"\x00" * len(v)
        self.app._encryption_key_cache.clear()

        encrypted_notes = self._pending_encrypted_notes
        for name in encrypted_notes:
            self.app.notes_manager._content_cache.pop(name, None)
            self.app.notes_manager._metadata_cache.pop(name, None)

        self._pending_futures = None
        self._old_entry.set_text("")
        self._new_entry.set_text("")
        self._confirm_entry.set_text("")
        self._show_success("Password changed successfully.")

    def _cleanup_new_files(self, names: list[str]) -> None:
        for name in names:
            new_path = self.app.notes_manager.notes_dir / f"{name}.md.enc.new"
            new_path.unlink(missing_ok=True)

    def _set_ui_sensitive(self, sensitive: bool) -> None:
        self._old_entry.set_sensitive(sensitive)
        self._new_entry.set_sensitive(sensitive)
        self._confirm_entry.set_sensitive(sensitive)
        self._change_btn.set_sensitive(sensitive)
        self._cancel_btn.set_sensitive(sensitive)

    def _show_success(self, message: str) -> None:
        self._progress_bar.set_visible(False)
        self._progress_label.set_visible(False)
        self._error_label.set_label(message)
        self._error_label.remove_css_class("error-label")
        self._error_label.set_markup(f'<span foreground="#6bcb77">{message}</span>')
        self._error_label.set_visible(True)
        self._set_ui_sensitive(True)
        GLib.timeout_add(2000, lambda: (self.close(), False)[1])
