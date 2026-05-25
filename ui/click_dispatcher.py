"""Click dispatcher — maps editor clicks to wiki links, URLs, tags, and deadlines."""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from core.utils import (
    DEADLINE_RE,
    MD_LINK_CLICK_RE,
    TAG_RE,
    URL_RE,
    WIKI_CLICK_RE,
)

if TYPE_CHECKING:
    from main import TokyoNotes

# Ordered list of (kind, pattern) pairs checked against the clicked line.
_CLICK_PATTERNS: list[tuple[str, Any]] = [
    ("wiki", WIKI_CLICK_RE),
    ("mdlink", MD_LINK_CLICK_RE),
    ("url", URL_RE),
    ("tag", TAG_RE),
    ("deadline", DEADLINE_RE),
]

# Whitelist of URL schemes that may be opened in the user's browser.
_SAFE_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def _is_safe_url(url: str) -> bool:
    """Return True only for http(s) URLs to prevent malicious links."""
    scheme = url.split(":", 1)[0].lower()
    return scheme in _SAFE_SCHEMES


class ClickDispatcher:
    """Resolves a pixel click in the editor to a semantic action."""

    def __init__(self, app: TokyoNotes) -> None:
        self.app = app

    def handle_click(
        self, x: float, y: float, gesture: Gtk.GestureClick | None = None
    ) -> None:
        """Translate window coordinates to a buffer position and dispatch."""
        text_view = self.app.text_view

        bx, by = text_view.window_to_buffer_coords(
            Gtk.TextWindowType.TEXT, int(x), int(y)
        )
        try:
            success, cursor_iter = text_view.get_iter_at_location(bx, by)
        except Exception:
            # GTK can raise "byte index off the end of the line" when the
            # buffer has been modified between the click and the lookup.
            return
        if not success:
            return

        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_end = cursor_iter.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()

        line_text = self.app.buffer.get_text(line_start, line_end, True)
        click_col = cursor_iter.get_line_offset()

        for kind, pattern in _CLICK_PATTERNS:
            for m in pattern.finditer(line_text):
                if m.start() <= click_col <= m.end():
                    self._dispatch(kind, m, x, y, cursor_iter, gesture)
                    return

    def _dispatch(
        self,
        kind: str,
        match: Any,
        x: float,
        y: float,
        cursor_iter: Gtk.TextIter,
        gesture: Gtk.GestureClick | None = None,
    ) -> None:
        app = self.app

        if kind == "wiki":
            if gesture:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            app.lifecycle.on_link_clicked(match.group(1))

        elif kind == "mdlink":
            url = match.group(3)
            if _is_safe_url(url):
                webbrowser.open_new_tab(url)
            else:
                app.lifecycle.on_link_clicked(url.rsplit(".", 1)[0])

        elif kind == "url":
            url = match.group(0)
            if _is_safe_url(url):
                webbrowser.open_new_tab(url)

        elif kind == "tag":
            current = app.sidebar.search_entry.get_text()
            new_text = match.group(0) if not current else f"{current} {match.group(0)}"
            app.sidebar.search_entry.set_text(new_text)
            app.on_search_changed(app.sidebar.search_entry)

        elif kind == "deadline":
            app.handle_deadline_click(
                x, y, app.current_note, cursor_iter.get_line() + 1
            )
