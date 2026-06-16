"""Logging configuration for Tokyo Notes.

Call configure_logging() once at process startup, before constructing
any other objects. After that, every module uses the standard pattern:

    import logging
    logger = logging.getLogger(__name__)

To enable note-name redaction in the file log, call ``set_note_names()``
with the current set of note names. Only strings that exactly match a
known note name are replaced with ``<name>``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Iterable

_note_names: set[str] = set()
_names_pattern: re.Pattern | None = None


def set_note_names(names: Iterable[str]) -> None:
    """Set the set of known note names for log sanitization.

    Pass an empty iterable to disable sanitization.
    """
    global _names_pattern
    _note_names.clear()
    _note_names.update(names)
    if _note_names:
        escaped = sorted((re.escape(n) for n in _note_names), key=len, reverse=True)
        _names_pattern = re.compile("|".join(escaped))
    else:
        _names_pattern = None


def _sanitize(msg: str) -> str:
    """Replace known note names in *msg* with ``<name>``."""
    if _names_pattern:
        return _names_pattern.sub("<name>", msg)
    return msg


class SanitizingFormatter(logging.Formatter):
    """Formatter that strips known note names from the final output."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return _sanitize(msg)


def configure_logging() -> None:
    """Set up rotating file logging and a console handler.

    Log level for the console is controlled by the environment variable
    ``TOKYO_NOTES_LOG_LEVEL`` (default ``WARNING``). The file handler
    captures ``INFO`` and above (reduce risk of note-name leakage).

    Log file location: ``~/.local/share/tokyo-notes/tokyo-notes.log``
    Rotation: 1 MB cap, 3 backups retained.
    """
    log_dir = Path.home() / ".local" / "share" / "tokyo-notes"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir.chmod(0o700)
    log_path = log_dir / "tokyo-notes.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers do the actual filtering

    fmt = SanitizingFormatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file — INFO level (note names sanitized in formatter).
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        # Non-fatal — log to stderr only.
        logging.warning("Could not open log file %s: %s", log_path, e)

    # Console — level comes from the environment.
    # No sanitizer on the console handler so tracebacks are readable during development.
    level_name = os.environ.get("TOKYO_NOTES_LOG_LEVEL", "WARNING").upper()
    console_level = getattr(logging, level_name, logging.WARNING)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(console_handler)
