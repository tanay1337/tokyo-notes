"""Tests for core/search.py — SearchController debounce wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.search import SearchController


class TestSearchController:
    def test_on_search_changed_delegates(self):
        cb = MagicMock()
        sc = SearchController(cb)
        entry = MagicMock()
        entry.get_text.return_value = "hello"
        sc.on_search_changed(entry)
        assert sc.is_pending() is True

    def test_cancel_clears_pending(self):
        sc = SearchController(MagicMock())
        entry = MagicMock()
        entry.get_text.return_value = "test"
        sc.on_search_changed(entry)
        sc.cancel()
        assert sc.is_pending() is False

    def test_is_pending_initially_false(self):
        sc = SearchController(MagicMock())
        assert sc.is_pending() is False
