"""Navigation controller — owns all content-stack view switching."""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

from core.graph_manager import GraphManager
from core.utils import create_empty_state_widget
from ui.dashboard import Dashboard
from ui.graph_view import GraphView
from ui.settings import SettingsView

if TYPE_CHECKING:
    from main import TokyoNotes


class NavigationController:
    """Manages all transitions between editor, dashboard, graph, and settings views."""

    def __init__(self, app: "TokyoNotes") -> None:
        self.app = app

    # Sidebar archive toggle

    def on_archived_clicked(self, btn: Gtk.Button) -> None:
        """Toggle between main and archive list in the sidebar."""
        self.app.sidebar.toggle_archive_view()

    # Dashboard

    def on_dashboard_clicked(self, button: Gtk.Button | None = None) -> None:
        """Switch to the dashboard view, lazily creating it on first access."""
        app = self.app
        if app.dashboard_view is None:
            app.dashboard_view = Dashboard(
                app.on_dashboard_checkbox_toggled,
                lambda cb, x, y: app.handle_deadline_click(x, y, cb["note"], cb["line"]),
                app.lifecycle.handle_row_click,
                self.on_dashboard_empty,
                self.refresh_dashboard,
                lambda: app.cfg.get("show_completed", True),
                lambda: app.cfg.get("show_progress_rings", True),
                assets_dir=app.base_dir / "assets",
                default_filter="today",
            )
            app.dashboard_list = app.dashboard_view.dashboard_list
            app.content_stack.add_named(app.dashboard_view, "dashboard")

        checkboxes = app.notes_manager.get_all_checkboxes(exclude=app.cfg.archived)
        unchecked = [cb for cb in checkboxes if not cb["checked"]]
        default_filter = self._compute_default_filter(unchecked)

        app.dashboard_view.update_active_filter(default_filter)
        # Pass already-fetched checkboxes to avoid a second get_all_checkboxes call.
        self._populate_dashboard(checkboxes, default_filter)
        app.content_stack.set_visible_child_name("dashboard")
        self.update_header_ui("Dashboard", is_editor=False)
        app.sidebar.set_active_view("dashboard")
        app._set_backlinks_visible(False)

    def refresh_dashboard(self, filter_type: str = "today") -> None:
        """Repopulate the dashboard, fetching fresh checkboxes from storage."""
        if self.app.dashboard_view is None:
            return
        checkboxes = self.app.notes_manager.get_all_checkboxes(
            exclude=self.app.cfg.archived
        )
        self._populate_dashboard(checkboxes, filter_type)

    def _populate_dashboard(
        self, checkboxes: list[dict], filter_type: str
    ) -> None:
        """Render *checkboxes* into the dashboard for *filter_type*.

        Separated from refresh_dashboard so on_dashboard_clicked can pass
        already-fetched checkboxes without triggering a second storage read.
        """
        app = self.app
        if app.dashboard_view is None:
            return
        count = app.dashboard_view.populate(checkboxes, filter_type)
        app.win.set_title(f"Dashboard — {count} items" if count else "Dashboard")

    def on_dashboard_empty(self, filter_type: str) -> None:
        """Insert an empty-state widget when the dashboard has no items."""
        msg = "No tasks found." if filter_type == "all" else f"No tasks for {filter_type}."
        widget = create_empty_state_widget(msg, self.app.base_dir)
        self.app.dashboard_list.append(widget)

    @staticmethod
    def _compute_default_filter(unchecked: list[dict[str, Any]]) -> str:
        """Return the most relevant dashboard filter for the current unchecked tasks.

        cb["deadline"] can be None (key present but no value set), so we always
        coerce with ``or ""`` rather than relying on dict.get's default argument,
        which is only used when the key is *absent*.
        """
        today = datetime.date.today()
        today_str = today.isoformat()
        week_end = (today + datetime.timedelta(days=6 - today.weekday())).isoformat()
        if any((cb.get("deadline") or "").startswith(today_str) for cb in unchecked):
            return "today"
        if any(
            cb["deadline"] <= week_end
            for cb in unchecked
            if cb.get("deadline")
        ):
            return "week"
        return "all"

    # Graph

    def on_graph_clicked(self) -> None:
        """Switch to the knowledge graph view, lazily creating it on first access."""
        app = self.app
        if app.graph_manager is None:
            app.graph_manager = GraphManager(app.notes_manager)
        graph_data = app.graph_manager.get_graph_data_rich(app.cfg.archived)
        if app.graph_view is None:
            app.graph_view = GraphView(graph_data, app.lifecycle.on_link_clicked)
            app.content_stack.add_named(app.graph_view, "graph")
        else:
            app.graph_view.update_data(graph_data)
        app.content_stack.set_visible_child_name("graph")
        self.update_header_ui("Knowledge Graph", is_editor=False)
        app.sidebar.set_active_view("graph")
        app._set_backlinks_visible(False)

    # Settings

    def on_settings_clicked(self, btn: Gtk.Button | None = None) -> None:
        """Switch to the settings view, lazily creating it on first access."""
        app = self.app
        if app.settings_view is None:
            has_encrypted = any(
                app.notes_manager.is_encrypted(n)
                for n in app.notes_manager.get_notes()
            )
            templates = app.template_manager.get_all_templates()
            app.settings_view = SettingsView(
                app.apply_theme,
                app.on_settings_config_changed,
                app.on_select_folder,
                {
                    "notes_folder": app.cfg.get("notes_folder"),
                    "show_toolbar": app.cfg.get("show_toolbar"),
                    "show_stats": app.cfg.get("show_stats"),
                    "sakura_effect": app.cfg.get("sakura_effect"),
                    "theme": app.cfg.get("theme"),
                    "show_completed": app.cfg.get("show_completed", True),
                    "show_progress_rings": app.cfg.get("show_progress_rings", True),
                    "show_backlinks": app.cfg.get("show_backlinks", True),
                    "lock_timeout_minutes": app.cfg.get("lock_timeout_minutes", 5),
                    "has_encrypted_notes": has_encrypted,
                },
                on_change_password=app._show_password_change_dialog,
                on_set_password=app._show_setup_dialog,
                on_new_template=app._on_new_template,
                on_edit_template=app._on_edit_template,
                on_delete_template=app._on_delete_template,
                on_open_templates_folder=app._on_open_templates_folder,
                templates=templates,
            )
            app.content_stack.add_named(app.settings_view, "settings")
        else:
            has_encrypted = any(
                app.notes_manager.is_encrypted(n)
                for n in app.notes_manager.get_notes()
            )
            app.settings_view.refresh_privacy_state(has_encrypted)
            templates = app.template_manager.get_all_templates()
            app.settings_view.refresh_templates(templates)

        app.content_stack.set_visible_child_name("settings")
        self.update_header_ui("Settings", is_editor=False)
        app.sidebar.set_active_view("settings")
        app._set_backlinks_visible(False)

    # Escape / back

    def on_escape_shortcut(self) -> bool:
        """Return to the editor from any secondary view, or clear search."""
        app = self.app
        current_page = app.content_stack.get_visible_child_name()
        if current_page in ("dashboard", "graph", "settings"):
            app.content_stack.set_visible_child_name("editor")
            title = app.current_note if app.current_note else "Tokyo Notes"
            self.update_header_ui(title, is_editor=True)
            app.sidebar.set_active_view("editor")
            app._set_backlinks_visible(True)
            return True
        if app.sidebar.search_entry.has_focus():
            app.sidebar.search_entry.set_text("")
            # set_text does not emit search-changed, so refresh manually.
            app.refresh_list()
            app.text_view.grab_focus()
            return True
        return False

    # Header

    def update_header_ui(self, title: str, is_editor: bool = True) -> None:
        """Update the content-area header bar title and button visibility."""
        app = self.app
        if is_editor:
            app.content_title.set_label(title)
            app.back_btn.set_visible(False)
        else:
            app.content_title.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
            app.back_btn.set_visible(True)
