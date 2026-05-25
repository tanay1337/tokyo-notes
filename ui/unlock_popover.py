"""Unlock dialog for private notes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

from core.utils import ErrorLabelMixin, set_response_suggested

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from main import TokyoNotes


class UnlockDialog(ErrorLabelMixin, Adw.MessageDialog):
    """Modal password prompt for unlocking private notes.

    Features:
    - Single password entry field (visibility hidden)
    - Wrong password: error label, field clears
    - Three consecutive wrong attempts: short pause (5 s cooldown)
    """

    def __init__(self, app: TokyoNotes) -> None:
        super().__init__(
            transient_for=app.win,
            modal=True,
            heading="Unlock Private Notes",
            body="Enter your master password to access private notes.",
        )
        self.app = app
        self._cooldown_check_id = 0

        self.add_response("cancel", "Cancel")
        self.add_response("unlock", "Unlock")
        set_response_suggested(self, "unlock")
        self.set_default_response("unlock")
        self.set_close_response("cancel")

        self._build_extra_content()
        self.connect("response", self._on_response)
        self.connect("notify::visible", self._on_visible_changed)

        if self.app.is_unlock_cooldown_active():
            self._enter_cooldown()

    def _on_visible_changed(self, _pspec: object, _value: object) -> None:
        if self.get_visible():
            self._entry.grab_focus()

    def _build_extra_content(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(20)
        box.set_margin_end(20)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Master password")
        self._entry.set_visibility(False)
        self._entry.set_hexpand(True)
        self._entry.set_can_focus(True)
        self._entry.set_receives_default(True)
        self._entry.connect("activate", lambda *_: self._try_unlock())
        box.append(self._entry)

        self._error_label = Gtk.Label(xalign=0)
        self._error_label.add_css_class("error-label")
        self._error_label.set_visible(False)
        box.append(self._error_label)

        self.set_extra_child(box)

    def _on_response(self, dialog: Adw.MessageDialog, response: str) -> None:
        if response != "unlock":
            return

        if self.app.is_unlock_cooldown_active():
            return

        self._try_unlock()

    def _try_unlock(self) -> None:
        password = self._entry.get_text()
        if not password:
            return

        self._hide_error()
        self._set_ui_sensitive(False)
        self.app.unlock_session(password)

    def _set_ui_sensitive(self, sensitive: bool) -> None:
        self._entry.set_sensitive(sensitive)
        self.set_response_enabled("unlock", sensitive)

    def on_verification_failed(self, message: str) -> None:
        """Called by app when unlock fails."""
        self._set_ui_sensitive(True)
        self._entry.set_text("")
        self._entry.grab_focus()
        self._show_error(message)

        if self.app.is_unlock_cooldown_active():
            self._enter_cooldown()

    def _enter_cooldown(self) -> None:
        self._set_ui_sensitive(False)
        remaining = self.app.get_unlock_cooldown_remaining()

        def _tick() -> bool:
            remaining = self.app.get_unlock_cooldown_remaining()
            if remaining <= 0:
                self._cooldown_check_id = 0
                self._set_ui_sensitive(True)
                self._hide_error()
                GLib.idle_add(lambda: (self._entry.grab_focus(), False)[1])
                return False
            self._show_error(f"Too many attempts. Wait {remaining}s…")
            return True

        self._show_error(f"Too many attempts. Wait {remaining}s…")
        self._cooldown_check_id = GLib.timeout_add_seconds(1, _tick)
