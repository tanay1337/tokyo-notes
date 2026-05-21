"""Unlock dialog for private notes."""
from __future__ import annotations

from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

if TYPE_CHECKING:
    from main import TokyoNotes


class UnlockDialog(Adw.MessageDialog):
    """Modal password prompt for unlocking private notes.

    Features:
    - Single password entry field (visibility hidden)
    - Wrong password: shake animation, field clears, error label
    - Three consecutive wrong attempts: 5-second cooldown with countdown
    """

    def __init__(self, app: "TokyoNotes") -> None:
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
        try:
            self.set_response_appearance("unlock", Adw.ResponseAppearance.SUGGESTED)
        except Exception:
            pass
        self.set_default_response("unlock")
        self.set_close_response("cancel")

        self._build_extra_content()
        self.connect("response", self._on_response)

        if self.app.is_unlock_cooldown_active():
            self._enter_cooldown()
        else:
            GLib.idle_add(lambda: (self._entry.grab_focus(), False)[1])

    def _build_extra_content(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(20)
        box.set_margin_end(20)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Master password")
        self._entry.set_visibility(False)
        self._entry.set_hexpand(True)
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

        self._entry.set_text("")
        self._hide_error()
        self.close()
        self.app.unlock_session(password)

    def _enter_cooldown(self) -> None:
        self._entry.set_sensitive(False)
        self.set_response_enabled("unlock", False)
        remaining = self.app.get_unlock_cooldown_remaining()

        def _tick() -> bool:
            remaining = self.app.get_unlock_cooldown_remaining()
            if remaining <= 0:
                self._cooldown_check_id = 0
                self._entry.set_sensitive(True)
                self.set_response_enabled("unlock", True)
                self._hide_error()
                GLib.idle_add(lambda: (self._entry.grab_focus(), False)[1])
                return False
            self._show_error(f"Too many attempts. Wait {remaining}s…")
            return True

        self._show_error(f"Too many attempts. Wait {remaining}s…")
        self._cooldown_check_id = GLib.timeout_add_seconds(1, _tick)

    def _show_error(self, message: str) -> None:
        self._error_label.set_label(message)
        self._error_label.set_visible(True)

    def _hide_error(self) -> None:
        self._error_label.set_visible(False)
