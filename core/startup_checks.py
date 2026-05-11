"""Startup validation — checks that required directories are accessible.

Call validate_notes_folder() after the main window is created so that
any recovery dialog has a window to attach to.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

if TYPE_CHECKING:
    from main import TokyoNotes

logger = logging.getLogger(__name__)


def validate_notes_folder(app: "TokyoNotes") -> None:
    """Check that the configured notes folder exists and is read/write accessible.

    If not, show a recovery dialog offering to locate the folder or fall back
    to the default location. The check runs asynchronously so the main window
    is fully realised before the dialog appears.
    """
    GLib.idle_add(lambda: _do_validate(app) or False)


def _do_validate(app: "TokyoNotes") -> None:
    folder = app.cfg.get("notes_folder")
    path = Path(folder)

    if path.exists() and path.is_dir() and os.access(path, os.R_OK | os.W_OK):
        logger.debug("Notes folder OK: %s", path)
        return

    logger.warning("Notes folder not accessible: %s", path)

    dialog = Adw.MessageDialog(
        transient_for=app.win,
        heading="Notes Folder Not Found",
        body=(
            f"The configured notes folder could not be accessed:\n\n"
            f"{folder}\n\n"
            "Would you like to locate it or switch to the default location?"
        ),
    )
    dialog.add_response("default", "Use Default Location")
    dialog.add_response("locate", "Locate Folder…")
    dialog.set_response_appearance("locate", Adw.ResponseAppearance.SUGGESTED)
    dialog.connect("response", _on_recovery_response, app)
    dialog.present()


def _on_recovery_response(
    dialog: Adw.MessageDialog, response: str, app: "TokyoNotes"
) -> None:
    if response == "default":
        from core.config import _default_notes_folder
        new_folder = _default_notes_folder()
        _apply_folder(app, new_folder)
    elif response == "locate":
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Select Notes Folder")
        file_dialog.select_folder(app.win, None, _on_folder_chosen, app)


def _on_folder_chosen(
    file_dialog: Gtk.FileDialog,
    result: Gio.AsyncResult,
    app: "TokyoNotes",
) -> None:
    try:
        folder = file_dialog.select_folder_finish(result)
        if folder:
            _apply_folder(app, folder.get_path())
    except GLib.Error:
        pass  # user cancelled — leave things as they are


def _apply_folder(app: "TokyoNotes", new_folder: str) -> None:
    """Switch the app to *new_folder* and refresh the note list."""
    from core.storage import NotesManager

    app.notes_folder = new_folder
    app.cfg.set("notes_folder", new_folder)
    app.notes_manager = NotesManager(notes_dir=new_folder)

    if app.settings_view:
        app.settings_view.update_folder_path(new_folder)

    app.current_note = None
    app.buffer.handler_block(app.changed_handler_id)
    app.buffer.set_text("")
    app.buffer.handler_unblock(app.changed_handler_id)
    app.win.set_title("Tokyo Notes")
    app.refresh_list()
    logger.info("Notes folder switched to: %s", new_folder)
