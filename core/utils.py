"""Shared constants, regex patterns, and utility functions."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

IS_MAC: bool = sys.platform == "darwin"

# Shared regex patterns
# Centralised here so highlighter.py, click_dispatcher.py, and storage.py
# all derive from the same definitions rather than duplicating them.

# Matches the first H1 heading line in a markdown document.
H1_TITLE_RE: re.Pattern = re.compile(r"^#\s*(.+)$", re.MULTILINE)

# Wiki-style and standard markdown links (used by snippet cleaner & highlighter).
WIKI_LINK_RE: re.Pattern  = re.compile(r"\[\[(.*?)\]\]")
MD_LINK_RE: re.Pattern    = re.compile(r"\[(.*?)\]\(.*?\)")
MD_FMT_RE: re.Pattern     = re.compile(r"[*_`~]")

# Click-dispatch patterns (also used by click_dispatcher.py).
WIKI_CLICK_RE: re.Pattern     = re.compile(r"\[\[([^\]]+)\]\]")
MD_LINK_CLICK_RE: re.Pattern  = re.compile(r"(!?)\[([^\]]+)\]\(([^)]+)\)")
URL_RE: re.Pattern            = re.compile(r"https?://[^\s\)]+")
TAG_RE: re.Pattern            = re.compile(r"(?<!\w)#(\w+)")
DEADLINE_RE: re.Pattern       = re.compile(r"@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)")

# --- Checkbox Patterns ---
# Extraction: indent, checked_char, text, deadline
CB_EXTRACT_RE: re.Pattern = re.compile(
    r"^(\s*)-\s*\[([ xX])\]\s*(.+?)(?:\s+@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?))?$"
)
# Update: prefix, checked_char, suffix
CB_UPDATE_RE: re.Pattern = re.compile(r"^(\s*-\s*\[)([ xX])(\].*)")
# Matches [ ] or [x] or [X]
CB_ANY_RE: re.Pattern = re.compile(r"\[[ xX]\]")
CB_EMPTY_RE: re.Pattern = re.compile(r"\[ \]")
CB_CHECKED_RE: re.Pattern = re.compile(r"\[[xX]\]")

# --- Markdown Structure ---
HR_RE: re.Pattern = re.compile(r"^(\s*[-*_]){3,}\s*$")
BLOCKQUOTE_RE: re.Pattern = re.compile(r"^(\s*>)\s*(.*)$")
LIST_UL_RE: re.Pattern = re.compile(r"^(\s*)([-*+])\s+(.+)$")
LIST_OL_RE: re.Pattern = re.compile(r"^(\s*)(\d+\.)\s+(.+)$")
TABLE_ROW_RE: re.Pattern = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE: re.Pattern = re.compile(r"^\s*\|?[\s\-:|]+\|?\s*$")
HEADER_ATX_RE: re.Pattern = re.compile(r"^(#+)( .+)$")
SETEXT_RE: re.Pattern = re.compile(r"^(\s*)(={3,}|-{3,})\s*$")
FENCED_CODE_RE: re.Pattern = re.compile(r"```(\w*)\n?([\s\S]*?)```")

# --- Inline markdown-to-Pango regexes (PDF renderer) ---
_FMI_LINK_RE: re.Pattern    = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_FMI_BOLD1_RE: re.Pattern   = re.compile(r"\*\*([^*]+)\*\*")
_FMI_BOLD2_RE: re.Pattern   = re.compile(r"__([^_]+)__")
_FMI_ITALIC1_RE: re.Pattern = re.compile(r"\*([^*]+)\*")
_FMI_ITALIC2_RE: re.Pattern = re.compile(r"_([^_]+)_")
_FMI_CODE_RE: re.Pattern    = re.compile(r"`([^`]+)`")
_FMI_STRIKE_RE: re.Pattern  = re.compile(r"~~([^~]+)~~")


# Text helpers

def get_snippet(content: str, length: int = 50) -> str:
    """Return a short plain-text snippet of *content* for sidebar display."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "---")):
            text = stripped
            break
    else:
        return ""

    text = WIKI_LINK_RE.sub(r"\1", text)
    text = MD_LINK_RE.sub(r"\1", text)
    text = MD_FMT_RE.sub("", text)
    return text[:length] + ("..." if len(text) > length else "")


def escape_xml(text: str) -> str:
    """Escape XML special characters for safe use in Pango markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_markdown_inline(text: str) -> str:
    """Convert basic inline markdown to Pango markup for PDF rendering."""
    text = escape_xml(text)
    text = _FMI_LINK_RE.sub(
        r'<span foreground="#1B365D" underline="single">\1</span>', text
    )
    text = _FMI_BOLD1_RE.sub(r'<span font_weight="500">\1</span>', text)
    text = _FMI_BOLD2_RE.sub(r'<span font_weight="500">\1</span>', text)
    text = _FMI_ITALIC1_RE.sub(r'<span font_style="italic">\1</span>', text)
    text = _FMI_ITALIC2_RE.sub(r'<span font_style="italic">\1</span>', text)
    text = _FMI_CODE_RE.sub(
        r'<span font_family="monospace" background="#e8e6dc">\1</span>', text
    )
    text = _FMI_STRIKE_RE.sub(r'<span strikethrough="true">\1</span>', text)
    return text


def create_empty_state_widget(message: str, base_dir: Path) -> Any:
    """Create a centred empty-state widget with the app SVG icon and a message."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.add_css_class("empty-state-box")
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)

    icon_path = Path(base_dir) / "assets" / "tokyo_notes_icon.svg"
    if icon_path.exists():
        img = Gtk.Image.new_from_file(str(icon_path))
        img.set_pixel_size(128)
        img.add_css_class("empty-state-icon")
        box.append(img)

    label = Gtk.Label(label=message)
    label.add_css_class("empty-state-label")
    box.append(label)

    return box


def get_accel(key: str) -> str:
    """Return the correct keyboard accelerator string for the current platform."""
    return f"{'<Meta>' if IS_MAC else '<Control>'}{key}"
