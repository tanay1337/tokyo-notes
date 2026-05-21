"""First-time setup dialog for private notes."""
from __future__ import annotations

from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

if TYPE_CHECKING:
    from main import TokyoNotes


class SetupDialog(Adw.Window):
    """First-time master password setup dialog.

    Features:
    - Password + confirm fields
    - Heuristic strength indicator (weak/fair/strong)
    - After setup: offers optional recovery key export
    """

    def __init__(self, app: "TokyoNotes", note_name: str) -> None:
        super().__init__(transient_for=app.win, modal=True)
        self.set_default_size(400, 320)
        self.set_title("Set up Private Notes")
        self.app = app
        self._note_name = note_name

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

        desc_label = Gtk.Label(
            label=(
                "Private notes are locked with a master password. "
                "Choose something you will remember, there is no recovery. "
                "You will be asked for it each time you open the app "
                "and after periods of inactivity."
            ),
            xalign=0, wrap=True,
        )
        desc_label.add_css_class("dim-label")
        heading_box.append(desc_label)

        box.append(heading_box)

        self._password_entry = Gtk.Entry()
        self._password_entry.set_placeholder_text("Master password")
        self._password_entry.set_visibility(False)
        self._password_entry.set_hexpand(True)
        self._password_entry.connect("changed", self._on_password_changed)
        self._password_entry.connect("activate", self._on_setup_clicked)
        box.append(self._password_entry)

        self._confirm_entry = Gtk.Entry()
        self._confirm_entry.set_placeholder_text("Confirm password")
        self._confirm_entry.set_visibility(False)
        self._confirm_entry.set_hexpand(True)
        self._confirm_entry.connect("activate", self._on_setup_clicked)
        box.append(self._confirm_entry)

        self._strength_label = Gtk.Label(xalign=0)
        self._strength_label.add_css_class("caption")
        box.append(self._strength_label)

        self._error_label = Gtk.Label(xalign=0)
        self._error_label.add_css_class("error-label")
        self._error_label.set_visible(False)
        box.append(self._error_label)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        cancel_btn = Gtk.Button(label="Cancel", hexpand=True)
        cancel_btn.add_css_class("pill")
        cancel_btn.connect("clicked", lambda *_: self.close())
        btn_row.append(cancel_btn)

        btn = Gtk.Button(label="Set Up", hexpand=True)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.connect("clicked", self._on_setup_clicked)
        btn_row.append(btn)

        box.append(btn_row)

        clamp.set_child(box)
        toolbar.set_content(clamp)

    def _on_password_changed(self, *_args) -> None:
        password = self._password_entry.get_text()
        strength = _assess_strength(password)
        if strength["color"]:
            self._strength_label.set_markup(
                f'<span foreground="{strength["color"]}">{strength["label"]}</span>'
            )
        else:
            self._strength_label.set_label("")

    def _on_setup_clicked(self, *_args) -> None:
        password = self._password_entry.get_text()
        confirm = self._confirm_entry.get_text()

        self._hide_error()

        if not password:
            self._show_error("Password cannot be empty.")
            return
        if password != confirm:
            self._show_error("Passwords do not match.")
            return
        if len(password) < 4:
            self._show_error("Password is too short (min 4 characters).")
            return

        if self._note_name:
            self._encrypt_note(self._note_name, password)
            self.app._show_toast(f"'{self._note_name}' is now private")
        else:
            self.app._show_toast("No note to encrypt — make a note private first")

        self.close()

    def _encrypt_note(self, note_name: str, password: str) -> None:
        import os
        from core.encryption import encrypt, secure_delete, derive_key, _SALT_LEN

        salt = os.urandom(_SALT_LEN)
        key = derive_key(password, salt)
        key_bytes = bytearray(key)

        content = self.app.notes_manager.read_note(note_name)
        ciphertext = encrypt(content, key_bytes, salt)

        plain_path = self.app.notes_manager.notes_dir / f"{note_name}.md"
        self.app.notes_manager.save_note(note_name, ciphertext.decode("latin-1"), encrypt=True)
        self.app.cfg.mark_encrypted(note_name)

        if plain_path.exists():
            secure_delete(plain_path)

        self.app._session_password_bytes = bytearray(password.encode("utf-8"))
        self.app._session_key = key_bytes
        self.app._is_session_locked = False
        self.app._update_sidebar_lock_state()
        self.app._reset_lock_timer()
        self.app.refresh_list()

        if hasattr(self.app, "settings_view") and self.app.settings_view:
            self.app.settings_view._has_encrypted_notes = True
            self.app.settings_view._change_password_btn.set_label("Change password")
            self.app.settings_view._change_password_btn.set_sensitive(True)
            self.app.settings_view._change_password_row.set_subtitle("")

        if self.app.current_note == note_name:
            self.app.buffer.handler_block(self.app.changed_handler_id)
            self.app.buffer.set_text(content)
            self.app.buffer.handler_unblock(self.app.changed_handler_id)

    def _show_error(self, message: str) -> None:
        self._error_label.set_label(message)
        self._error_label.set_visible(True)

    def _hide_error(self) -> None:
        self._error_label.set_visible(False)


def _assess_strength(password: str) -> dict:
    """Heuristic password strength assessment."""
    if not password:
        return {"label": "", "color": None}

    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if any(c.isupper() for c in password):
        score += 1
    if any(c.islower() for c in password):
        score += 1
    if any(c.isdigit() for c in password):
        score += 1
    if any(not c.isalnum() for c in password):
        score += 1

    if score <= 2:
        return {"label": "Weak", "color": "#ff6b6b"}
    elif score <= 4:
        return {"label": "Fair", "color": "#ffd93d"}
    else:
        return {"label": "Strong", "color": "#6bcb77"}
