"""Logging configuration for Tokyo Notes.

Call configure_logging() once at process startup, before constructing
any other objects. After that, every module uses the standard pattern:

    import logging
    logger = logging.getLogger(__name__)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


def configure_logging() -> None:
    """Set up rotating file logging and a console handler.

    Log level for the console is controlled by the environment variable
    ``TOKYO_NOTES_LOG_LEVEL`` (default ``WARNING``). The file handler
    always captures ``DEBUG`` and above so that detailed traces are
    available in crash reports even when the console is quiet.

    Log file location: ``~/.local/share/tokyo-notes/tokyo-notes.log``
    Rotation: 1 MB cap, 3 backups retained.
    """
    log_dir = Path.home() / ".local" / "share" / "tokyo-notes"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "tokyo-notes.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers apply their own level filters

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file — always at DEBUG so crash reports contain full detail.
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        # Non-fatal — log to stderr only.
        logging.warning("Could not open log file %s: %s", log_path, e)

    # Console — level comes from the environment.
    level_name = os.environ.get("TOKYO_NOTES_LOG_LEVEL", "WARNING").upper()
    console_level = getattr(logging, level_name, logging.WARNING)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)
