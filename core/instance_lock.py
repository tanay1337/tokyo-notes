"""Single-instance lock using an advisory file lock (fcntl).

Usage:
    lock = InstanceLock()
    if not lock.acquire():
        # Another instance is running — show dialog and exit.
        ...
    # Run the app.
    lock.release()

The lock is automatically released when the process exits, even without an
explicit release() call, because the OS closes all file descriptors on exit.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

_LOCK_PATH = Path.home() / ".local" / "share" / "tokyo-notes" / "instance.lock"


class InstanceLock:
    """Advisory file lock that prevents multiple concurrent app instances."""

    def __init__(self) -> None:
        self._lock_file: IO | None = None

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns True on success, False if already held."""
        try:
            import fcntl

            _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._lock_file = open(_LOCK_PATH, "w")
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            logger.debug("Instance lock acquired (pid %s)", os.getpid())
            return True
        except ImportError:
            # fcntl is Unix-only. On other platforms, skip locking.
            logger.debug("fcntl not available — skipping instance lock")
            return True
        except OSError:
            logger.info("Another instance is already running")
            if self._lock_file:
                self._lock_file.close()
                self._lock_file = None
            return False

    def release(self) -> None:
        """Release the lock. Safe to call even if acquire() was never called.

        We do NOT unlink the lock file: on Linux the flock survives the unlink
        via the open fd, but unlinking lets a concurrently-opening next instance
        race to create a new lockfile with a new inode, allowing two instances.
        Closing the fd is sufficient — the kernel cleans up on process exit.
        """
        if self._lock_file is None:
            return
        try:
            import fcntl

            if not getattr(self._lock_file, "closed", False):
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        finally:
            try:
                self._lock_file.close()
            except OSError:
                pass
            self._lock_file = None
            logger.debug("Instance lock released")
