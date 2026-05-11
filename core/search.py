"""Debounced search controller for the sidebar search entry."""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from core.timing import Debouncer


class SearchController:
    """Wraps a Debouncer to drive search-as-you-type from a Gtk.SearchEntry."""

    def __init__(self, on_search: Callable[[str], None], delay_ms: int = 150) -> None:
        self._debouncer = Debouncer(delay_ms, on_search)

    def on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._debouncer.schedule(entry.get_text())

    def cancel(self) -> None:
        """Cancel any pending search. Call before swapping the notes manager."""
        self._debouncer.cancel()

    def is_pending(self) -> bool:
        return self._debouncer.is_pending()
