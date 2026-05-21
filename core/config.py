"""Configuration management for Tokyo Notes."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from gi.repository import GLib

logger = logging.getLogger(__name__)

# Keys and their types are fixed — update _DEFAULTS and _migrate together.
_DEFAULTS: dict[str, Any] = {
    "notes_folder":      None,   # resolved lazily in __init__
    "show_sidebar":      True,
    "show_toolbar":      True,
    "show_stats":        False,
    "sakura_effect":     True,
    "theme":             "tokyo-night",
    "show_completed":    True,
    "show_progress_rings": True,
    "show_backlinks":    True,
    "lock_timeout_minutes": 5,
}

# How long to wait after the last set() call before flushing to disk (ms).
_DEBOUNCE_MS = 2_000


def _default_notes_folder() -> str:
    """Return the platform-appropriate default notes directory.

    Called once during ConfigManager.__init__ so we don't stat the
    filesystem at import time.
    """
    docs = Path.home() / "Documents"
    return str(docs / "TokyoNotes" if docs.exists() else Path("notes"))


class ConfigManager:
    """Persists app preferences, pinned notes, and archived notes to ~/.config.

    General settings (self.data) are written with a 2-second debounce so that
    rapid calls to set() during theme switching or autosave do not cause a
    burst of synchronous disk writes on the GTK main thread.

    Pinned and archived note sets are written immediately on every change
    because each change is user-intentional and infrequent.
    """

    def __init__(self) -> None:
        self.config_dir: Path = Path.home() / ".config" / "tokyo-notes"
        self.config_path: Path = self.config_dir / "tokyo-notes.json"
        self.pinned_path: Path = self.config_dir / "pinned.json"
        self.archive_path: Path = self.config_dir / "archived.json"
        self.encrypted_path: Path = self.config_dir / "encrypted.json"

        self.data: dict[str, Any] = self._load_json(self.config_path, dict(_DEFAULTS))
        # Resolve the notes folder default here, not at module level.
        if not self.data.get("notes_folder"):
            self.data["notes_folder"] = _default_notes_folder()

        self.pinned: set[str] = set(self._load_json(self.pinned_path, []))
        self.archived: set[str] = set(self._load_json(self.archive_path, []))
        self.encrypted: set[str] = set(self._load_json(self.encrypted_path, []))

        # Debounce state — managed exclusively by set() and _flush().
        self._dirty: bool = False
        self._flush_timer: int = 0

    # JSON helpers

    def _load_json(self, path: Path, default: dict | list) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not read %s: %s", path, e)
        return dict(default) if isinstance(default, dict) else default

    def _save_json(self, path: Path, data: dict | set | list) -> None:
        """Atomic JSON write — write temp then rename to avoid corruption on crash."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        save_data: Any = sorted(data) if isinstance(data, set) else data
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(save_data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            logger.warning("Could not save %s: %s", path, e)

    # General settings — debounced writes

    def get(self, key: str, fallback: Any = None) -> Any:
        return self.data.get(key, _DEFAULTS.get(key, fallback))

    def set(self, key: str, value: Any) -> None:
        """Update *key* in memory and schedule a debounced flush to disk.

        If the stored value is already equal to *value*, the call is a no-op —
        no dirty flag is set and no timer is scheduled. This prevents spurious
        flushes from callers (such as apply_theme on startup) that call set()
        with the value that is already persisted.
        """
        if self.data.get(key) == value:
            return
        self.data[key] = value
        self._dirty = True
        if self._flush_timer > 0:
            GLib.source_remove(self._flush_timer)
        self._flush_timer = GLib.timeout_add(_DEBOUNCE_MS, self._flush)

    def _flush(self) -> bool:
        """GLib timeout callback: write data to disk if it has changed."""
        self._flush_timer = 0
        if self._dirty:
            self._save_json(self.config_path, self.data)
            self._dirty = False
            logger.debug("Config flushed to disk")
        return False  # do not reschedule

    def flush_immediate(self) -> None:
        """Write any pending changes to disk right now.

        Call before app shutdown to ensure nothing is lost if the debounce
        timer has not yet fired.
        """
        if self._flush_timer > 0:
            GLib.source_remove(self._flush_timer)
            self._flush_timer = 0
        if self._dirty:
            self._save_json(self.config_path, self.data)
            self._dirty = False
            logger.debug("Config flushed immediately (shutdown)")

    # Pinned notes — immediate writes

    def pin(self, note_name: str) -> None:
        if note_name not in self.pinned:
            self.pinned.add(note_name)
            self._save_json(self.pinned_path, self.pinned)

    def unpin(self, note_name: str) -> None:
        if note_name in self.pinned:
            self.pinned.discard(note_name)
            self._save_json(self.pinned_path, self.pinned)

    def is_pinned(self, note_name: str) -> bool:
        return note_name in self.pinned

    # Archived notes — immediate writes

    def toggle_archive(self, note_name: str) -> None:
        if note_name in self.archived:
            self.archived.discard(note_name)
        else:
            self.archived.add(note_name)
        self._save_json(self.archive_path, self.archived)

    def is_archived(self, note_name: str) -> bool:
        return note_name in self.archived

    def remove_note(self, note_name: str) -> None:
        """Remove a deleted note from pinned, archived, and encrypted sets."""
        changed_pinned = note_name in self.pinned
        changed_archived = note_name in self.archived
        changed_encrypted = note_name in self.encrypted
        self.pinned.discard(note_name)
        self.archived.discard(note_name)
        self.encrypted.discard(note_name)
        if changed_pinned:
            self._save_json(self.pinned_path, self.pinned)
        if changed_archived:
            self._save_json(self.archive_path, self.archived)
        if changed_encrypted:
            self._save_json(self.encrypted_path, self.encrypted)

    # Encrypted notes — immediate writes

    def mark_encrypted(self, note_name: str) -> None:
        if note_name not in self.encrypted:
            self.encrypted.add(note_name)
            self._save_json(self.encrypted_path, self.encrypted)

    def mark_decrypted(self, note_name: str) -> None:
        if note_name in self.encrypted:
            self.encrypted.discard(note_name)
            self._save_json(self.encrypted_path, self.encrypted)

    def is_config_encrypted(self, note_name: str) -> bool:
        return note_name in self.encrypted
