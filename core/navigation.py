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
from core.utils import is_entry_focused
from ui.dashboard import Dashboard
from ui.flashcard_review import FlashcardReview
from ui.graph_view import GraphView
from ui.media_gallery import MediaGalleryView
from ui.pdf_reader import PdfReaderView
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
        """Switch to the widget-based dashboard view, lazily creating it."""
        app = self.app
        app._save_current_cursor()
        if app.dashboard_view is None:
            app.dashboard_view = Dashboard(app)
            app.content_stack.add_named(app.dashboard_view, "dashboard")

        app.content_stack.set_visible_child_name("dashboard")
        self.update_header_ui(tr("Dashboard"), is_editor=False)
        app.sidebar.set_active_view("dashboard")
        app._set_backlinks_visible(False)

    def refresh_dashboard(self, filter_type: str | None = None) -> None:
        """Refresh tasks, preserving its active view unless one is specified."""
        if self.app.dashboard_view is None:
            return
        tasks_w = self.app.dashboard_view.get_widget("tasks")
        if tasks_w is not None:
            tasks_w._refresh(filter_type or tasks_w.active_filter)

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

    @staticmethod
    def _compute_default_filter(
        unchecked: list[dict[str, Any]], *, start_week_on_sunday: bool = True
    ) -> str:
        """Return the most relevant dashboard filter for the current unchecked tasks."""
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
                    "telegram_voice_emoji": app.cfg.get("telegram_voice_emoji", True),
                    "telegram_owner_id": app.cfg.get("telegram_owner_id", 0),
                    "assistant_enabled": app.cfg.get("assistant_enabled", False),
                    "llama_cpp_url": app.cfg.get("llama_cpp_url"),
                    "llama_cpp_api_key": app.cfg.get("llama_cpp_api_key", ""),
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
            app.settings_view.refresh_target_notes(app.notes_manager.get_notes())

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

    # Media gallery

    def on_media_clicked(self) -> None:
        app = self.app
        app._save_current_cursor()
        if app.media_gallery_view is None:
            app.media_gallery_view = MediaGalleryView(
                notes_dir=app.notes_manager.notes_dir,
                get_notes=app.notes_manager.get_notes,
                read_note=app.notes_manager.read_plain,
                readable_notes=app._media_readable_notes,
                on_open_pdf=self.on_pdf_reader_clicked,
                on_open_diagram=app._on_open_diagram_action,
                on_go_to_note=app._open_note_from_media,
                on_return_to_note=self.on_escape_shortcut,
                on_refresh=app.refresh_list,
            )
            app.content_stack.add_named(app.media_gallery_view, "media")
        app.media_gallery_view.refresh()
        app.content_stack.set_visible_child_name("media")
        self.update_header_ui(tr("Media"), is_editor=False)
        app.sidebar.set_active_view("media")
        app._set_backlinks_visible(False)

    # PDF reader

    def on_pdf_reader_clicked(
        self, path_or_url: str, note_name: str | None = None
    ) -> None:
        """Open the dedicated PDF reader for the given source."""
        app = self.app
        app._save_current_cursor()
        if app.pdf_reader_view is None:
            app.pdf_reader_view = PdfReaderView(app)
            app.content_stack.add_named(app.pdf_reader_view, "pdf_reader")
        source_note = note_name or app.current_note
        return_view = app.content_stack.get_visible_child_name() or "editor"
        app.pdf_reader_view.open_document(path_or_url, source_note, return_view)
        app.content_stack.set_visible_child_name("pdf_reader")
        self.update_header_ui(app.pdf_reader_view.get_title(), is_editor=False)
        app.sidebar.set_active_view("editor")
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
            "media",
            "pdf_reader",
            "diagram",
            "table",
        ):
            if current_page == "diagram":
                app._on_diagram_close()
                return True
            if current_page == "table":
                app._on_table_close()
                return True
            if current_page == "pdf_reader":
                if app.pdf_reader_view is not None:
                    target = app.pdf_reader_view._return_view
                    app.pdf_reader_view._flush_state()
                    app.content_stack.set_visible_child_name(target)
                    if hasattr(app.editor, "refresh_embeds"):
                        app.editor.refresh_embeds(
                            app_width=app.cfg.get("embed_width", 0)
                        )
                    title = app.current_note if app.current_note else tr("Tokyo Notes")
                    if target == "media":
                        self.update_header_ui(tr("Media"), is_editor=False)
                        app.sidebar.set_active_view("media")
                        app._set_backlinks_visible(False)
                    else:
                        self.update_header_ui(title, is_editor=True)
                        app.sidebar.set_active_view("editor")
                        app._set_backlinks_visible(True)
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
