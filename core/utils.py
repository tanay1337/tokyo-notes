import re
import sys
from gi.repository import Gtk, Pango

IS_MAC = sys.platform == "darwin"

def get_accel(key):
    """Returns the correct accelerator string based on platform."""
    modifier = "<Meta>" if IS_MAC else "<Control>"
    return f"{modifier}{key}"

def escape_xml(text):
    """Escapes XML special characters in text."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def create_empty_state_widget(message, base_dir):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.add_css_class("empty-state-box")
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    
    icon_path = base_dir / "assets" / "tokyo_notes_icon.svg"
    if icon_path.exists():
        img = Gtk.Image.new_from_file(str(icon_path))
        img.set_pixel_size(128)
        img.add_css_class("empty-state-icon")
        box.append(img)
        
    label = Gtk.Label(label=message)
    label.add_css_class("empty-state-label")
    box.append(label)
    
    return box

def get_snippet(content, length=30):
    """Returns the first 'length' characters of content, cleaned for sidebar display."""
    # Remove all markdown headers (e.g. # Header, ## Header, etc.)
    snippet = re.sub(r'^#+\s+.*$', '', content, flags=re.MULTILINE)
    # Remove remaining newlines and extra spaces
    snippet = snippet.replace('\n', ' ').strip()
    return snippet[:length] + ("..." if len(snippet) > length else "")

def format_markdown_inline(text):
    """Basic markdown to Pango markup conversion."""
    text = escape_xml(text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<span foreground="#1B365D" underline="single">\1</span>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<span font_weight="500">\1</span>', text)
    text = re.sub(r'__([^_]+)__', r'<span font_weight="500">\1</span>', text)
    text = re.sub(r'\*([^*]+)\*', r'<span font_style="italic">\1</span>', text)
    text = re.sub(r'_([^_]+)_', r'<span font_style="italic">\1</span>', text)
    text = re.sub(r'`([^`]+)`', r'<span font_family="monospace" background="#e8e6dc">\1</span>', text)
    text = re.sub(r'~~([^~]+)~~', r'<span strikethrough="true">\1</span>', text)
    return text
