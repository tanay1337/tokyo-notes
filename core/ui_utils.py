"""GTK signal-blocking helpers."""
from __future__ import annotations

from typing import Callable

from gi.repository import Gtk


def block_and_exec(buffer: Gtk.TextBuffer, handler_id: int, func: Callable) -> None:
    """Block *handler_id* on *buffer*, run *func*, then unblock.

    Prevents re-entrant on-changed callbacks when programmatically
    setting buffer content.

        block_and_exec(app.buffer, app.changed_handler_id,
                       lambda: app.buffer.set_text(""))
    """
    if handler_id > 0:
        buffer.handler_block(handler_id)
    try:
        func()
    finally:
        if handler_id > 0:
            buffer.handler_unblock(handler_id)
