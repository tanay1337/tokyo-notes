import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk, Pango
from pathlib import Path
from core.utils import escape_xml, format_markdown_inline
import uuid
try:
    from gi.repository import PangoCairo
except ImportError:
    PangoCairo = None

class ActionsHandler:
    def __init__(self, app):
        self.app = app

    def on_copy_markdown(self, button):
        if not self.app.current_note: return
        start, end = self.app.buffer.get_bounds()
        content = self.app.buffer.get_text(start, end, True)
        clipboard = self.app.win.get_clipboard()
        clipboard.set(content)

    def on_paste_clipboard(self, text_view):
        clipboard = self.app.win.get_clipboard()
        clipboard.read_texture_async(None, self.on_paste_texture_finish)

    def on_paste_texture_finish(self, clipboard, result):
        texture = clipboard.read_texture_finish(result)
        if texture:
            img_id = str(uuid.uuid4())
            filename = f"pasted_{img_id}.png" # Simplified path for testing, relative to notes
            # NOTE: Logic to store in the notes folder needs to be handled correctly
            # We assume the notes dir for now
            note_dir = Path(self.app.notes_manager.notes_dir)
            texture.save_to_png(str(note_dir / filename))
            self.app.buffer.insert_at_cursor(f"\n![Pasted Image]({filename})\n")

    def on_export_pdf(self, button):
        if not self.app.current_note: return
        
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
                self.app.show_export_dialog("Export Failed", f"An error occurred.", is_error=True)
            else:
                self.app.show_export_dialog("Success", f"Saved to {pdf_path}", is_error=False)
        except Exception as e:
            self.app.show_export_dialog("Error", str(e), is_error=True)

    def on_draw_page(self, operation, context, page_nr):
        if PangoCairo is None: return
        cr = context.get_cairo_context()
        width = context.get_width()
        height = context.get_height()
        cr.set_source_rgb(245/255, 244/255, 237/255)
        cr.paint()
        start, end = self.app.buffer.get_bounds()
        text = self.app.buffer.get_text(start, end, True)
        INK_BLUE = "#1B365D"
        def set_text_color(r, g, b):
            cr.set_source_rgb(r/255, g/255, b/255)
        set_text_color(20, 20, 19)
        margin = 50
        y = margin
        line_height = 14
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
            if not stripped:
                y += line_height
                continue
            if stripped.startswith('# '):
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='36' font_weight='500' foreground='{INK_BLUE}'>{escape_xml(stripped[2:])}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin, y)
                PangoCairo.show_layout(cr, layout)
                y += 40
            elif stripped.startswith('## '):
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='22' font_weight='500' foreground='{INK_BLUE}'>{escape_xml(stripped[3:])}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin, y)
                PangoCairo.show_layout(cr, layout)
                y += 28
            elif stripped.startswith('### '):
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='16' font_weight='500' foreground='{INK_BLUE}'>{escape_xml(stripped[4:])}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin, y)
                PangoCairo.show_layout(cr, layout)
                y += 22
            elif stripped.startswith('- ') or stripped.startswith('* '):
                layout = context.create_pango_layout()
                markup = format_markdown_inline(stripped[2:])
                layout.set_markup(f"<span font='10'>{markup}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin + 15, y)
                PangoCairo.show_layout(cr, layout)
                y += 14
            elif stripped.startswith('1. ') or stripped.startswith('2. ') or stripped.startswith('3. '):
                markup = format_markdown_inline(stripped)
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='10'>{markup}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin + 15, y)
                PangoCairo.show_layout(cr, layout)
                y += 14
            elif stripped.startswith('|'):
                continue
            elif stripped.startswith('>'):
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='10' font_style='italic' foreground='#504e49'>{escape_xml(stripped[2:])}</span>")
                layout.set_width(int((width - margin * 2 - 20) * Pango.SCALE))
                cr.move_to(margin + 20, y)
                PangoCairo.show_layout(cr, layout)
                y += 14
            elif stripped.startswith('---') or stripped.startswith('***') or stripped.startswith('___'):
                cr.set_source_rgb(232/255, 230/255, 220/255)
                cr.set_line_width(1)
                cr.move_to(margin, y + 7)
                cr.line_to(width - margin, y + 7)
                cr.stroke()
                set_text_color(20, 20, 19)
                y += 14
            elif stripped.startswith('`') and stripped.endswith('`'):
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='9' font_family='monospace'>{escape_xml(stripped[1:-1])}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin, y)
                PangoCairo.show_layout(cr, layout)
                y += 16
            else:
                markup = format_markdown_inline(stripped)
                layout = context.create_pango_layout()
                layout.set_markup(f"<span font='10'>{markup}</span>")
                layout.set_width(int((width - margin * 2) * Pango.SCALE))
                cr.move_to(margin, y)
                PangoCairo.show_layout(cr, layout)
                y += 14
            if y > height - margin:
                break
