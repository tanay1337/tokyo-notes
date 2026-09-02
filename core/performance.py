"""Local slow-callback diagnostics for GTK-sensitive operations."""

from __future__ import annotations

import logging
import os
import threading
from functools import wraps
from time import perf_counter
from typing import Any, Callable

logger = logging.getLogger("performance")

_FRAME_BUDGET_MS = 16.0
_ENABLED = os.environ.get("TOKYO_NOTES_PERF_DEBUG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def enable_performance_logging() -> None:
    """Enable slow-callback warnings for this process."""
    global _ENABLED
    _ENABLED = True


def _line_count(args: tuple[Any, ...]) -> int | None:
    if not args:
        return None
    owner = args[0]
    buffer = getattr(owner, "buffer", None)
    if buffer is None:
        app = getattr(owner, "app", None) or getattr(owner, "_app", None)
        buffer = getattr(app, "buffer", None)
    if buffer is None:
        return None
    try:
        return int(buffer.get_line_count())
    except (AttributeError, TypeError, ValueError):
        return None


def slow_callback(
    operation: str,
    *,
    threshold_ms: float = _FRAME_BUDGET_MS,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Log callback durations exceeding one frame without logging note data."""

    def decorate(callback: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(callback)
        def measured(*args: Any, **kwargs: Any) -> Any:
            if not _ENABLED:
                return callback(*args, **kwargs)
            started = perf_counter()
            try:
                return callback(*args, **kwargs)
            finally:
                duration_ms = (perf_counter() - started) * 1000
                if duration_ms >= threshold_ms:
                    lines = _line_count(args)
                    suffix = f" lines={lines}" if lines is not None else ""
                    logger.warning(
                        "Slow operation operation=%s duration_ms=%.1f thread=%s%s",
                        operation,
                        duration_ms,
                        threading.current_thread().name,
                        suffix,
                    )

        return measured

    return decorate
