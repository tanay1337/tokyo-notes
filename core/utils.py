import re
from gi.repository import Gtk, Pango

def escape_xml(text):
    """Escapes XML special characters in text."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

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
