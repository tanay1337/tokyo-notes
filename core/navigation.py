"""Navigation controller — owns all content-stack view switching."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

from core.graph_manager import GraphManager
from core.services import get_week_boundaries
from core.speech import model_cached
from core.translations import tr
from core.utils import create_empty_state_widget, is_entry_focused
from ui.dashboard import Dashboard
from ui.flashcard_review import FlashcardReview
from ui.graph_view import GraphView
from ui.settings import SettingsView

if TYPE_CHECKING:
    from main import TokyoNotes


class NavigationController:
    """Manages all transitions between editor, dashboard, graph, and settings views."""

    def __init__(self, app: TokyoNotes) -> None:
        self.app = app

    # Sidebar archive toggle

    def on_archived_clicked(self, btn: Gtk.Button) -> None:
        """Toggle between main and archive list in the sidebar."""
        self.app.sidebar.toggle_archive_view()

    # Dashboard

    def on_dashboard_clicked(self, button: Gtk.Button | None = None) -> None:
        """Switch to the dashboard view, lazily creating it on first access."""
        app = self.app
        app._save_current_cursor()
        if app.dashboard_view is None:
            app.dashboard_view = Dashboard(
                app.on_dashboard_checkbox_toggled,
                lambda cb, x, y: app.handle_deadline_click(
                    x,
                    y,
                    cb["note"],
                    cb["line"],
                    cb.get("text", ""),
                ),
                app.lifecycle.handle_row_click,
                self.on_dashboard_empty,
                self.refresh_dashboard,
                lambda: app.cfg.get("show_completed", True),
                lambda: app.cfg.get("show_progress_rings", True),
                lambda: app.cfg.get("start_week_on_sunday", True),
                on_snooze=app.handle_snooze,
                assets_dir=app.base_dir / "assets",
                default_filter="today",
                on_quick_add=app.on_quick_add_task,
                get_notes_fn=lambda: [
                    n
                    for n in app.notes_manager.get_notes()
                    if not app.cfg.is_archived(n)
                ],
            )
            app.dashboard_list = app.dashboard_view.dashboard_list
            app.content_stack.add_named(app.dashboard_view, "dashboard")

        checkboxes = app.notes_manager.get_all_checkboxes(exclude=app.cfg.archived)
        unchecked = [cb for cb in checkboxes if not cb["checked"]]
        default_filter = self._compute_default_filter(
            unchecked,
            start_week_on_sunday=app.cfg.get("start_week_on_sunday", True),
        )

        app.dashboard_view.update_active_filter(default_filter)
        # Pass already-fetched checkboxes to avoid a second get_all_checkboxes call.
        self._populate_dashboard(checkboxes, default_filter)
        app.content_stack.set_visible_child_name("dashboard")
        self.update_header_ui(tr("Dashboard"), is_editor=False)
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

    def _populate_dashboard(self, checkboxes: list[dict], filter_type: str) -> None:
        """Render *checkboxes* into the dashboard for *filter_type*.

        Separated from refresh_dashboard so on_dashboard_clicked can pass
        already-fetched checkboxes without triggering a second storage read.
        """
        app = self.app
        if app.dashboard_view is None:
            return
        count = app.dashboard_view.populate(checkboxes, filter_type)
        app.win.set_title(
            tr("Dashboard — {count} items").format(count=count)
            if count
            else tr("Dashboard")
        )

    def on_dashboard_empty(self, filter_type: str) -> None:
        """Insert an empty-state widget when the dashboard has no items."""
        msg = (
            tr("No tasks found.")
            if filter_type == "all"
            else tr("No tasks for {filter_type}.").format(filter_type=filter_type)
        )
        widget = create_empty_state_widget(msg, self.app.base_dir)
        self.app.dashboard_list.append(widget)

    @staticmethod
    def _compute_default_filter(
        unchecked: list[dict[str, Any]], *, start_week_on_sunday: bool = True
    ) -> str:
        """Return the most relevant dashboard filter for the current unchecked tasks.

        cb["deadline"] can be None (key present but no value set), so we always
        coerce with ``or ""`` rather than relying on dict.get's default argument,
        which is only used when the key is *absent*.
        """
        today = datetime.date.today()
        today_str = today.isoformat()
        if any((cb.get("deadline") or "").startswith(today_str) for cb in unchecked):
            return "today"
        week_start, week_end = get_week_boundaries(start_week_on_sunday)
        if any(
            week_start <= cb["deadline"] <= week_end
            for cb in unchecked
            if cb.get("deadline")
        ):
            return "week"
        return "all"

    # Graph

    def on_graph_clicked(self) -> None:
        """Switch to the knowledge graph view, lazily creating it on first access."""
        app = self.app
        app._save_current_cursor()
        if app.graph_manager is None:
            app.graph_manager = GraphManager(app.notes_manager)
        graph_data = app.graph_manager.get_graph_data_rich(app.cfg.archived)
        if app.graph_view is None:
            app.graph_view = GraphView(graph_data, app.lifecycle.on_link_clicked)
            app.content_stack.add_named(app.graph_view, "graph")
            app.graph_view.update_font(
                app.cfg.get("font_family") or "Inter",
                (app.cfg.get("font_size") or 11) * 96 // 72,
            )
        else:
            app.graph_view.update_data(graph_data)
        app.content_stack.set_visible_child_name("graph")
        self.update_header_ui(tr("Knowledge Graph"), is_editor=False)
        app.sidebar.set_active_view("graph")
        app._set_backlinks_visible(False)

    # Settings

    def on_settings_clicked(self, btn: Gtk.Button | None = None) -> None:
        """Switch to the settings view, lazily creating it on first access."""
        app = self.app
        app._save_current_cursor()
        if app.settings_view is None:
            has_encrypted = any(
                app.notes_manager.is_encrypted(n) for n in app.notes_manager.get_notes()
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
                    "show_toc": app.cfg.get("show_toc", True),
                    "lock_timeout_minutes": app.cfg.get("lock_timeout_minutes", 5),
                    "font_family": app.cfg.get("font_family"),
                    "font_size": app.cfg.get("font_size"),
                    "language": app.cfg.get("language", "en"),
                    "spell_check_enabled": app.cfg.get("spell_check_enabled", True),
                    "spell_check_language": app.cfg.get("spell_check_language", "en"),
                    "embed_width": app.cfg.get("embed_width", 0),
                    "always_show_markdown": app.cfg.get("always_show_markdown", False),
                    "speech_enabled": app.cfg.get("speech_enabled", False),
                    "speech_language": app.cfg.get("speech_language"),
                    "speech_input_device": app.cfg.get("speech_input_device"),
                    "speech_model_cached": model_cached(),
                    "has_encrypted_notes": has_encrypted,
                    "git_available": app.git_controller.is_git_installed(),
                    "git_enabled": app.cfg.get("git_enabled", False),
                    "git_auto_commit": app.cfg.get("git_auto_commit", True),
                    "sort_order": app.cfg.get("sort_order", "last_modified"),
                    "telegram_bot_token": app.cfg.get("telegram_bot_token", ""),
                    "telegram_target_note": app.cfg.get(
                        "telegram_target_note", "Inbox"
                    ),
                    "telegram_separator": app.cfg.get("telegram_separator", False),
                    "telegram_prefix": app.cfg.get("telegram_prefix", ""),
                    "telegram_owner_id": app.cfg.get("telegram_owner_id", 0),
                    "all_notes": app.notes_manager.get_notes(),
                },
                on_change_password=app._show_password_change_dialog,
                on_set_password=app._show_setup_dialog,
                on_new_template=app._on_new_template,
                on_edit_template=app._on_edit_template,
                on_delete_template=app._on_delete_template,
                on_open_templates_folder=app._on_open_templates_folder,
                on_restore_builtins=app._on_restore_builtins,
                templates=templates,
                assets_dir=app.base_dir / "assets" / "settings",
                telegram_bot=app.telegram_bot,
            )
            app.content_stack.add_named(app.settings_view, "settings")
        else:
            has_encrypted = any(
                app.notes_manager.is_encrypted(n) for n in app.notes_manager.get_notes()
            )
            app.settings_view.refresh_privacy_state(has_encrypted)
            templates = app.template_manager.get_all_templates()
            app.settings_view.refresh_templates(templates)

        app.content_stack.set_visible_child_name("settings")
        self.update_header_ui(tr("Settings"), is_editor=False)
        app.sidebar.set_active_view("settings")
        app._set_backlinks_visible(False)

    # Flashcards

    def on_flashcard_clicked(self) -> None:
        """Switch to the flashcard review view, lazily creating it on first access."""
        app = self.app
        app._save_current_cursor()
        if app.flashcard_view is None:
            app.flashcard_view = FlashcardReview(
                get_notes_fn=app.notes_manager.get_notes,
                read_fn=lambda n: app.notes_manager.read_plain(n) or "",
                assets_dir=app.base_dir / "assets",
                on_note_selected=app.lifecycle.on_link_clicked,
            )
            app.content_stack.add_named(app.flashcard_view, "flashcard")
        app.flashcard_view.refresh()
        app.content_stack.set_visible_child_name("flashcard")
        self.update_header_ui(tr("Flashcards"), is_editor=False)
        app.sidebar.set_active_view("flashcard")
        app._set_backlinks_visible(False)

    # Escape / back

    def on_escape_shortcut(self) -> bool:
        """Return to the editor from any secondary view, or clear search."""
        app = self.app
        current_page = app.content_stack.get_visible_child_name()
        if current_page in (
            "dashboard",
            "graph",
            "settings",
            "flashcard",
            "diagram",
            "table",
        ):
            if current_page == "diagram":
                app._on_diagram_close()
                return True
            if current_page == "table":
                app._on_table_close()
                return True
            target = "split_editor" if app.split_editor is not None else "editor"
            app.content_stack.set_visible_child_name(target)
            title = app.current_note if app.current_note else tr("Tokyo Notes")
            self.update_header_ui(title, is_editor=True)
            app.sidebar.set_active_view("editor")
            app._set_backlinks_visible(True)
            return True
        # Escape closes split view if active
        if app.split_editor is not None:
            app.split_editor._close_pane(app.split_editor._active_side)
            return True
        if app.sidebar.search_entry.has_focus():
            app.sidebar.search_entry.set_text("")
            # set_text does not emit search-changed, so refresh manually.
            app.refresh_list()
            if not is_entry_focused(app.win.get_focus()):
                app.text_view.grab_focus()
            return True
        return False

    # Header

    def update_header_ui(self, title: str, is_editor: bool = True) -> None:
        """Update the content-area header bar title and button visibility.

        If *title* contains ``/``, it is displayed as a breadcrumb (``a > b > c``).
        """
        app = self.app
        display = title.replace("/", "  \u203a  ") if "/" in title else title
        if is_editor:
            app.content_title.set_label(display)
            app.back_btn.set_visible(False)
        else:
            app.content_title.set_markup(f"<b>{GLib.markup_escape_text(display)}</b>")
            app.back_btn.set_visible(True)
