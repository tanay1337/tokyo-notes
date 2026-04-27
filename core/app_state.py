from pathlib import Path
import json

class AppState:
    def __init__(self):
        self.config_dir = Path.home() / ".config" / "tokyo-notes"
        self.config_path = self.config_dir / "tokyo-notes.json"
        self.config = self.load_config()
        self.notes_folder = self.config.get('notes_folder', "notes")
        
        self.current_note = None
        self.is_loading = False
        self.highlighter = None
        self.highlight_timeout_id = 0
        self.rename_timeout_id = 0
        self.changed_handler_id = 0
        self.is_updating_images = False
        self.link_anchors = {}
        self.image_anchors = []

    def load_config(self):
        default_config = {
            'notes_folder': str(Path.home() / "Documents" / "TokyoNotes" if (Path.home() / "Documents").exists() else "notes"),
            'show_sidebar': True,
            'show_toolbar': True,
            'show_stats': False
        }
        if self.config_path.exists():
            try:
                return {**default_config, **json.loads(self.config_path.read_text())}
            except:
                pass
        return default_config

    def save_config(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.config_path.write_text(json.dumps(self.config))
        except:
            pass
