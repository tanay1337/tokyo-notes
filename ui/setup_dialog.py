"""First-time setup dialog for private notes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from core.translations import tr
from core.utils import ErrorLabelMixin, assess_password_strength

if TYPE_CHECKING:
    from main import TokyoNotes


class SetupDialog(ErrorLabelMixin, Adw.Window):
    """First-time master password setup dialog.

    Features:
    - Password + confirm fields
    - Heuristic strength indicator (weak/fair/strong)
    - After setup: offers optional recovery key export
    """

    def __init__(self, app: TokyoNotes, note_name: str) -> None:
        super().__init__(transient_for=app.win, modal=True)
        self.set_default_size(400, 320)
        self.set_title(tr("Set up Private Notes"))
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
            label=tr(
                "Private notes are locked with a master password. "
                "Choose something you will remember, there is no recovery. "
                "You will be asked for it each time you open the app "
                "and after periods of inactivity."
            ),
            xalign=0,
            wrap=True,
        )
        desc_label.add_css_class("dim-label")
        heading_box.append(desc_label)

        box.append(heading_box)

        self._password_entry = Gtk.Entry()
        self._password_entry.set_placeholder_text(tr("Master password"))
        self._password_entry.set_visibility(False)
        self._password_entry.set_hexpand(True)
        self._password_entry.connect("changed", self._on_password_changed)
        self._password_entry.connect("activate", self._on_setup_clicked)
        box.append(self._password_entry)

        self._confirm_entry = Gtk.Entry()
        self._confirm_entry.set_placeholder_text(tr("Confirm password"))
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

        cancel_btn = Gtk.Button(label=tr("Cancel"), hexpand=True)
        cancel_btn.add_css_class("pill")
        cancel_btn.connect("clicked", lambda *_: self.close())
        btn_row.append(cancel_btn)

        btn = Gtk.Button(label=tr("Set Up"), hexpand=True)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.connect("clicked", self._on_setup_clicked)
        btn_row.append(btn)

        box.append(btn_row)

        clamp.set_child(box)
        toolbar.set_content(clamp)

    def _on_password_changed(self, *_args) -> None:
        password = self._password_entry.get_text()
        strength = assess_password_strength(password)
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
            self._show_error(tr("Password cannot be empty."))
            return
        if password != confirm:
            self._show_error(tr("Passwords do not match."))
            return
        if len(password) < 8:
            self._show_error(tr("Password is too short (min 8 characters)."))
            return

        if self._note_name:
            self._encrypt_note(self._note_name, password)
            self.app._show_toast(
                tr("'{note_name}' is now private").format(note_name=self._note_name)
            )
        else:
            self.app._show_toast(tr("No note to encrypt — make a note private first"))

        self.close()

    def _encrypt_note(self, note_name: str, password: str) -> None:
        from core.services import encrypt_note_on_disk

        password_bytes = bytearray(password.encode("utf-8"))
        content, key_bytes = encrypt_note_on_disk(
            note_name=note_name,
            password=password_bytes,
            notes_manager=self.app.notes_manager,
            cfg=self.app.cfg,
        )

        self.app._session_password_bytes = password_bytes
        self.app._encryption_key_cache[note_name] = key_bytes
        self.app._is_session_locked = False
        self.app.sidebar.set_row_encrypted(note_name, True)
        self.app._update_sidebar_lock_state()
        self.app._reset_lock_timer()
        self.app.current_note = note_name
        self.app._select_sidebar_row(note_name)
        self.app._set_buffer_text(content)
        if self.app.highlighter:
            self.app.highlighter.highlight()

        if hasattr(self.app, "settings_view") and self.app.settings_view:
            self.app.settings_view._has_encrypted_notes = True
            self.app.settings_view._change_password_btn.set_label("Change password")
            self.app.settings_view._change_password_btn.set_sensitive(True)
            self.app.settings_view._change_password_row.set_subtitle("")
