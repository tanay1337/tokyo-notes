"""Tests for core/md_clean.py — markdown document cleaner."""

from __future__ import annotations

from core.md_clean import cleanup_document


class TestTrailingWhitespace:
    def test_stripped(self) -> None:
        result = cleanup_document("line 1   \nline 2 \n")
        assert result == "line 1\nline 2\n"

    def test_no_whitespace(self) -> None:
        result = cleanup_document("line 1\nline 2\n")
        assert result == "line 1\nline 2\n"


class TestFinalNewline:
    def test_added(self) -> None:
        result = cleanup_document("hello")
        assert result == "hello\n"

    def test_already_present(self) -> None:
        result = cleanup_document("hello\n")
        assert result == "hello\n"

    def test_empty(self) -> None:
        assert cleanup_document("") == ""


class TestConsecutiveBlanks:
    def test_triple_collapsed(self) -> None:
        result = cleanup_document("a\n\n\n\nb\n")
        assert result == "a\n\n\nb\n"

    def test_double_preserved(self) -> None:
        result = cleanup_document("a\n\n\nb\n")
        assert result == "a\n\n\nb\n"

    def test_single_preserved(self) -> None:
        result = cleanup_document("a\n\nb\n")
        assert result == "a\n\nb\n"


class TestHeadingSpace:
    def test_missing_space(self) -> None:
        result = cleanup_document("#Title\n")
        assert result == "# Title\n"

    def test_h2_missing_space(self) -> None:
        result = cleanup_document("##Title\n")
        assert result == "## Title\n"

    def test_existing_space_unchanged(self) -> None:
        result = cleanup_document("# Title\n")
        assert result == "# Title\n"

    def test_not_a_heading(self) -> None:
        content = "#Not aheading but just text\n"
        result = cleanup_document(content)
        assert result == "# Not aheading but just text\n"

    def test_inside_code_fence_untouched(self) -> None:
        content = "```\n#not a heading\n```\n"
        result = cleanup_document(content)
        assert result == "```\n#not a heading\n```\n"


class TestUlMarker:
    def test_star_to_dash(self) -> None:
        result = cleanup_document("* item\n")
        assert result == "- item\n"

    def test_plus_to_dash(self) -> None:
        result = cleanup_document("+ item\n")
        assert result == "- item\n"

    def test_dash_unchanged(self) -> None:
        result = cleanup_document("- item\n")
        assert result == "- item\n"

    def test_indented_star(self) -> None:
        result = cleanup_document("  * item\n")
        assert result == "  - item\n"

    def test_inside_code_fence_untouched(self) -> None:
        content = "```\n* not a list\n```\n"
        result = cleanup_document(content)
        assert result == "```\n* not a list\n```\n"


class TestCheckboxFormat:
    def test_upper_x_to_lower(self) -> None:
        result = cleanup_document("- [X] done\n")
        assert result == "- [x] done\n"

    def test_lower_x_unchanged(self) -> None:
        result = cleanup_document("- [x] done\n")
        assert result == "- [x] done\n"

    def test_unchecked_unchanged(self) -> None:
        result = cleanup_document("- [ ] todo\n")
        assert result == "- [ ] todo\n"

    def test_indented_upper_x(self) -> None:
        result = cleanup_document("  - [X] done\n")
        assert result == "  - [x] done\n"

    def test_inside_code_fence_untouched(self) -> None:
        content = "```\n- [X] not a real task\n```\n"
        result = cleanup_document(content)
        assert result == "```\n- [X] not a real task\n```\n"


class TestHeadingMultiSpace:
    def test_double_space_collapsed(self) -> None:
        result = cleanup_document("#  Title\n")
        assert result == "# Title\n"

    def test_triple_space_collapsed(self) -> None:
        result = cleanup_document("###   Title\n")
        assert result == "### Title\n"

    def test_single_space_unchanged(self) -> None:
        result = cleanup_document("# Title\n")
        assert result == "# Title\n"


class TestHrStyle:
    def test_asterisks_to_dash(self) -> None:
        result = cleanup_document("***\n")
        assert result == "---\n"

    def test_underscores_to_dash(self) -> None:
        result = cleanup_document("___\n")
        assert result == "---\n"

    def test_dash_unchanged(self) -> None:
        result = cleanup_document("---\n")
        assert result == "---\n"

    def test_indented_asterisks(self) -> None:
        result = cleanup_document("  ***\n")
        assert result == "---\n"

    def test_inside_code_fence_untouched(self) -> None:
        content = "```\n***\n```\n"
        result = cleanup_document(content)
        assert result == "```\n***\n```\n"


class TestHeadingBlanks:
    def test_blank_after_before_text(self) -> None:
        result = cleanup_document("# Title\nbody\n")
        assert result == "# Title\n\nbody\n"

    def test_blank_before_after_text(self) -> None:
        result = cleanup_document("text\n# Title\n")
        assert result == "text\n\n# Title\n"

    def test_blanks_both_sides(self) -> None:
        result = cleanup_document("text\n# Title\nbody\n")
        assert result == "text\n\n# Title\n\nbody\n"

    def test_no_blank_between_headings(self) -> None:
        result = cleanup_document("# Title\n## Sub\n")
        assert result == "# Title\n## Sub\n"

    def test_no_blank_at_start(self) -> None:
        result = cleanup_document("# Title\n\nbody\n")
        assert result == "# Title\n\nbody\n"

    def test_existing_blank_preserved(self) -> None:
        result = cleanup_document("text\n\n# Title\n\nbody\n")
        assert result == "text\n\n# Title\n\nbody\n"


class TestFenceBlanks:
    def test_blank_before_fence(self) -> None:
        result = cleanup_document("text\n```\ncode\n```\n")
        assert result == "text\n\n```\ncode\n```\n"

    def test_blank_after_fence(self) -> None:
        result = cleanup_document("```\ncode\n```\ntext\n")
        assert result == "```\ncode\n```\n\ntext\n"

    def test_existing_blanks_preserved(self) -> None:
        result = cleanup_document("text\n\n```\ncode\n```\n\ntext\n")
        assert result == "text\n\n```\ncode\n```\n\ntext\n"

    def test_fence_at_start_no_blank_before(self) -> None:
        result = cleanup_document("```\ncode\n```\n")
        assert result == "```\ncode\n```\n"


class TestListBlanks:
    def test_blank_before_list(self) -> None:
        result = cleanup_document("text\n- a\n- b\n")
        assert result == "text\n\n- a\n- b\n"

    def test_blank_after_list(self) -> None:
        result = cleanup_document("- a\n- b\ntext\n")
        assert result == "- a\n- b\n\ntext\n"

    def test_blanks_both_sides(self) -> None:
        result = cleanup_document("text\n- a\n- b\nmore\n")
        assert result == "text\n\n- a\n- b\n\nmore\n"

    def test_list_at_start_no_blank_before(self) -> None:
        result = cleanup_document("- a\n- b\n")
        assert result == "- a\n- b\n"

    def test_existing_blanks_preserved(self) -> None:
        result = cleanup_document("text\n\n- a\n- b\n\nmore\n")
        assert result == "text\n\n- a\n- b\n\nmore\n"


class TestTableBlanks:
    def test_blank_before_table(self) -> None:
        result = cleanup_document("text\n| a | b |\n|---|---|\n")
        assert result == "text\n\n| a | b |\n|---|---|\n"

    def test_blank_after_table(self) -> None:
        result = cleanup_document("| a | b |\n|---|---|\ntext\n")
        assert result == "| a | b |\n|---|---|\n\ntext\n"

    def test_table_at_start_no_blank_before(self) -> None:
        result = cleanup_document("| a | b |\n|---|---|\n")
        assert result == "| a | b |\n|---|---|\n"


class TestFrontMatterSpacing:
    def test_blank_inserted_after_fm(self) -> None:
        content = "---\ntitle: Foo\n---\nbody\n"
        result = cleanup_document(content)
        assert result == "---\ntitle: Foo\n---\n\nbody\n"

    def test_existing_blank_preserved(self) -> None:
        content = "---\ntitle: Foo\n---\n\nbody\n"
        result = cleanup_document(content)
        assert result == "---\ntitle: Foo\n---\n\nbody\n"

    def test_no_front_matter_untouched(self) -> None:
        result = cleanup_document("body\n")
        assert result == "body\n"


class TestEdgeCases:
    def test_empty_document(self) -> None:
        assert cleanup_document("") == ""

    def test_only_blanks(self) -> None:
        result = cleanup_document("\n\n\n\n")
        assert result == "\n"

    def test_only_front_matter(self) -> None:
        content = "---\ntitle: Foo\n---\n"
        result = cleanup_document(content)
        assert result == "---\ntitle: Foo\n---\n"

    def test_flashcard_fence_untouched(self) -> None:
        content = "```flashcard\nQ\n---\nA\n```\n"
        result = cleanup_document(content)
        assert result == "```flashcard\nQ\n---\nA\n```\n"

    def test_table_untouched(self) -> None:
        content = "| a | b |\n|---|---|\n| 1 | 2 |\n"
        result = cleanup_document(content)
        assert result == "| a | b |\n|---|---|\n| 1 | 2 |\n"
