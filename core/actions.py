"""Actions handler for application commands and PDF export."""
from __future__ import annotations

import datetime
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango

from core.utils import escape_xml, format_markdown_inline

if TYPE_CHECKING:
    from main import TokyoNotes

logger = logging.getLogger(__name__)

try:
    from gi.repository import PangoCairo
except ImportError:
    PangoCairo = None

_ORDERED_LIST_RE: re.Pattern = re.compile(r"^\d+\.\s")

_PDF_BG_COLOR: tuple[float, float, float] = (245 / 255, 244 / 255, 237 / 255)
_PDF_INK_BLUE: str = "#1B365D"
_PDF_TEXT_RGB: tuple[float, float, float] = (20 / 255, 20 / 255, 19 / 255)
_PDF_LINE_HEIGHT: float = 14.0
_PDF_MARGIN: float = 50.0


class ActionsHandler:
    """Handles clipboard, PDF export, zen mode, and other app-level actions."""

    def __init__(self, app: "TokyoNotes") -> None:
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
                    "Paste Failed", "Notes directory is invalid.", is_error=True
                )
                return
            img_id = str(uuid.uuid4())
            filename = f"pasted_{img_id}.png"
            texture.save_to_png(str(note_dir / filename))
            self.app.buffer.insert_at_cursor(f"\n![Pasted Image]({filename})\n")
        except GLib.Error as e:
            # Expected when clipboard content changes between request and callback.
            logger.warning("Image paste skipped: %s", e.message)
        except Exception:
            logger.exception("Failed to paste image")
            self.app.show_export_dialog(
                "Paste Failed", "Could not paste image.", is_error=True
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

    # PDF export

    def on_export_pdf(self, button: Gtk.Button) -> None:
        """Export the current note to a PDF file in ~/Downloads."""
        if not self.app.current_note:
            return

        downloads = Path.home() / "Downloads"
        downloads.mkdir(exist_ok=True)
        safe_name = "".join(
            c for c in self.app.current_note if c.isalnum() or c in (" ", "-", "_")
        ).strip()
        pdf_path = downloads / f"{safe_name}.pdf"

        start, end = self.app.buffer.get_bounds()
        # Cache the text for the draw-page callbacks so they don't each
        # re-read the buffer (one read per page was wasteful).
        self._pdf_text: str = self.app.buffer.get_text(start, end, True)
        n_pages = self._count_pages(self._pdf_text)

        print_op = Gtk.PrintOperation()
        print_op.set_n_pages(n_pages)
        print_op.set_export_filename(str(pdf_path))
        print_op.connect("draw-page", self.on_draw_page)

        try:
            result = print_op.run(Gtk.PrintOperationAction.EXPORT, self.app.win)
            if result == Gtk.PrintOperationResult.ERROR:
                self.app.show_export_dialog(
                    "Export Failed", "An error occurred.", is_error=True
                )
            else:
                self.app.show_export_dialog("Success", f"Saved to {pdf_path}")
        except Exception as e:
            self.app.show_export_dialog("Error", str(e), is_error=True)
        finally:
            self._pdf_text = None  # release cached text

    def _line_advance(self, stripped: str) -> float:
        """Vertical advance in points for *stripped* on a PDF page."""
        if not stripped:
            return _PDF_LINE_HEIGHT
        if stripped.startswith("# "):
            return 40.0
        if stripped.startswith("## "):
            return 28.0
        if stripped.startswith("### "):
            return 22.0
        if (
            stripped.startswith("- ")
            or stripped.startswith("* ")
            or _ORDERED_LIST_RE.match(stripped)
            or stripped.startswith(("---", "***", "___"))
        ):
            return _PDF_LINE_HEIGHT
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
            return 16.0
        return _PDF_LINE_HEIGHT

    def _pdf_line_info(self, line: str, stripped: str, in_code_block: bool) -> tuple[float, bool]:
        """Return (advance, new_in_code_block) for *line* given current block state.

        Fence lines (```) get a full line advance so the page-break guard fires
        correctly for them.  Previously they had advance=0.0, which meant the
        page counter could increment *after* the fence toggle, leaving
        in_code_block in the wrong state for subsequent lines on a new page.
        """
        if stripped.startswith("```"):
            return (_PDF_LINE_HEIGHT, not in_code_block)
        if in_code_block:
            return (16.0, in_code_block)
        return (self._line_advance(stripped), in_code_block)

    def _count_pages(self, text: str) -> int:
        page_height = 841.0 - 2 * _PDF_MARGIN
        y = 0.0
        pages = 1
        in_code_block = False
        for line in text.split("\n"):
            stripped = line.strip()
            advance, in_code_block = self._pdf_line_info(line, stripped, in_code_block)
            y += advance
            if y > page_height:
                pages += 1
                y = advance
        return max(pages, 1)

    def _draw_pango(
        self,
        cr: Any,
        context: Gtk.PrintContext,
        markup: str,
        x: float,
        y: float,
        width: float,
    ) -> None:
        """Render a Pango markup string at (x, y) into the print context."""
        layout = context.create_pango_layout()
        layout.set_markup(markup)
        layout.set_width(int(width * Pango.SCALE))
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    def _render_line(
        self,
        cr: Any,
        context: Gtk.PrintContext,
        line: str,
        y: float,
        width: float,
        margin: float,
    ) -> float:
        """Render one markdown line and return the new y position."""
        stripped = line.strip()
        content_width = width - margin * 2

        if not stripped:
            return y + _PDF_LINE_HEIGHT

        if stripped.startswith("# "):
            self._draw_pango(
                cr, context,
                f"<span font='36' font_weight='500' foreground='{_PDF_INK_BLUE}'>"
                f"{escape_xml(stripped[2:])}</span>",
                margin, y, content_width,
            )
            return y + 40.0

        if stripped.startswith("## "):
            self._draw_pango(
                cr, context,
                f"<span font='22' font_weight='500' foreground='{_PDF_INK_BLUE}'>"
                f"{escape_xml(stripped[3:])}</span>",
                margin, y, content_width,
            )
            return y + 28.0

        if stripped.startswith("### "):
            self._draw_pango(
                cr, context,
                f"<span font='16' font_weight='500' foreground='{_PDF_INK_BLUE}'>"
                f"{escape_xml(stripped[4:])}</span>",
                margin, y, content_width,
            )
            return y + 22.0

        if stripped.startswith("- ") or stripped.startswith("* "):
            self._draw_pango(
                cr, context,
                f"<span font='10'>{format_markdown_inline(stripped[2:])}</span>",
                margin + 15, y, content_width,
            )
            return y + _PDF_LINE_HEIGHT

        if _ORDERED_LIST_RE.match(stripped):
            self._draw_pango(
                cr, context,
                f"<span font='10'>{format_markdown_inline(stripped)}</span>",
                margin + 15, y, content_width,
            )
            return y + _PDF_LINE_HEIGHT

        if stripped.startswith(">"):
            self._draw_pango(
                cr, context,
                f"<span font='10' font_style='italic' foreground='#504e49'>"
                f"{escape_xml(stripped.lstrip('>').lstrip())}</span>",
                margin + 20, y, content_width - 20,
            )
            return y + _PDF_LINE_HEIGHT

        if stripped.startswith(("---", "***", "___")):
            cr.set_source_rgb(232 / 255, 230 / 255, 220 / 255)
            cr.set_line_width(1)
            cr.move_to(margin, y + 7)
            cr.line_to(width - margin, y + 7)
            cr.stroke()
            cr.set_source_rgb(*_PDF_TEXT_RGB)
            return y + _PDF_LINE_HEIGHT

        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
            self._draw_pango(
                cr, context,
                f"<span font='9' font_family='monospace'>"
                f"{escape_xml(stripped[1:-1])}</span>",
                margin, y, content_width,
            )
            return y + 16.0

        # Tables: render as plain text rather than silently dropping the row.
        if stripped.startswith("|"):
            plain = " ".join(
                cell.strip() for cell in stripped.strip("|").split("|") if cell.strip()
            )
            self._draw_pango(
                cr, context,
                f"<span font='9' font_family='monospace'>{escape_xml(plain)}</span>",
                margin, y, content_width,
            )
            return y + _PDF_LINE_HEIGHT

        self._draw_pango(
            cr, context,
            f"<span font='10'>{format_markdown_inline(stripped)}</span>",
            margin, y, content_width,
        )
        return y + _PDF_LINE_HEIGHT

    def on_draw_page(
        self,
        operation: Gtk.PrintOperation,
        context: Gtk.PrintContext,
        page_nr: int,
    ) -> None:
        """GTK draw-page callback — renders the requested page of the note."""
        if PangoCairo is None:
            return

        cr = context.get_cairo_context()
        width = context.get_width()
        height = context.get_height()

        # Background
        cr.set_source_rgb(*_PDF_BG_COLOR)
        cr.paint()
        cr.set_source_rgb(*_PDF_TEXT_RGB)

        # Use the text cached by on_export_pdf to avoid re-reading the buffer
        # once per page.  Fall back to a direct read if somehow called standalone.
        text = getattr(self, "_pdf_text", None)
        if text is None:
            start, end = self.app.buffer.get_bounds()
            text = self.app.buffer.get_text(start, end, True)

        # Skip lines that belong to earlier pages.
        page_height = height - 2 * _PDF_MARGIN
        y = _PDF_MARGIN
        current_page = 0
        in_code_block = False

        for line in text.split("\n"):
            stripped = line.strip()
            advance, in_code_block = self._pdf_line_info(line, stripped, in_code_block)

            if y + advance > page_height and current_page < page_nr:
                current_page += 1
                y = _PDF_MARGIN

            if current_page < page_nr:
                y += advance
                continue

            if current_page > page_nr:
                break

            if in_code_block:
                self._draw_pango(
                    cr, context,
                    f"<span font='9' font_family='monospace'>{escape_xml(line)}</span>",
                    _PDF_MARGIN, y, width - _PDF_MARGIN * 2,
                )
                y += advance
            else:
                y = self._render_line(cr, context, line, y, width, _PDF_MARGIN)

            if y > page_height:
                break
