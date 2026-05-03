"""Actions handler for application commands and PDF export."""
from __future__ import annotations

import datetime
import re
import uuid
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, Gtk, Pango

from core.utils import escape_xml, format_markdown_inline

try:
    from gi.repository import PangoCairo
except ImportError:
    PangoCairo = None

if TYPE_CHECKING:
    from typing import Any

_ORDERED_LIST_RE: re.Pattern = re.compile(r'^\d+\.\s')

_PDF_BG_COLOR: tuple[float, float, float] = (245/255, 244/255, 237/255)
_PDF_INK_BLUE: str = "#1B365D"
_PDF_TEXT_RGB: tuple[float, float, float] = (20/255, 20/255, 19/255)

class ActionsHandler:
    def __init__(self, app: Any) -> None:
        self.app: Any = app
        self.in_zen_mode: bool = False

    def on_copy_markdown(self, button: Gtk.Button) -> None:
        """Copies note content as markdown to the system clipboard."""
        if not self.app.current_note:
            return
        start, end = self.app.buffer.get_bounds()
        content: str = self.app.buffer.get_text(start, end, True)
        clipboard = self.app.win.get_clipboard()
        clipboard.set(content)

    def on_insert_timestamp(self, *args: Any) -> None:
        """Inserts current date and time into the editor."""
        timestamp: str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.app.buffer.insert_at_cursor(timestamp)

    def on_zen_mode(self, *args: Any) -> None:
        """Toggles zen mode, hiding UI elements."""
        toggle_handler = getattr(self.app, 'sidebar_toggle_handler', None)
        
        if self.in_zen_mode:
            # Restore saved configuration
            if toggle_handler:
                self.app.sidebar_toggle.handler_block(toggle_handler)
            
            self.app.split_view.set_show_sidebar(self.app.cfg.get('show_sidebar'))
            self.app.sidebar_toggle.set_active(self.app.cfg.get('show_sidebar'))
            
            if toggle_handler:
                self.app.sidebar_toggle.handler_unblock(toggle_handler)
            
            self.app.toolbar.set_visible(self.app.cfg.get('show_toolbar'))
            self.app.editor.status_bar.set_visible(self.app.cfg.get('show_stats'))
            self.in_zen_mode = False
        else:
            # Hide UI for Zen Mode
            if toggle_handler:
                self.app.sidebar_toggle.handler_block(toggle_handler)
            
            self.app.split_view.set_show_sidebar(False)
            self.app.sidebar_toggle.set_active(False)
            
            if toggle_handler:
                self.app.sidebar_toggle.handler_unblock(toggle_handler)
            
            self.app.toolbar.set_visible(False)
            self.app.editor.status_bar.set_visible(False)
            self.in_zen_mode = True

    def on_paste_clipboard(self, text_view: Gtk.TextView) -> None:
        """Handles paste requests from clipboard."""
        clipboard = self.app.win.get_clipboard()
        clipboard.read_texture_async(None, self.on_paste_texture_finish)

    def on_paste_texture_finish(self, clipboard: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
        """Finishes texture paste, saving it as a file."""
        try:
            texture = clipboard.read_texture_finish(result)
            if texture:
                img_id = str(uuid.uuid4())
                filename = f"pasted_{img_id}.png"
                note_dir = Path(self.app.notes_manager.notes_dir)
                texture.save_to_png(str(note_dir / filename))
                self.app.buffer.insert_at_cursor(f"\n![Pasted Image]({filename})\n")
        except Exception:
            pass

    def on_export_pdf(self, button: Gtk.Button) -> None:
        """Exports the current note as a PDF."""
        if not self.app.current_note:
            return
        
        downloads = Path.home() / "Downloads"
        downloads.mkdir(exist_ok=True)
        
        safe_name = "".join(c for c in self.app.current_note if c.isalnum() or c in (' ', '-', '_')).strip()
        pdf_path = downloads / f"{safe_name}.pdf"
        
        print_op = Gtk.PrintOperation()
        print_op.set_n_pages(1)
        print_op.set_export_filename(str(pdf_path))
        print_op.connect("draw-page", self.on_draw_page)
        
        try:
            result = print_op.run(Gtk.PrintOperationAction.EXPORT, self.app.win)
            if result == Gtk.PrintOperationResult.ERROR:
                self.app.show_export_dialog("Export Failed", "An error occurred.", is_error=True)
            else:
                self.app.show_export_dialog("Success", f"Saved to {pdf_path}", is_error=False)
        except Exception as e:
            self.app.show_export_dialog("Error", str(e), is_error=True)

    def _render_line(self, cr: Any, context: Gtk.PrintContext, line: str, y: float, width: float, margin: float) -> float:
        """Renders a single line of Markdown to PDF context."""
        line_height = 14.0
        stripped = line.strip()
        if not stripped:
            return y + line_height
            
        if stripped.startswith('# '):
            layout = context.create_pango_layout()
            layout.set_markup(f"<span font='36' font_weight='500' foreground='{_PDF_INK_BLUE}'>{escape_xml(stripped[2:])}</span>")
            layout.set_width(int((width - margin * 2) * Pango.SCALE))
            cr.move_to(margin, y)
            PangoCairo.show_layout(cr, layout)
            return y + 40
            
        elif stripped.startswith('## '):
            layout = context.create_pango_layout()
            layout.set_markup(f"<span font='22' font_weight='500' foreground='{_PDF_INK_BLUE}'>{escape_xml(stripped[3:])}</span>")
            layout.set_width(int((width - margin * 2) * Pango.SCALE))
            cr.move_to(margin, y)
            PangoCairo.show_layout(cr, layout)
            return y + 28
            
        elif stripped.startswith('### '):
            layout = context.create_pango_layout()
            layout.set_markup(f"<span font='16' font_weight='500' foreground='{_PDF_INK_BLUE}'>{escape_xml(stripped[4:])}</span>")
            layout.set_width(int((width - margin * 2) * Pango.SCALE))
            cr.move_to(margin, y)
            PangoCairo.show_layout(cr, layout)
            return y + 22
            
        elif stripped.startswith('- ') or stripped.startswith('* '):
            layout = context.create_pango_layout()
            markup = format_markdown_inline(stripped[2:])
            layout.set_markup(f"<span font='10'>{markup}</span>")
            layout.set_width(int((width - margin * 2) * Pango.SCALE))
            cr.move_to(margin + 15, y)
            PangoCairo.show_layout(cr, layout)
            return y + 14
            
        elif _ORDERED_LIST_RE.match(stripped):
            markup = format_markdown_inline(stripped)
            layout = context.create_pango_layout()
            layout.set_markup(f"<span font='10'>{markup}</span>")
            layout.set_width(int((width - margin * 2) * Pango.SCALE))
            cr.move_to(margin + 15, y)
            PangoCairo.show_layout(cr, layout)
            return y + 14
            
        elif stripped.startswith('>'):
            layout = context.create_pango_layout()
            layout.set_markup(f"<span font='10' font_style='italic' foreground='#504e49'>{escape_xml(stripped[2:])}</span>")
            layout.set_width(int((width - margin * 2 - 20) * Pango.SCALE))
            cr.move_to(margin + 20, y)
            PangoCairo.show_layout(cr, layout)
            return y + 14
            
        elif stripped.startswith('---') or stripped.startswith('***') or stripped.startswith('___'):
            cr.set_source_rgb(232/255, 230/255, 220/255)
            cr.set_line_width(1)
            cr.move_to(margin, y + 7)
            cr.line_to(width - margin, y + 7)
            cr.stroke()
            cr.set_source_rgb(*_PDF_TEXT_RGB)
            return y + 14
            
        elif stripped.startswith('`') and stripped.endswith('`'):
            layout = context.create_pango_layout()
            layout.set_markup(f"<span font='9' font_family='monospace'>{escape_xml(stripped[1:-1])}</span>")
            layout.set_width(int((width - margin * 2) * Pango.SCALE))
            cr.move_to(margin, y)
            PangoCairo.show_layout(cr, layout)
            return y + 16
            
        else:
            markup = format_markdown_inline(stripped)
            layout = context.create_pango_layout()
            layout.set_markup(f"<span font='10'>{markup}</span>")
            layout.set_width(int((width - margin * 2) * Pango.SCALE))
            cr.move_to(margin, y)
            PangoCairo.show_layout(cr, layout)
            return y + 14

    def on_draw_page(self, operation: Gtk.PrintOperation, context: Gtk.PrintContext, page_nr: int) -> None:
        """Draws the note content onto a PDF page."""
        if PangoCairo is None:
            return
        
        cr = context.get_cairo_context()
        width = context.get_width()
        height = context.get_height()
        
        cr.set_source_rgb(*_PDF_BG_COLOR)
        cr.paint()
        
        start, end = self.app.buffer.get_bounds()
        text = self.app.buffer.get_text(start, end, True)
        
        cr.set_source_rgb(*_PDF_TEXT_RGB)
        margin = 50.0
        y = margin
        lines = text.split('\n')
        in_code_block = False
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='9' font_family='monospace'>{escape_xml(line)}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin, y)
                PangoCairo.show_layout(cr, layout)
                y += 16
                continue
            
            if stripped.startswith('|'):
                continue
                
            y = self._render_line(cr, context, line, y, width, margin)
            
            if y > height - margin:
                break
