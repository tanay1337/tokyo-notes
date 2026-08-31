"""Tests for clickable links in dashboard task text."""

from __future__ import annotations

from unittest.mock import MagicMock

from ui.widgets.tasks import TasksWidget, task_text_to_markup


def test_task_text_to_markup_renders_wiki_links() -> None:
    markup = task_text_to_markup("Read [[Project Plan]]")

    assert 'href="note:Project%20Plan"' in markup
    assert ">Project Plan</a>" in markup


def test_task_text_to_markup_renders_markdown_links() -> None:
    markup = task_text_to_markup("Open [site](https://example.com/path?a=1&b=2)")

    assert 'href="https://example.com/path?a=1&amp;b=2"' in markup
    assert ">site</a>" in markup


def test_task_text_to_markup_renders_bare_urls() -> None:
    url = "https://linkedin.com/name=tan_pan_hello"
    markup = task_text_to_markup(f"Follow up {url}")

    assert f'href="{url}"' in markup
    assert f">{url}</a>" in markup


def test_task_link_activation_opens_internal_note() -> None:
    widget = TasksWidget.__new__(TasksWidget)
    widget.app = MagicMock()

    assert widget._on_task_link_activated(MagicMock(), "note:Project%20Plan")

    widget.app.lifecycle.on_link_clicked.assert_called_once_with("Project Plan")


def test_task_link_activation_opens_safe_external_url(monkeypatch) -> None:
    opened: list[str] = []
    widget = TasksWidget.__new__(TasksWidget)
    widget.app = None

    monkeypatch.setattr("ui.widgets.tasks.webbrowser.open_new_tab", opened.append)

    assert widget._on_task_link_activated(MagicMock(), "https://example.com")
    assert opened == ["https://example.com"]


def test_task_link_activation_ignores_unsafe_external_url(monkeypatch) -> None:
    opened: list[str] = []
    widget = TasksWidget.__new__(TasksWidget)
    widget.app = None

    monkeypatch.setattr("ui.widgets.tasks.webbrowser.open_new_tab", opened.append)

    assert widget._on_task_link_activated(MagicMock(), "javascript:alert(1)")
    assert opened == []
