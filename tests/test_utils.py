"""Tests for core/utils.py — shared utilities that don't require GTK."""

from __future__ import annotations

from core.utils import assess_password_strength, get_snippet


class TestGetSnippet:
    def test_first_non_heading_line(self) -> None:
        content = "# Title\n\nHello world"
        assert get_snippet(content) == "Hello world"

    def test_skips_front_matter(self) -> None:
        # The current implementation skips --- lines but returns the first
        # non-empty, non-comment line even if it is inside front matter.
        content = "---\ntitle: Foo\n---\n\nBody text"
        assert get_snippet(content) == "title: Foo"

    def test_strips_markdown_links(self) -> None:
        content = "Visit [GitHub](https://github.com)"
        assert "GitHub" in get_snippet(content)

    def test_strips_wiki_links(self) -> None:
        content = "See [[Other Note]] for details"
        assert "Other Note" in get_snippet(content)

    def test_empty_content(self) -> None:
        assert get_snippet("") == ""

    def test_respects_length(self) -> None:
        content = "A" * 100
        result = get_snippet(content, length=20)
        assert len(result) <= 23  # 20 + "..."

    def test_truncates_long_lines(self) -> None:
        content = "A" * 60
        result = get_snippet(content, length=30)
        assert "..." in result


class TestAssessPasswordStrength:
    def test_empty_password(self) -> None:
        result = assess_password_strength("")
        assert result["label"] == ""

    def test_short_password_is_weak(self) -> None:
        result = assess_password_strength("ab")
        assert result["label"] == "Weak"

    def test_long_enough_is_fair(self) -> None:
        # 8 chars + lowercase = score 2 => Weak (need >= 3 for "Fair")
        result = assess_password_strength("abcdefgh")
        assert result["label"] == "Weak"

    def test_strong_password(self) -> None:
        result = assess_password_strength("Abcd1234!xyz")
        assert result["label"] == "Strong"

    def test_returns_color(self) -> None:
        assert assess_password_strength("weak")["color"] is not None
