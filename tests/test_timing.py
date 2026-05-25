"""Tests for core/timing.py — Debouncer utility."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.timing import Debouncer


class TestDebouncer:
    def test_schedule_sets_timeout_id(self):
        d = Debouncer(100, MagicMock())
        d.schedule()
        assert d._timeout_id != 0

    def test_schedule_sets_pending(self):
        d = Debouncer(100, MagicMock())
        d.schedule()
        assert d.is_pending() is True

    def test_cancel_clears_pending(self):
        d = Debouncer(100, MagicMock())
        d.schedule()
        d.cancel()
        assert d.is_pending() is False

    def test_cancel_without_schedule(self):
        d = Debouncer(100, MagicMock())
        d.cancel()  # should not raise

    def test_double_schedule_changes_timeout_id(self):
        d = Debouncer(100, MagicMock())
        d.schedule("first")
        tid1 = d._timeout_id
        d.schedule("second")
        tid2 = d._timeout_id
        # second schedule should produce a different timer ID
        assert tid2 != tid1

    def test_is_pending_after_schedule(self):
        d = Debouncer(200, MagicMock())
        assert d.is_pending() is False
        d.schedule()
        assert d.is_pending() is True
        d.cancel()
        assert d.is_pending() is False

    def test_set_delay(self):
        d = Debouncer(100, MagicMock())
        d.set_delay(500)
        assert d._delay_ms == 500

    def test_fire_calls_callback(self):
        cb = MagicMock()
        d = Debouncer(100, cb)
        d.schedule("hello")
        # Manually invoke the callback that GLib would call
        d._fire(("hello",))
        cb.assert_called_once_with("hello")
