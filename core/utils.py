"""Utility functions for Tokyo Notes text processing and UI widget generation."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Gtk

if TYPE_CHECKING:
    pass

IS_MAC: bool = sys.platform == "darwin"


def get_accel(key: str) -> str:
    """Returns the correct accelerator string based on platform."""
    modifier: str = "<Meta>" if IS_MAC else "<Control>"
    return f"{modifier}{key}"


def escape_xml(text: str) -> str:
    """Escapes XML special characters in text."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def create_empty_state_widget(message: str, base_dir: Path) -> Gtk.Box:
    """Creates a standardized empty state widget."""
    box: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.add_css_class("empty-state-box")
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    
    icon_path: Path = base_dir / "assets" / "tokyo_notes_icon.svg"
    if icon_path.exists():
        img: Gtk.Image = Gtk.Image.new_from_file(str(icon_path))
        img.set_pixel_size(128)
        img.add_css_class("empty-state-icon")
        box.append(img)
        
    label: Gtk.Label = Gtk.Label(label=message)
    label.add_css_class("empty-state-label")
    box.append(label)
    
    return box


# Pre-compile regex patterns
HEADER_RE: re.Pattern = re.compile(r'^#+\s+.*$', flags=re.MULTILINE)
LINK_RE: re.Pattern = re.compile(r'\[([^\]]+)\]\([^)]+\)')
INTERNAL_LINK_RE: re.Pattern = re.compile(r'\[\[([^\]]+)\]\]')
IMAGE_RE: re.Pattern = re.compile(r'!\[[^\]]*\]\([^)]+\)')
BOLD_ITALIC_RE: re.Pattern = re.compile(r'(\*\*|__|\*|_)')
CODE_RE: re.Pattern = re.compile(r'`{1,3}.*?`{1,3}', flags=re.DOTALL)

# Pre-compile regex patterns for format_markdown_inline
_FMI_LINK_RE: re.Pattern    = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_FMI_BOLD1_RE: re.Pattern   = re.compile(r'\*\*([^*]+)\*\*')
_FMI_BOLD2_RE: re.Pattern   = re.compile(r'__([^_]+)__')
_FMI_ITALIC1_RE: re.Pattern = re.compile(r'\*([^*]+)\*')
_FMI_ITALIC2_RE: re.Pattern = re.compile(r'_([^_]+)_')
_FMI_CODE_RE: re.Pattern    = re.compile(r'`([^`]+)`')
_FMI_STRIKE_RE: re.Pattern  = re.compile(r'~~([^~]+)~~')


def _clean_snippet(text: str) -> str:
    """Applies a sequence of regex replacements to clean text for snippets."""
    text = HEADER_RE.sub('', text)
    text = LINK_RE.sub(r'\1', text)
    text = INTERNAL_LINK_RE.sub(r'\1', text)
    text = IMAGE_RE.sub('', text)
    text = BOLD_ITALIC_RE.sub('', text)
    text = CODE_RE.sub('', text)
    return text.replace('\n', ' ').strip()


def get_snippet(content: str, length: int = 30) -> str:
    """Returns the first 'length' characters of content, cleaned for sidebar display."""
    snippet: str = _clean_snippet(content)
    return snippet[:length] + ("..." if len(snippet) > length else "")


def format_markdown_inline(text: str) -> str:
    """Basic markdown to Pango markup conversion."""
    text = escape_xml(text)
    text = _FMI_LINK_RE.sub(r'<span foreground="#1B365D" underline="single">\1</span>', text)
    text = _FMI_BOLD1_RE.sub(r'<span font_weight="500">\1</span>', text)
    text = _FMI_BOLD2_RE.sub(r'<span font_weight="500">\1</span>', text)
    text = _FMI_ITALIC1_RE.sub(r'<span font_style="italic">\1</span>', text)
    text = _FMI_ITALIC2_RE.sub(r'<span font_style="italic">\1</span>', text)
    text = _FMI_CODE_RE.sub(r'<span font_family="monospace" background="#e8e6dc">\1</span>', text)
    text = _FMI_STRIKE_RE.sub(r'<span strikethrough="true">\1</span>', text)
    return text
