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
    "notes_folder": None,  # resolved lazily in __init__
    "show_sidebar": True,
    "show_toolbar": True,
    "show_stats": False,
    "sakura_effect": True,
    "theme": "tokyo-night",
    "show_completed": True,
    "show_progress_rings": True,
    "show_backlinks": True,
    "show_toc": False,
    "lock_timeout_minutes": 5,
    "start_week_on_sunday": True,
    "create_on_link_click": True,
    "git_enabled": False,
    "git_auto_commit": True,
    "git_init_dismissed": False,
    "font_family": None,
    "font_size": None,
    "language": "en",
    "spell_check_enabled": True,
    "spell_check_language": "en",
    "always_show_markdown": False,
    "sort_order": "last_modified",
    "speech_enabled": False,
    "speech_language": None,
    "speech_input_device": None,
    "embed_width": 0,
    "telegram_bot_token": "",
    "telegram_target_note": "Inbox",
    "telegram_separator": False,
    "telegram_prefix": "",
    "telegram_voice_emoji": True,
    "telegram_owner_id": 0,
    "widgets": [],
    "grid_cols": 4,
    "assistant_enabled": False,
    "llama_cpp_url": "http://127.0.0.1:8080/v1",
    "llama_cpp_port": 8080,
    "llama_cpp_api_key": "",
    "llama_cpp_model": "",
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
        self.folder_order_path: Path = self.config_dir / "folder_order.json"
        self.pinned_folders_path: Path = self.config_dir / "pinned_folders.json"
        self.pdf_state_path: Path = self.config_dir / "pdf_state.json"

        self.data: dict[str, Any] = self._load_json(self.config_path, dict(_DEFAULTS))
        removed_cloud_keys = False
        for obsolete_key in ("openai_api_key", "openai_model"):
            if obsolete_key in self.data:
                self.data.pop(obsolete_key)
                removed_cloud_keys = True
        # Resolve the notes folder default here, not at module level.
        if not self.data.get("notes_folder"):
            self.data["notes_folder"] = _default_notes_folder()

        self.pinned: set[str] = set(self._load_json(self.pinned_path, []))
        self.archived: set[str] = set(self._load_json(self.archive_path, []))
        self.encrypted: set[str] = set(self._load_json(self.encrypted_path, []))
        self.folder_order: list[str] = list(self._load_json(self.folder_order_path, []))
        self.pinned_folders: set[str] = set(
            self._load_json(self.pinned_folders_path, [])
        )
        self.pdf_state: dict[str, Any] = dict(self._load_json(self.pdf_state_path, {}))

        # Debounce state — managed exclusively by set() and _flush().
        self._dirty: bool = False
        self._flush_timer: int = 0
        if removed_cloud_keys:
            self._save_json(self.config_path, self.data)

    # JSON helpers

    def _load_json(self, path: Path, default: dict | list) -> Any:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, type(default)):
                    return data
                logger.warning(
                    "Config %s has wrong type (%s), expected %s — using default",
                    path,
                    type(data).__name__,
                    type(default).__name__,
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not read %s: %s", path, e)
        return dict(default) if isinstance(default, dict) else list(default)

    def _save_json(self, path: Path, data: dict | set | list) -> None:
        """Atomic JSON write — write temp then rename to avoid corruption on crash."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.chmod(0o700)
        save_data: Any = sorted(data) if isinstance(data, set) else data
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.touch(mode=0o600, exist_ok=True)
            tmp.chmod(0o600)
            tmp.write_text(json.dumps(save_data, indent=2), encoding="utf-8")
            tmp.replace(path)
            path.chmod(0o600)
        except OSError as e:
            logger.warning("Could not save %s: %s", path, e)

    # General settings — debounced writes

    def get(self, key: str, fallback: Any = None) -> Any:
        """Return the config value for *key*, or *fallback* if not set."""
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
        tid = self._flush_timer
        self._flush_timer = 0
        if tid > 0:
            GLib.source_remove(tid)
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
        tid = self._flush_timer
        self._flush_timer = 0
        if tid > 0:
            GLib.source_remove(tid)
        if self._dirty:
            self._save_json(self.config_path, self.data)
            self._dirty = False
            logger.debug("Config flushed immediately (shutdown)")

    # PDF reading state — immediate writes

    def get_pdf_state(self, key: str) -> dict[str, Any]:
        """Return persisted reader state for a PDF embed key."""
        state = self.pdf_state.get(key)
        return dict(state) if isinstance(state, dict) else {}

    def set_pdf_state(self, key: str, state: dict[str, Any]) -> None:
        """Persist reader state for a PDF embed key."""
        self.pdf_state[key] = state
        self._save_json(self.pdf_state_path, self.pdf_state)

    # Pinned notes — immediate writes

    def pin(self, note_name: str) -> None:
        """Persist *note_name* as pinned."""
        if note_name not in self.pinned:
            self.pinned.add(note_name)
            self._save_json(self.pinned_path, self.pinned)

    def unpin(self, note_name: str) -> None:
        """Remove *note_name* from the pinned set."""
        if note_name in self.pinned:
            self.pinned.discard(note_name)
            self._save_json(self.pinned_path, self.pinned)

    # Archived notes — immediate writes

    def toggle_archive(self, note_name: str) -> None:
        """Toggle the archived state of *note_name*."""
        if note_name in self.archived:
            self.archived.discard(note_name)
        else:
            self.archived.add(note_name)
        self._save_json(self.archive_path, self.archived)

    def is_archived(self, note_name: str) -> bool:
        """Return True if *note_name* is archived."""
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

    def rename_note_in_config(self, old_name: str, new_name: str) -> None:
        """Migrate a note's keys in pinned, archived, encrypted, and UI state."""
        for s_attr in ("pinned", "archived", "encrypted"):
            s: set[str] = getattr(self, s_attr)
            if old_name in s:
                s.discard(old_name)
                s.add(new_name)
        old_pdf_prefix = f"{old_name}::"
        renamed_pdf_state: dict[str, Any] = {}
        pdf_state_changed = False
        for key, state in self.pdf_state.items():
            if key.startswith(old_pdf_prefix):
                renamed_pdf_state[f"{new_name}::{key[len(old_pdf_prefix) :]}"] = state
                pdf_state_changed = True
            else:
                renamed_pdf_state[key] = state
        if pdf_state_changed:
            self.pdf_state = renamed_pdf_state
            self._save_json(self.pdf_state_path, self.pdf_state)
        self._save_json(self.pinned_path, self.pinned)
        self._save_json(self.archive_path, self.archived)
        self._save_json(self.encrypted_path, self.encrypted)

    # Pinned folders — immediate writes

    def pin_folder(self, folder: str) -> None:
        """Persist *folder* as pinned."""
        if folder not in self.pinned_folders:
            self.pinned_folders.add(folder)
            self._save_json(self.pinned_folders_path, self.pinned_folders)

    def unpin_folder(self, folder: str) -> None:
        """Remove *folder* from the pinned folders set."""
        if folder in self.pinned_folders:
            self.pinned_folders.discard(folder)
            self._save_json(self.pinned_folders_path, self.pinned_folders)

    def is_folder_pinned(self, folder: str) -> bool:
        """Return True if *folder* is pinned."""
        return folder in self.pinned_folders

    # Encrypted notes — immediate writes

    def mark_encrypted(self, note_name: str) -> None:
        """Record *note_name* as encrypted."""
        if note_name not in self.encrypted:
            self.encrypted.add(note_name)
            self._save_json(self.encrypted_path, self.encrypted)

    def mark_decrypted(self, note_name: str) -> None:
        """Remove the encrypted flag from *note_name*."""
        if note_name in self.encrypted:
            self.encrypted.discard(note_name)
            self._save_json(self.encrypted_path, self.encrypted)

    def sync_encrypted_set(self, actual: set[str]) -> None:
        """Replace the encrypted set with the actual set of .md.enc files on disk.

        This is the canonical way to reconcile encrypted.json with the filesystem
        (e.g. after a folder change, external deletion, or re-encryption pass).
        """
        if self.encrypted == actual:
            return
        self.encrypted = set(actual)
        self._save_json(self.encrypted_path, self.encrypted)

    # Folder order — immediate writes

    def set_folder_order(self, folders: list[str]) -> None:
        """Persist folder display order."""
        self.folder_order = list(folders)
        self._save_json(self.folder_order_path, self.folder_order)
