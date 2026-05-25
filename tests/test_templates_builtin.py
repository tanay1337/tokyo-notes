"""Tests for built-in templates."""

from __future__ import annotations

from core.templates_builtin import BUILTIN_TEMPLATES


def test_builtin_templates_is_dict() -> None:
    assert isinstance(BUILTIN_TEMPLATES, dict)


def test_daily_journal_exists() -> None:
    assert "daily-journal" in BUILTIN_TEMPLATES


def test_daily_journal_name() -> None:
    assert BUILTIN_TEMPLATES["daily-journal"]["name"] == "Daily Journal"


def test_templates_have_content() -> None:
    for name, tmpl in BUILTIN_TEMPLATES.items():
        assert tmpl.get("content"), f"Template {name!r} has empty content"
