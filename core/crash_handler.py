"""Crash handler — catches unhandled exceptions and writes structured reports.

Install once at startup via install(app). After that, any Python exception
that reaches the top of the call stack (rather than being caught inside a
GTK signal callback) will be logged, saved to disk, and shown to the user
as a non-fatal dialog where possible.
"""

from __future__ import annotations

import datetime
import logging
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import TokyoNotes

logger = logging.getLogger(__name__)

_CRASH_DIR = Path.home() / ".local" / "share" / "tokyo-notes" / "crashes"

# Eagerly import gi so that if the *runtime* environment is broken we fail
# early (at import time) rather than inside the excepthook where any error
# would be silently swallowed.
try:
    import gi  # noqa: F401  (imported for side-effect, version not needed)
except ImportError:
    gi = None  # type: ignore[misc]


def _write_crash_report(exc_type: type, exc_value: Exception, exc_tb: Any) -> Path:
    """Serialise the exception to a timestamped file and return its path."""
    _CRASH_DIR.mkdir(parents=True, exist_ok=True)
    _CRASH_DIR.chmod(0o700)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    crash_path = _CRASH_DIR / f"crash_{timestamp}.txt"

    report_lines = [
        f"Tokyo Notes crash report — {datetime.datetime.now().isoformat()}",
        "=" * 60,
        "",
        *traceback.format_exception(exc_type, exc_value, exc_tb),
    ]
    try:
        crash_path.write_text("\n".join(report_lines), encoding="utf-8")
        crash_path.chmod(0o600)
    except OSError as e:
        logger.error("Could not write crash report: %s", e)

    return crash_path


def install(app: TokyoNotes) -> None:
    """Replace sys.excepthook with one that logs, saves, and surfaces crashes."""
    original_hook = sys.excepthook

    def _hook(exc_type: type, exc_value: Exception, exc_tb: Any) -> None:
        # Let KeyboardInterrupt pass through without logging or dialog.
        if issubclass(exc_type, KeyboardInterrupt):
            original_hook(exc_type, exc_value, exc_tb)
            return

        logger.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

        crash_path = _write_crash_report(exc_type, exc_value, exc_tb)

        # Show a dialog if the main window exists and we're on the GTK main thread.
        # gi was imported at module level; if it failed, this block is skipped.
        try:
            if gi is None:
                win = None
            else:
                import gi as _gi

                _gi.require_version("Adw", "1")
                from gi.repository import Adw, GLib

                win = getattr(app, "win", None)
                if win is not None and GLib.main_depth() > 0:
                    dialog = Adw.MessageDialog(
                        transient_for=win,
                        heading="Unexpected Error",
                        body=(
                            f"{exc_type.__name__}: {exc_value}\n\n"
                            f"A crash report has been saved to:\n{crash_path}"
                        ),
                    )
                    dialog.add_response("ok", "OK")
                    dialog.present()
        except Exception:
            pass  # Never let the crash handler itself crash

        original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
