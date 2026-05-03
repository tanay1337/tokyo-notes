"""Configuration management for Tokyo Notes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass

CONFIG_DEFAULTS: dict[str, Any] = {
    'notes_folder': str(
        Path.home() / "Documents" / "TokyoNotes"
        if (Path.home() / "Documents").exists()
        else "notes"
    ),
    'show_sidebar': True,
    'show_toolbar': True,
    'show_stats': False,
    'sakura_effect': True,
    'mcp_server_enabled': False,
    'mcp_server_port': 8999,
    'theme': 'tokyo-night',
}

class ConfigManager:
    def __init__(self) -> None:
        self.config_dir: Path = Path.home() / ".config" / "tokyo-notes"
        self.config_path: Path = self.config_dir / "tokyo-notes.json"
        self.pinned_path: Path = self.config_dir / "pinned.json"
        self.archive_path: Path = self.config_dir / "archived.json"

        self.data: dict[str, Any] = self._load_json(self.config_path, CONFIG_DEFAULTS)
        self.pinned: set[str] = set(self._load_json(self.pinned_path, []))
        self.archived: set[str] = set(self._load_json(self.archive_path, []))

    def _load_json(self, path: Path, default: dict[str, Any] | list[str]) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        
        if isinstance(default, dict):
            return dict(default)
        return default

    def _save_json(self, path: Path, data: dict[str, Any] | set[str] | list[str]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # Convert sets to sorted lists for deterministic JSON
        save_data: Any = sorted(list(data)) if isinstance(data, set) else data
        try:
            path.write_text(json.dumps(save_data), encoding="utf-8")
        except OSError:
            pass

    def get(self, key: str, fallback: Any = None) -> Any:
        return self.data.get(key, CONFIG_DEFAULTS.get(key, fallback))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self._save_json(self.config_path, self.data)

    # --- Pinned ---
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

    # --- Archived ---
    def toggle_archive(self, note_name: str) -> None:
        if note_name in self.archived:
            self.archived.discard(note_name)
        else:
            self.archived.add(note_name)
        self._save_json(self.archive_path, self.archived)

    def is_archived(self, note_name: str) -> bool:
        return note_name in self.archived

    def remove_note(self, note_name: str) -> None:
        """Cleanup pinned and archived lists when a note is deleted."""
        if note_name in self.pinned:
            self.pinned.discard(note_name)
            self._save_json(self.pinned_path, self.pinned)
        if note_name in self.archived:
            self.archived.discard(note_name)
            self._save_json(self.archive_path, self.archived)
