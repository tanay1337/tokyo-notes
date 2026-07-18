"""Actions handler for application commands."""

from __future__ import annotations

import datetime
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, GLib, Gtk

from core.translations import tr

if TYPE_CHECKING:
    from main import TokyoNotes

logger = logging.getLogger(__name__)


class ActionsHandler:
    """Handles clipboard, zen mode, and other app-level actions."""

    def __init__(self, app: TokyoNotes) -> None:
        self.app = app
        self.in_zen_mode: bool = False

    # Clipboard

    def on_paste_clipboard(self, text_view: Gtk.TextView) -> None:
        clipboard = self.app.win.get_clipboard()
        formats = clipboard.get_formats()
        has_image = any(
            formats.contain_mime_type(mime)
            for mime in ("image/png", "image/jpeg", "image/webp", "image/gif")
        )
        if has_image:
            clipboard.read_texture_async(None, self.on_paste_texture_finish)
        else:
            clipboard.read_text_async(None, self.on_paste_text_finish)
        try:
            text_view.stop_emission("paste-clipboard")
        except TypeError:
            pass

    def on_paste_text_finish(
        self, clipboard: Gdk.Clipboard, result: Gio.AsyncResult
    ) -> None:
        try:
            text = clipboard.read_text_finish(result)
            if not text:
                return
            self.app.buffer.insert_at_cursor(text)
            if self.app.highlight_timeout_id:
                GLib.source_remove(self.app.highlight_timeout_id)
                self.app.highlight_timeout_id = 0
            self.app._do_highlight()
        except GLib.Error as e:
            logger.warning("Text paste skipped: %s", e.message)

    def _ensure_images_dir(self, note_dir: Path) -> Path:
        images_dir = note_dir / ".images"
        images_dir.mkdir(exist_ok=True)
        return images_dir

    def on_paste_texture_finish(
        self, clipboard: Gdk.Clipboard, result: Gio.AsyncResult
    ) -> None:
        try:
            texture = clipboard.read_texture_finish(result)
            if not texture:
                return
            note_dir = Path(self.app.notes_manager.notes_dir).resolve()
            if not note_dir.exists() or not note_dir.is_dir():
                logger.error("Invalid notes directory: %s", note_dir)
                self.app.show_export_dialog(
                    tr("Paste Failed"),
                    tr("Notes directory is invalid."),
                    is_error=True,
                )
                return
            images_dir = self._ensure_images_dir(note_dir)
            img_id = str(uuid.uuid4())
            filename = f"pasted_{img_id}.png"
            filepath = images_dir / filename
            texture.save_to_png(str(filepath))
            self.app.buffer.insert_at_cursor(f"\n![Pasted Image](.images/{filename})\n")
        except GLib.Error as e:
            # Expected when clipboard content changes between request and callback.
            logger.warning("Image paste skipped: %s", e.message)
        except Exception:
            logger.exception("Failed to paste image")
            self.app.show_export_dialog(
                tr("Paste Failed"), tr("Could not paste image."), is_error=True
            )

    # Timestamp / Zen

    def on_insert_timestamp(self, *args: Any) -> None:
        """Insert the current date and time at the cursor position."""
        self.app.buffer.insert_at_cursor(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        )

    def on_zen_mode(self, *args: Any) -> None:
        """Toggle zen mode, hiding the sidebar, toolbar, and status bar."""
        entering_zen = not self.in_zen_mode
        if entering_zen:
            # Hide everything regardless of user preferences.
            self._set_sidebar_visible(False)
            self.app.toolbar.set_visible(False)
            self.app.editor.status_bar.set_visible(False)
        else:
            # Restore from persisted preferences.
            self._set_sidebar_visible(self.app.cfg.get("show_sidebar"))
            self.app.toolbar.set_visible(self.app.cfg.get("show_toolbar"))
            self.app.editor.status_bar.set_visible(self.app.cfg.get("show_stats"))
        self.in_zen_mode = entering_zen

    def _set_sidebar_visible(self, visible: bool) -> None:
        """Show or hide the sidebar without triggering the toggle signal."""
        handler = getattr(self.app, "sidebar_toggle_handler", None)
        if handler:
            self.app.sidebar_toggle.handler_block(handler)
        self.app.split_view.set_show_sidebar(visible)
        self.app.sidebar_toggle.set_active(visible)
        if handler:
            self.app.sidebar_toggle.handler_unblock(handler)
