"""Tests for navigation decision logic."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import core.navigation as navigation
from core.navigation import NavigationController


class TestComputeDefaultFilter:
    def test_prefers_today_when_task_due_today(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(navigation.datetime, "date", FixedDate)

        result = NavigationController._compute_default_filter(
            [{"deadline": "2026-05-27 14:30"}],
            start_week_on_sunday=False,
        )

        assert result == "today"

    def test_uses_week_when_no_today_task_but_due_this_week(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(navigation.datetime, "date", FixedDate)

        result = NavigationController._compute_default_filter(
            [{"deadline": "2026-05-29"}],
            start_week_on_sunday=False,
        )

        assert result == "week"

    def test_uses_all_when_no_tasks_due_this_week(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(navigation.datetime, "date", FixedDate)

        result = NavigationController._compute_default_filter(
            [{"deadline": "2026-06-10"}, {"deadline": None}],
            start_week_on_sunday=False,
        )

        assert result == "all"


class TestDashboardRefresh:
    def test_preserves_active_task_filter_by_default(self) -> None:
        app = MagicMock()
        tasks = MagicMock()
        tasks.active_filter = "week"
        app.dashboard_view.get_widget.return_value = tasks

        NavigationController(app).refresh_dashboard()

        tasks._refresh.assert_called_once_with("week")

    def test_explicit_filter_still_overrides_active_filter(self) -> None:
        app = MagicMock()
        tasks = MagicMock()
        tasks.active_filter = "all"
        app.dashboard_view.get_widget.return_value = tasks

        NavigationController(app).refresh_dashboard("today")

        tasks._refresh.assert_called_once_with("today")


class TestPdfReaderNavigation:
    def test_opens_pdf_reader_view(self) -> None:
        app = MagicMock()
        app.current_note = "Research"
        app.content_stack.get_visible_child_name.return_value = "editor"
        app.content_stack.add_named = MagicMock()
        app.content_stack.set_visible_child_name = MagicMock()
        app.sidebar = MagicMock()
        app.sidebar.set_active_view = MagicMock()
        app._save_current_cursor = MagicMock()
        app._set_backlinks_visible = MagicMock()
        app.pdf_reader_view = None

        reader = MagicMock()
        reader.get_title.return_value = "PDF: paper.pdf"

        with patch("core.navigation.PdfReaderView", return_value=reader):
            nav = NavigationController(app)
            nav.on_pdf_reader_clicked("docs/paper.pdf")

        app._save_current_cursor.assert_called_once()
        app.content_stack.add_named.assert_called_once_with(reader, "pdf_reader")
        reader.open_document.assert_called_once_with(
            "docs/paper.pdf", "Research", "editor"
        )
        app.content_stack.set_visible_child_name.assert_called_once_with("pdf_reader")
        app.sidebar.set_active_view.assert_called_once_with("editor")
        app._set_backlinks_visible.assert_called_once_with(False)

    def test_escape_from_pdf_reader_returns_to_origin_view(self) -> None:
        app = MagicMock()
        app.current_note = "Research"
        app.content_stack.get_visible_child_name.return_value = "pdf_reader"
        app.sidebar = MagicMock()
        app.sidebar.set_active_view = MagicMock()
        app._set_backlinks_visible = MagicMock()
        app.split_editor = None
        app.pdf_reader_view = MagicMock()
        app.pdf_reader_view._return_view = "editor"
        app.editor = MagicMock()
        app.editor.refresh_embeds = MagicMock()
        app.cfg = MagicMock()
        app.cfg.get.return_value = 0

        nav = NavigationController(app)

        assert nav.on_escape_shortcut() is True
        app.pdf_reader_view._flush_state.assert_called_once()
        app.content_stack.set_visible_child_name.assert_called_once_with("editor")
        app.editor.refresh_embeds.assert_called_once_with(app_width=0)
        app.sidebar.set_active_view.assert_called_once_with("editor")
        app._set_backlinks_visible.assert_called_once_with(True)
