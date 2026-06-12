"""Tests for the markdown tokenizer."""

from __future__ import annotations

import pytest

from markdown.tokenizer import LineTokenizer


def test_blank_line() -> None:
    tok = LineTokenizer()
    md, fence = tok.tokenize("")
    assert md.kind == "blank"
    assert not fence


def test_blank_line_whitespace() -> None:
    tok = LineTokenizer()
    md, fence = tok.tokenize("   ")
    assert md.kind == "blank"
    assert not fence


def test_blank_line_newline_stripped() -> None:
    tok = LineTokenizer()
    md, fence = tok.tokenize("\n")
    assert md.kind == "blank"
    assert not fence


class TestATXHeadings:
    def test_h1(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("# Title")
        assert md.kind == "h1"
        assert md.text == "Title"
        assert not fence

    def test_h2(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("## Section")
        assert md.kind == "h2"
        assert md.text == "Section"
        assert not fence

    def test_h6(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("###### Deep")
        assert md.kind == "h6"
        assert md.text == "Deep"
        assert not fence

    def test_heading_with_trailing_hash(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("# Title #")
        assert md.kind == "h1"
        assert md.text == "Title #"


class TestUnorderedLists:
    def test_ul_dash(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("- item")
        assert md.kind == "ul"
        assert md.marker == "-"
        assert md.text == "item"
        assert not fence

    def test_ul_star(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("* item")
        assert md.kind == "ul"
        assert md.marker == "*"
        assert md.text == "item"
        assert not fence

    def test_ul_plus(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("+ item")
        assert md.kind == "ul"
        assert md.marker == "+"
        assert md.text == "item"
        assert not fence

    def test_ul_indented(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("  - item")
        assert md.kind == "ul"
        assert md.indent == 2
        assert md.text == "item"
        assert not fence


class TestOrderedLists:
    def test_ol(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("1. item")
        assert md.kind == "ol"
        assert md.marker == "1. "
        assert md.text == "item"
        assert not fence

    def test_ol_indented(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("   1. item")
        assert md.kind == "ol"
        assert md.indent == 3
        assert md.text == "item"
        assert not fence

    def test_ol_high_number(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("42. answer")
        assert md.kind == "ol"
        assert md.marker == "42. "
        assert md.text == "answer"
        assert not fence


class TestTasks:
    def test_task_unchecked(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("- [ ] todo")
        assert md.kind == "task"
        assert md.marker == "-"
        assert md.text == "todo"
        assert md.meta == {"checked": False}
        assert not fence

    def test_task_checked_lower_x(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("- [x] done")
        assert md.kind == "task"
        assert md.meta == {"checked": True}

    def test_task_checked_upper_x(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("- [X] done")
        assert md.kind == "task"
        assert md.meta == {"checked": True}

    def test_task_indented(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("    - [ ] sub task")
        assert md.kind == "task"
        assert md.indent == 4
        assert md.text == "sub task"
        assert not fence


class TestCodeFences:
    def test_fence_start(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("```python")
        assert md.kind == "code_fence_start"
        assert md.marker == "python"
        assert fence

    def test_fence_start_no_lang(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("```")
        assert md.kind == "code_fence_start"
        assert md.marker == ""
        assert fence

    def test_fence_end(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("```", in_fence=True)
        assert md.kind == "code_fence_end"
        assert md.marker == ""
        assert not fence

    def test_code_block(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("console.log('hi')", in_fence=True)
        assert md.kind == "code_block"
        assert fence

    def test_code_block_empty(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("", in_fence=True)
        assert md.kind == "code_block"
        assert fence

    def test_heading_inside_fence_is_code_block(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("# not a heading", in_fence=True)
        assert md.kind == "code_block"
        assert fence

    def test_fence_language_with_dash(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("```mermaid-flow")
        assert md.kind == "code_fence_start"
        assert md.marker == "mermaid-flow"
        assert fence


class TestBlockquotes:
    def test_blockquote(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("> quoted text")
        assert md.kind == "blockquote"
        assert md.text == "quoted text"
        assert not fence

    def test_blockquote_nested(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("> > nested")
        assert md.kind == "blockquote"
        assert md.text == "> nested"
        assert not fence


class TestHorizontalRules:
    @pytest.mark.parametrize("hr", ["---", "***", "___", " - - -", "* * *", "-----"])
    def test_hr(self, hr: str) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize(hr)
        assert md.kind == "hr"
        assert not fence


class TestTables:
    def test_table_row(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("| a | b |")
        assert md.kind == "table_row"
        assert not fence

    def test_table_sep(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("|---|---|")
        assert md.kind == "table_sep"
        assert not fence

    def test_table_sep_colons(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("| :--- | :---: | ---: |")
        assert md.kind == "table_sep"
        assert not fence


class TestPlainText:
    def test_text(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("Hello world")
        assert md.kind == "text"
        assert md.raw == "Hello world"
        assert not fence

    def test_text_with_numbers(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("abc 123 def")
        assert md.kind == "text"
        assert not fence

    def test_text_updates_prev_line(self) -> None:
        tok = LineTokenizer()
        md1, _ = tok.tokenize("first")
        assert md1.kind == "text"
        md2, _ = tok.tokenize("paragraph")
        assert md2.kind == "text"


class TestStateManagement:
    def test_fresh_tokenizer_no_prev(self) -> None:
        tok = LineTokenizer()
        assert tok._prev_line is None

    def test_prev_line_set_only_for_text(self) -> None:
        tok = LineTokenizer()
        tok.tokenize("# Heading")
        assert tok._prev_line is None
        tok.tokenize("paragraph")
        assert tok._prev_line is not None
        assert tok._prev_line.kind == "text"

    def test_blank_line_clears_prev(self) -> None:
        tok = LineTokenizer()
        tok.tokenize("text")
        tok.tokenize("")
        md, fence = tok.tokenize("---")
        assert md.kind == "hr"


class TestEdgeCases:
    def test_nested_list_not_task(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("  - [not a checkbox]")
        assert md.kind == "ul"
        assert md.text == "[not a checkbox]"

    def test_marker_star_not_hr(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("* ")
        assert md.kind == "ul"
        assert md.marker == "*"
        assert md.text == ""

    def test_three_dashes_not_hr_indented(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("  ---")
        assert md.kind == "hr"

    def test_fence_start_ignored_inside_fence(self) -> None:
        tok = LineTokenizer()
        md, fence = tok.tokenize("```", in_fence=True)
        assert md.kind == "code_fence_end"
        assert not fence
