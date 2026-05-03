# core/config.py
import json
from pathlib import Path

CONFIG_DEFAULTS = {
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
    def __init__(self):
        self.config_dir = Path.home() / ".config" / "tokyo-notes"
        self.config_path = self.config_dir / "tokyo-notes.json"
        self.pinned_path = self.config_dir / "pinned.json"
        self.archive_path = self.config_dir / "archived.json"

        self.data = self._load_json(self.config_path, CONFIG_DEFAULTS)
        self.pinned: set[str] = set(self._load_json(self.pinned_path, []))
        self.archived: set[str] = set(self._load_json(self.archive_path, []))

    def _load_json(self, path: Path, default):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return default if not isinstance(default, dict) else {**default}

    def _save_json(self, path: Path, data):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # Convert sets to sorted lists for deterministic JSON
        save_data = sorted(list(data)) if isinstance(data, set) else data
        try:
            path.write_text(json.dumps(save_data), encoding="utf-8")
        except OSError:
            pass

    def get(self, key, fallback=None):
        return self.data.get(key, CONFIG_DEFAULTS.get(key, fallback))

    def set(self, key, value):
        self.data[key] = value
        self._save_json(self.config_path, self.data)

    # --- Pinned ---
    def pin(self, note_name: str):
        if note_name not in self.pinned:
            self.pinned.add(note_name)
            self._save_json(self.pinned_path, self.pinned)

    def unpin(self, note_name: str):
        if note_name in self.pinned:
            self.pinned.remove(note_name)
            self._save_json(self.pinned_path, self.pinned)

    def is_pinned(self, note_name: str) -> bool:
        return note_name in self.pinned

    # --- Archived ---
    def toggle_archive(self, note_name: str):
        if note_name in self.archived:
            self.archived.discard(note_name)
        else:
            self.archived.add(note_name)
        self._save_json(self.archive_path, self.archived)

    def is_archived(self, note_name: str) -> bool:
        return note_name in self.archived

    def remove_note(self, note_name: str):
        """Cleanup pinned and archived lists when a note is deleted."""
        changed = False
        if note_name in self.pinned:
            self.pinned.discard(note_name)
            self._save_json(self.pinned_path, self.pinned)
        if note_name in self.archived:
            self.archived.discard(note_name)
            self._save_json(self.archive_path, self.archived)
