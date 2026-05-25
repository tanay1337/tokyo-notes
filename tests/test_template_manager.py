"""Tests for core/template_manager.py — template CRUD and variable substitution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.template_manager import TemplateManager


def _make_app(tmp_path):
    app = MagicMock()
    app.notes_folder = str(tmp_path)
    return app


class TestTemplateManagerProvision:
    def test_creates_templates_dir(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        tm._ensure_templates_dir()
        assert (tmp_path / ".templates").exists()

    def test_provisions_builtins(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        tm._ensure_templates_dir()
        templates_dir = tmp_path / ".templates"
        files = list(templates_dir.glob("*.md"))
        assert len(files) >= 1

    def test_does_not_overwrite_existing(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        # Pre-create a template with custom content
        templates_dir = tmp_path / ".templates"
        templates_dir.mkdir(parents=True)
        (templates_dir / "daily-journal.md").write_text("custom", encoding="utf-8")
        tm._ensure_templates_dir()
        content = (templates_dir / "daily-journal.md").read_text(encoding="utf-8")
        assert content == "custom"  # not overwritten


class TestTemplateManagerCRUD:
    def test_get_all_templates(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        all_t = tm.get_all_templates()
        assert isinstance(all_t, list)
        assert len(all_t) >= 1
        for t in all_t:
            assert "slug" in t
            assert "content" in t

    def test_get_template_content_exists(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        content = tm.get_template_content("daily-journal")
        assert content is not None
        assert "{{today}}" in content

    def test_get_template_content_missing(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        assert tm.get_template_content("nonexistent") is None

    def test_save_and_read_user_template(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        slug = tm.save_as_template("My Template", "Custom content")
        assert slug == "my-template"
        assert tm.get_template_content(slug) == "Custom content"

    def test_save_as_template_does_not_overwrite_existing_slug(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        first = tm.save_as_template("Duplicate", "first")
        second = tm.save_as_template("Duplicate", "second")

        assert first == "duplicate"
        assert second == "duplicate-1"
        assert tm.get_template_content(first) == "first"
        assert tm.get_template_content(second) == "second"

    def test_delete_template(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        slug = tm.save_as_template("Temp", "content")
        assert tm.delete_template(slug) is True
        assert tm.get_template_content(slug) is None

    def test_delete_nonexistent(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        assert tm.delete_template("ghost") is False

    def test_update_template(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        slug = tm.save_as_template("Updatable", "original")
        assert tm.update_template(slug, "updated")
        assert tm.get_template_content(slug) == "updated"

    def test_update_nonexistent(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        assert tm.update_template("ghost", "data") is False

    def test_rejects_path_traversal_slug(self, tmp_path):
        tm = TemplateManager(_make_app(tmp_path))
        with pytest.raises(ValueError):
            tm.get_template_content("../outside")
        with pytest.raises(ValueError):
            tm.update_template("nested/path", "data")
        with pytest.raises(ValueError):
            tm.delete_template("")


class TestTemplateSubstitute:
    def test_substitute_today(self):
        import datetime

        result = TemplateManager.substitute_variables("Date: {{today}}")
        assert datetime.date.today().isoformat() in result

    def test_substitute_now(self):
        result = TemplateManager.substitute_variables("Now: {{now}}")
        assert len(result) > 5

    def test_substitute_weekday(self):
        import datetime

        result = TemplateManager.substitute_variables("Day: {{weekday}}")
        assert datetime.date.today().strftime("%A") in result

    def test_substitute_multiple(self):
        result = TemplateManager.substitute_variables("{{today}} {{time}}")
        assert len(result.split()) == 2

    def test_unknown_variable_left_untouched(self):
        result = TemplateManager.substitute_variables("{{unknown}}")
        assert "{{unknown}}" in result


class TestSlug:
    def test_make_slug_basic(self):
        assert TemplateManager._make_slug("Hello World") == "hello-world"

    def test_make_slug_strips_special(self):
        assert TemplateManager._make_slug("My Note!@#") == "my-note"

    def test_make_slug_empty_fallback(self):
        assert TemplateManager._make_slug("!@#$%") == "untitled"

    def test_make_slug_strips_whitespace(self):
        assert TemplateManager._make_slug("  Hello  ") == "hello"
