"""Tests for built-in template files."""

from __future__ import annotations

from pathlib import Path

BUILTIN_DIR = Path(__file__).parent.parent / "core" / "templates"


def test_builtins_dir_exists() -> None:
    assert BUILTIN_DIR.exists()


def test_daily_journal_exists() -> None:
    assert (BUILTIN_DIR / "daily-journal.md").exists()


def test_templates_have_content() -> None:
    for path in sorted(BUILTIN_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        assert content, f"Built-in template {path.name!r} is empty"
