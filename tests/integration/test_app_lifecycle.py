from pathlib import Path

import pytest

from main import TokyoNotes


@pytest.mark.gtk
class TestAppIntegration:
    @pytest.fixture
    def app(self, tmp_path):
        from gi.repository import Adw

        from core.config import ConfigManager

        # Monkeypatch ConfigManager.get to return our tmp_path
        original_get = ConfigManager.get

        def mock_get(self, key, default=None):
            if key == "notes_folder":
                return str(tmp_path)
            if key == "theme":
                return "tokyo-night"
            if key == "show_sidebar":
                return True
            return original_get(self, key, default)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(ConfigManager, "get", mock_get)
            # Prevent the app from actually running its main loop blocks
            mp.setattr(Adw.Application, "run", lambda self, argv: 0)

            app = TokyoNotes()
            # We need to build the layout for many tests
            app._build_layout()
            yield app
            # Cleanup
            if hasattr(app, "win"):
                app.win.destroy()

    def test_app_startup_with_notes(self, app, tmp_path):
        # Pre-populate notes
        (tmp_path / "Note 1.md").write_text("# Note 1\nContent 1")
        (tmp_path / "Note 2.md").write_text("# Note 2\nContent 2")

        # Simulate initial load
        app.lifecycle.initial_load()

        # Verify first note is loaded
        assert app.current_note == "Note 2"  # Sorted by mtime, Note 2 is newer
        assert (
            app.buffer.get_text(*app.buffer.get_bounds(), True) == "# Note 2\nContent 2"
        )

    def test_create_new_note(self, app):
        app.lifecycle.on_new_note(None)
        assert app.current_note == "Untitled"
        app.buffer.set_text("# My New Title\nNew content")

        # Simulate the save flush
        app._flush_pending_save()

        # Verify file exists
        note_path = Path(app.notes_folder) / "Untitled.md"
        assert note_path.exists()
        assert "# My New Title" in note_path.read_text()

    def test_note_switching(self, app, tmp_path):
        (tmp_path / "A.md").write_text("Content A")
        (tmp_path / "B.md").write_text("Content B")
        app.refresh_list()

        # Simulate clicking on row for "A"
        # We find the row in the sidebar
        row_a = None
        row = app.sidebar.main_list.get_first_child()
        while row:
            if getattr(row, "note_name", None) == "A":
                row_a = row
                break
            row = row.get_next_sibling()

        assert row_a is not None
        app.lifecycle.on_note_selected(app.sidebar.main_list, row_a)

        assert app.current_note == "A"
        assert "Content A" in app.buffer.get_text(*app.buffer.get_bounds(), True)
