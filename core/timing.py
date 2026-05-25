"""Debounce utility for frequent UI events."""

from __future__ import annotations

from typing import Any, Callable

from gi.repository import GLib


class Debouncer:
    """Delays a callback until a quiet period follows the last schedule() call."""

    def __init__(self, delay_ms: int, callback: Callable[..., Any]) -> None:
        self._timeout_id: int = 0
        self._callback = callback
        self._delay_ms = delay_ms

    def schedule(self, *args: Any) -> None:
        """Reset the debounce timer; callback will fire after *delay_ms* of quiet."""
        self.cancel()
        self._timeout_id = GLib.timeout_add(self._delay_ms, self._fire, args)

    def _fire(self, args: tuple) -> bool:
        self._timeout_id = 0
        self._callback(*args)
        return False

    def cancel(self) -> None:
        """Cancel any pending scheduled callback."""
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0

    def is_pending(self) -> bool:
        """Return True if a callback is scheduled and waiting."""
        return self._timeout_id > 0

    def set_delay(self, delay_ms: int) -> None:
        """Update the debounce delay. Does not affect an already-scheduled timer."""
        self._delay_ms = delay_ms
