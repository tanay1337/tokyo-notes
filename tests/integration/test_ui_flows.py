import datetime
from unittest.mock import MagicMock

import pytest

from main import TokyoNotes


class UIHelper:
    """Helper class to interact with TokyoNotes widgets in tests."""

    def __init__(self, app: TokyoNotes):
        self.app = app

    def click_button(self, button):
        """Simulate a button click."""
        button.emit("clicked")

    def toggle_button(self, button, active: bool):
        """Simulate a toggle button state change."""
        if button.get_active() != active:
            button.set_active(active)

    def set_search_text(self, text: str):
        """Type text into the sidebar search entry."""
        self.app.sidebar.search_entry.set_text(text)
        self.app.on_search_changed(self.app.sidebar.search_entry)

    def get_sidebar_rows(self):
        """Return all rows in the main sidebar list."""
        rows = []
        row = self.app.sidebar.main_list.get_first_child()
        while row:
            if hasattr(row, "note_name"):
                rows.append(row)
            row = row.get_next_sibling()
        return rows

    def find_sidebar_row(self, note_name: str):
        """Find a sidebar row by note name."""
        for row in self.get_sidebar_rows():
            if row.note_name == note_name:
                return row
        return None


@pytest.mark.gtk
class TestUIFlows:
    @pytest.fixture
    def app(self, tmp_path):
        from gi.repository import Adw

        from core.config import ConfigManager

        # Setup mock config to point to tmp_path
        original_get = ConfigManager.get

        def mock_get(self, key, default=None):
            if key == "notes_folder":
                return str(tmp_path)
            if key == "theme":
                return "tokyo-night"
            if key == "show_sidebar":
                return True
            if key == "show_backlinks":
                return True
            return original_get(self, key, default)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(ConfigManager, "get", mock_get)
            mp.setattr(Adw.Application, "run", lambda self, argv: 0)

            app = TokyoNotes()
            app._build_layout()
            yield app
            if hasattr(app, "win"):
                app.win.destroy()

    @pytest.fixture
    def ui(self, app):
        return UIHelper(app)

    def test_navigation_to_settings_and_back(self, app, ui):
        """Verify that clicking settings changes view and back returns to editor."""
        # 1. Click Settings
        ui.click_button(app.settings_btn)

        # 2. Verify view changed
        assert app.content_stack.get_visible_child_name() == "settings"
        assert app.back_btn.get_visible() is True
        assert app.content_title.get_text() == "Settings"

        # 3. Click Back
        ui.click_button(app.back_btn)

        # 4. Verify returned to editor
        assert app.content_stack.get_visible_child_name() == "editor"
        assert app.back_btn.get_visible() is False

    def test_sidebar_toggle_visibility(self, app, ui):
        """Verify the sidebar toggle button correctly hides/shows the sidebar."""
        # Initial state is shown (from our mock_get)
        assert app.split_view.get_show_sidebar() is True

        # Toggle OFF
        ui.toggle_button(app.sidebar_toggle, False)
        assert app.split_view.get_show_sidebar() is False

        # Toggle ON
        ui.toggle_button(app.sidebar_toggle, True)
        assert app.split_view.get_show_sidebar() is True

    def test_search_filters_sidebar_list(self, app, ui, tmp_path):
        """Verify that typing in search entry filters the visible sidebar rows."""
        # Create notes
        (tmp_path / "Apple.md").write_text("fruit")
        (tmp_path / "Banana.md").write_text("fruit")
        app.refresh_list()

        assert len(ui.get_sidebar_rows()) == 2

        # Search for Apple.
        # In this integration environment, we'll manually call
        # refresh_list with the text to ensure the UI updates
        # correctly since GLib main loop might not be running
        # the debouncer callback exactly as expected in XVFB.
        ui.set_search_text("Apple")
        app.refresh_list("Apple")

        rows = ui.get_sidebar_rows()
        assert len(rows) == 1
        assert rows[0].note_name == "Apple"

        # Clear search
        ui.set_search_text("")
        app.refresh_list("")
        assert len(ui.get_sidebar_rows()) == 2

    def test_editor_lock_state_ui_enforcement(self, app, ui, tmp_path):
        """Verify that the UI correctly locks the editor for encrypted notes."""
        (tmp_path / "Secret.md.enc").write_bytes(b"dummy_ciphertext")
        app.refresh_list()

        # Ensure the app thinks the session is locked
        app._is_session_locked = True

        # Select the encrypted note
        row = ui.find_sidebar_row("Secret")
        assert row is not None
        assert row.is_encrypted is True

        # Trigger selection
        app.lifecycle.on_note_selected(app.sidebar.main_list, row)

        # Verify UI state: should be locked/not editable because session is not unlocked
        assert app.editor.text_view.get_editable() is False
        assert app.current_note == "Secret"

    def test_auto_rename_on_title_change(self, app, ui, tmp_path):
        """Verify that changing the H1 heading renames the note file."""
        from gi.repository import GLib

        app.lifecycle.on_new_note(None)

        # When a new note is created, it's called 'Untitled'
        assert app.current_note == "Untitled"

        # Set content with a title
        app.buffer.set_text("# Original Title\ncontent")

        # Wait for the rename debouncer (2000ms)
        loop = GLib.MainLoop()
        GLib.timeout_add(2100, loop.quit)
        loop.run()

        assert app.current_note == "Original Title"
        assert (tmp_path / "Original Title.md").exists()

        # Change title
        app.buffer.set_text("# New Title\ncontent")

        # Wait for debouncer again
        GLib.timeout_add(2100, loop.quit)
        loop.run()

        assert app.current_note == "New Title"
        assert not (tmp_path / "Original Title.md").exists()
        assert (tmp_path / "New Title.md").exists()

    def test_dashboard_to_editor_sync(self, app, ui, tmp_path):
        """Verify that checking a task in the dashboard updates the editor buffer."""
        (tmp_path / "Tasks.md").write_text("- [ ] My Task")
        app.refresh_list()

        # Open the note in editor
        row = ui.find_sidebar_row("Tasks")
        app.lifecycle.on_note_selected(app.sidebar.main_list, row)

        # Navigate to dashboard
        app.nav.on_dashboard_clicked()
        assert app.content_stack.get_visible_child_name() == "dashboard"

        # Find the checkbox in dashboard and click it
        dashboard = app.dashboard_view
        task_row = None
        child = dashboard.dashboard_list.get_first_child()
        while child:
            cb = getattr(child, "checkbox_data", None)
            if cb and cb.get("text") == "My Task":
                task_row = child
                break
            child = child.get_next_sibling()

        assert task_row is not None
        checkbox = task_row._checkbox
        checkbox.set_active(True)  # Triggers on_checkbox_toggled

        # Verify disk
        assert "[x] My Task" in (tmp_path / "Tasks.md").read_text()

        # Verify Editor Buffer (synced in-place)
        assert "[x] My Task" in app.buffer.get_text(*app.buffer.get_bounds(), True)

    def test_first_time_security_setup(self, app, ui, tmp_path):
        """Verify the first-time master password setup flow."""
        from gi.repository import GLib, Gtk

        (tmp_path / "Public.md").write_text("sensible data")
        app.refresh_list()

        # Trigger "Make Private"
        row = ui.find_sidebar_row("Public")
        app.on_make_private(None, GLib.Variant("s", "Public"))

        # Verify SetupDialog is shown
        setup_dialog = None
        for win in Gtk.Window.list_toplevels():
            if "SetupDialog" in str(type(win)):
                setup_dialog = win
                break

        assert setup_dialog is not None

        # Fill dialog
        setup_dialog._password_entry.set_text("supersecret")
        setup_dialog._confirm_entry.set_text("supersecret")

        # Click Set Up
        setup_dialog._on_setup_clicked()

        # Verify Encryption
        assert (tmp_path / "Public.md.enc").exists()
        assert not (tmp_path / "Public.md").exists()
        assert app._session_password_bytes is not None
        assert app._is_session_locked is False
        assert row.is_encrypted is True

    def test_template_substitution(self, app, ui, tmp_path, monkeypatch):
        """Verify creating a note from a template with variable substitution."""
        # TemplateManager.templates_dir is computed from app.notes_folder
        templates_dir = tmp_path / ".templates"
        templates_dir.mkdir(exist_ok=True)
        (templates_dir / "meeting.md").write_text("# Meeting on {{today}}")

        # Mocking today's date for consistent testing
        today = datetime.date.today().isoformat()

        # Capture the callback passed to TemplatePicker
        captured_callback = None
        from ui.template_picker import TemplatePicker

        original_init = TemplatePicker.__init__

        def mocked_init(self, templates, on_selected, *args, **kwargs):
            nonlocal captured_callback
            captured_callback = on_selected
            original_init(self, templates, on_selected, *args, **kwargs)

        monkeypatch.setattr(TemplatePicker, "__init__", mocked_init)

        # Trigger New from Template
        app._on_new_from_template()

        assert captured_callback is not None
        captured_callback("meeting")

        # Verify Result
        assert app.current_note == f"Meeting on {today}"
        assert f"# Meeting on {today}" in app.buffer.get_text(
            *app.buffer.get_bounds(), True
        )

    def test_quick_add_task_from_any_view(self, app, ui, tmp_path):
        """Verify Ctrl+T opens Quick Add and adds a task."""
        from gi.repository import GLib

        app.nav.on_settings_clicked()
        assert app.content_stack.get_visible_child_name() == "settings"

        # Trigger Ctrl+T (simulated via method call)
        app._on_quick_add_shortcut()

        assert app.content_stack.get_visible_child_name() == "dashboard"
        dashboard = app.dashboard_view

        # In a real app, grab_focus() happens inside open_quick_add_popover
        # We process events to let the popover map
        loop = GLib.MainLoop()
        GLib.idle_add(loop.quit)
        loop.run()

        # Check if popover is visible. If get_visible() is flaky, we check focus
        # or just proceed with typing if we trust the method was called.
        assert dashboard._quick_add_popover.get_visible() is True

        # Fill Quick Add
        dashboard._quick_add_entry.set_text("Quick Task")
        # Submit
        dashboard._on_quick_add_submit()

        # Verify disk
        inbox_path = tmp_path / "Inbox.md"
        assert inbox_path.exists()
        assert "- [ ] Quick Task" in inbox_path.read_text()

    def test_folder_migration(self, app, ui, tmp_path):
        """Verify that changing the notes folder refreshes the entire UI."""
        (tmp_path / "Old.md").write_text("old")
        app.refresh_list()
        assert len(ui.get_sidebar_rows()) == 1

        # Create new folder
        new_path = tmp_path / "NewFolder"
        new_path.mkdir()
        (new_path / "New.md").write_text("new")

        # Change folder
        # Mock the File object to return the actual new_path string
        mock_file = MagicMock()
        mock_file.get_path.return_value = str(new_path)

        # Mock the dialog finish to return our mock file
        mock_dialog = MagicMock()
        mock_dialog.select_folder_finish.return_value = mock_file

        app._on_folder_selected(mock_dialog, None)

        # Verify UI Refresh
        assert app.notes_folder == str(new_path)
        rows = ui.get_sidebar_rows()
        assert len(rows) == 1
        assert rows[0].note_name == "New"
        assert app.buffer.get_text(*app.buffer.get_bounds(), True) == ""

    def test_existing_note_unlock_flow(self, app, ui, tmp_path):
        """Verify unlocking an existing encrypted note makes it editable."""
        from gi.repository import GLib

        from core.encryption import derive_key, encrypt

        salt = b"0123456789abcdef"
        key = derive_key("password", salt)
        ciphertext = encrypt("secret content", key, salt)
        (tmp_path / "Secret.md.enc").write_bytes(ciphertext)
        app.refresh_list()

        # Select the encrypted note
        app._is_session_locked = True
        row = ui.find_sidebar_row("Secret")
        app.lifecycle.on_note_selected(app.sidebar.main_list, row)
        assert app.editor.text_view.get_editable() is False

        # Simulate unlock
        app.unlock_session("password")

        # Wait for async key derivation and UI update
        loop = GLib.MainLoop()

        # Derive key async takes a moment. _finish_unlock is called via idle_add
        def _check_unlocked():
            if not app._is_session_locked:
                loop.quit()
            return True

        GLib.timeout_add(100, _check_unlocked)
        loop.run()

        assert app._is_session_locked is False
        assert app.editor.text_view.get_editable() is True
        assert "secret content" in app.buffer.get_text(*app.buffer.get_bounds(), True)

    def test_inactivity_auto_lock_flow(self, app, ui, tmp_path):
        """Verify that the app auto-locks and clears buffer after inactivity."""
        # Setup an unlocked session
        app._session_password_bytes = bytearray(b"password")
        app._is_session_locked = False
        app.current_note = "Public"
        app.buffer.set_text("sensitive data")

        # Mocking an encrypted note exists so lock_session does its work
        app.notes_manager.is_encrypted = MagicMock(return_value=True)

        # Set a very short timeout for testing (1 minute in config)
        app.cfg.set("lock_timeout_minutes", 1)
        app._reset_lock_timer()

        # Instead of waiting a minute, we manually trigger the timeout handler
        app._on_lock_timeout()

        assert app._is_session_locked is True
        assert app._session_password_bytes is None
        assert app.buffer.get_text(*app.buffer.get_bounds(), True) == ""
        assert app.editor.text_view.get_editable() is False

    def test_settings_impact_propagation(self, app, ui, tmp_path):
        """Verify that changing a setting immediately impacts the Dashboard view."""
        from gi.repository import Adw

        app.nav.on_dashboard_clicked()
        dashboard = app.dashboard_view

        # Initial state (from our mock_get in fixture, start_week_on_sunday is True)
        assert dashboard.get_start_week_on_sunday() is True

        # Change setting via UI
        app.nav.on_settings_clicked()
        settings = app.settings_view

        # Find the "Start Week on Sunday" switch row
        sunday_row = None

        # We need to find it in the dashboard group
        # This is a bit deep, let's just find all SwitchRows
        def find_row(widget, title):
            if isinstance(widget, Adw.SwitchRow) and widget.get_title() == title:
                return widget
            if hasattr(widget, "get_first_child"):
                child = widget.get_first_child()
                while child:
                    res = find_row(child, title)
                    if res:
                        return res
                    child = child.get_next_sibling()
            return None

        sunday_row = find_row(settings, "Start Week on Sunday")
        assert sunday_row is not None

        # Toggle it OFF
        sunday_row.set_active(False)  # Triggers on_config_changed

        # Verify Dashboard state updated
        assert dashboard.get_start_week_on_sunday() is False

    def test_backlink_navigation_loop(self, app, ui, tmp_path, monkeypatch):
        """Verify navigating from backlinks popover back to source note."""
        (tmp_path / "Note A.md").write_text("Link to [[Note B]]")
        (tmp_path / "Note B.md").write_text("# Note B")
        app.refresh_list()

        # Load Note B
        row_b = ui.find_sidebar_row("Note B")
        app.lifecycle.on_note_selected(app.sidebar.main_list, row_b)

        # Capture popover instantiation
        captured_popover = None
        from ui.backlinks_popover import BacklinksPopover

        original_init = BacklinksPopover.__init__

        def mocked_init(self, *args, **kwargs):
            nonlocal captured_popover
            captured_popover = self
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(BacklinksPopover, "__init__", mocked_init)

        # Show Backlinks Popover
        app._show_backlinks_popover(app.backlinks_btn)

        assert captured_popover is not None

        # Find "Note A" row in popover list
        row_a = None
        child = captured_popover.list_box.get_first_child()
        while child:
            if getattr(child, "note_name", None) == "Note A":
                row_a = child
                break
            child = child.get_next_sibling()

        assert row_a is not None

        # Activate the row
        captured_popover.on_row_activated(captured_popover.list_box, row_a)

        # Verify Navigation
        assert app.current_note == "Note A"
        assert app.content_stack.get_visible_child_name() == "editor"
        assert "Link to [[Note B]]" in app.buffer.get_text(
            *app.buffer.get_bounds(), True
        )
