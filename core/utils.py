"""Shared constants, regex patterns, and utility functions."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from core.translations import tr

IS_MAC: bool = sys.platform == "darwin"


class ErrorLabelMixin:
    """Mixin for dialogs that display inline error messages via ``_error_label``.

    Usage::

        class MyDialog(ErrorLabelMixin, Adw.Window):
            def __init__(self):
                super().__init__()
                self._error_label = Gtk.Label(xalign=0)
                self._error_label.add_css_class("error-label")
                self._error_label.set_visible(False)
                ...
    """

    def _show_error(self, message: str) -> None:
        self._error_label.set_label(message)
        self._error_label.set_visible(True)

    def _hide_error(self) -> None:
        self._error_label.set_visible(False)


def clear_listbox(lb: Gtk.ListBox) -> None:
    """Remove all children from a Gtk.ListBox."""
    while child := lb.get_first_child():
        lb.remove(child)


def set_response_suggested(
    dialog: Any,
    response_id: str,
    _logger: logging.Logger | None = None,
) -> None:
    """Mark a dialog response as SUGGESTED with graceful fallback on older Adw."""
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw

    try:
        dialog.set_response_appearance(response_id, Adw.ResponseAppearance.SUGGESTED)
    except AttributeError:
        (_logger or logging.getLogger(__name__)).debug(
            "set_response_appearance not supported (older Adw version)"
        )


def confirm_destructive_dialog(
    transient_for: Gtk.Window,
    heading: str,
    body: str,
    confirm_label: str = "Delete",
    cancel_label: str = "Cancel",
    _logger: logging.Logger | None = None,
) -> Any:
    """Create a confirmation dialog with a destructive-action button.

    Falls back gracefully on older Adw versions that lack set_response_appearance.
    """
    confirm_label = tr(confirm_label)
    cancel_label = tr(cancel_label)
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw

    dialog = Adw.MessageDialog(
        transient_for=transient_for,
        heading=heading,
        body=body,
    )
    dialog.add_response("cancel", cancel_label)
    dialog.add_response("delete", confirm_label)
    try:
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    except AttributeError:
        (_logger or logging.getLogger(__name__)).debug(
            "set_response_appearance not supported (older Adw version)"
        )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    return dialog


# Shared regex patterns
# Centralised here so highlighter.py, click_dispatcher.py, and storage.py
# all derive from the same definitions rather than duplicating them.

# Matches the first H1 heading line in a markdown document.
H1_TITLE_RE: re.Pattern = re.compile(r"^#\s*(.+)$", re.MULTILINE)

# URL pattern that handles balanced parentheses (e.g., "image(1).png").
# Used by MD_LINK_RE, MD_LINK_CLICK_RE, and by highlighter/editor directly.
MD_URL_BALANCED: str = r"[^\s()]*(?:\([^\s()]*\)[^\s()]*)*"

# Wiki-style and standard markdown links (used by snippet cleaner & highlighter).
WIKI_LINK_RE: re.Pattern = re.compile(r"\[\[(.*?)\]\]")
MD_LINK_RE: re.Pattern = re.compile(r"\[([^\]]*)\]\((" + MD_URL_BALANCED + r")\)")
MD_FMT_RE: re.Pattern = re.compile(r"[*_`~]")

# Click-dispatch patterns (also used by click_dispatcher.py).
WIKI_CLICK_RE: re.Pattern = re.compile(r"\[\[([^\]]+)\]\]")
MD_LINK_CLICK_RE: re.Pattern = re.compile(
    r"(!?)\[([^\]]+)\]\((" + MD_URL_BALANCED + r")\)"
)
URL_RE: re.Pattern = re.compile(r"https?://" + MD_URL_BALANCED)
TAG_RE: re.Pattern = re.compile(r"(?<!\w)#(\w+)")
DEADLINE_RE: re.Pattern = re.compile(r"@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)")

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
FENCED_CODE_RE: re.Pattern = re.compile(r"```([\w-]*)\n?([\s\S]*?)```")
FLASHCARD_FENCE_RE: re.Pattern = re.compile(
    r"^```flashcard\s*\n(.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


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


def assess_password_strength(password: str) -> dict:
    """Heuristic password strength assessment."""
    if not password:
        return {"label": "", "color": None}

    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if any(c.isupper() for c in password):
        score += 1
    if any(c.islower() for c in password):
        score += 1
    if any(c.isdigit() for c in password):
        score += 1
    if any(not c.isalnum() for c in password):
        score += 1

    if score <= 2:
        return {"label": tr("Weak"), "color": "#ff6b6b"}
    elif score <= 4:
        return {"label": tr("Fair"), "color": "#ffd93d"}
    else:
        return {"label": tr("Strong"), "color": "#6bcb77"}


# --- Folder / path helpers ---


def split_note_path(qualified_name: str) -> tuple[str | None, str]:
    """Split a qualified note name into (folder_path, stem).

    For ``'Work/Month/note'`` returns ``('Work/Month', 'note')``.
    For ``'note'`` returns ``(None, 'note')``.
    """
    if "/" in qualified_name:
        folder, stem = qualified_name.rsplit("/", 1)
        return folder, stem
    return None, qualified_name


def join_note_path(folder: str | None, stem: str) -> str:
    """Join a folder path and stem into a qualified note name.

    ``('Work/Month', 'note')`` → ``'Work/Month/note'``
    ``(None, 'note')`` → ``'note'``
    """
    return f"{folder}/{stem}" if folder else stem


def is_entry_focused(widget: object) -> bool:
    if widget is None or not isinstance(widget, Gtk.Widget):
        return False
    if isinstance(widget, (Gtk.Entry, Gtk.SearchEntry)):
        return True
    return is_entry_focused(widget.get_parent())


def strip_anchors_for_save(buffer: Gtk.TextBuffer) -> str:
    """Get buffer text sans child-anchor chars and the newline after each."""
    start, end = buffer.get_bounds()
    chars: list[str] = []
    it = start.copy()
    while it.compare(end) < 0:
        ch = it.get_char()
        if ord(ch) == 0xFFFC:
            it.forward_char()
            if it.compare(end) < 0 and it.get_char() == "\n":
                it.forward_char()
            continue
        chars.append(ch)
        it.forward_char()
    return "".join(chars)
