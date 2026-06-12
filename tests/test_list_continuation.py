"""Tests for ordered list auto-continuation helpers (alpha, roman, arabic)."""

from __future__ import annotations

import re

from ui.editor import (
    _CONTINUATION_PATTERNS,
    _ORDERED_SCHEMES,
    _ORDERED_START_MARKERS,
    _get_list_info,
    _increment_alpha,
    _increment_roman,
    _int_to_roman,
    _roman_to_int,
)


class TestAlphaIncrement:
    def test_a_to_b(self) -> None:
        assert _increment_alpha("a") == "b"

    def test_y_to_z(self) -> None:
        assert _increment_alpha("y") == "z"

    def test_z_stays_z(self) -> None:
        assert _increment_alpha("z") == "z"

    def test_uppercase(self) -> None:
        assert _increment_alpha("A") == "B"
        assert _increment_alpha("Y") == "Z"
        assert _increment_alpha("Z") == "Z"

    def test_lowercase_only(self) -> None:
        assert _increment_alpha("a").islower()


class TestRomanConversion:
    def test_basic_values(self) -> None:
        cases = [
            ("i", 1),
            ("ii", 2),
            ("iii", 3),
            ("iv", 4),
            ("v", 5),
            ("vi", 6),
            ("vii", 7),
            ("viii", 8),
            ("ix", 9),
            ("x", 10),
            ("xl", 40),
            ("l", 50),
            ("xc", 90),
            ("c", 100),
            ("cd", 400),
            ("d", 500),
            ("cm", 900),
            ("m", 1000),
            ("mix", 1009),
            ("mmxxvi", 2026),
        ]
        for roman, expected in cases:
            assert _roman_to_int(roman) == expected, f"to_int({roman})"
            assert _int_to_roman(expected) == roman, f"to_roman({expected})"

    def test_invalid_returns_none(self) -> None:
        assert _roman_to_int("iiii") is None
        assert _roman_to_int("vx") is None
        assert _roman_to_int("abc") is None
        assert _roman_to_int("") is None
        assert _roman_to_int("vl") is None
        assert _roman_to_int("ic") is None

    def test_uppercase_is_not_accepted(self) -> None:
        # _roman_to_int expects lowercase; uppercase handled at call site.
        assert _roman_to_int("IV") is None


class TestRomanIncrement:
    def test_basic_increments(self) -> None:
        cases = [
            ("i", "ii"),
            ("ii", "iii"),
            ("iii", "iv"),
            ("iv", "v"),
            ("v", "vi"),
            ("vi", "vii"),
            ("vii", "viii"),
            ("viii", "ix"),
            ("ix", "x"),
            ("x", "xi"),
            ("xxxix", "xl"),
            ("xl", "xli"),
            ("xcix", "c"),
            ("cmxcix", "m"),
        ]
        for before, after in cases:
            assert _increment_roman(before) == after, f"{before} → {after}"

    def test_invalid_returns_none(self) -> None:
        assert _increment_roman("iiii") is None
        assert _increment_roman("vx") is None


class TestContinuationPatterns:
    """Verify regex patterns match / reject as expected."""

    def _pattern_for(self, kind: str) -> re.Pattern:
        for pat, k in _CONTINUATION_PATTERNS:
            if k == kind:
                return pat
        raise ValueError(f"no pattern for {kind}")

    def test_alpha_matches_lowercase(self) -> None:
        pat = self._pattern_for("ordered_alpha")
        m = pat.match("a. item")
        assert m is not None
        assert m.group(1) == "a."

    def test_alpha_matches_uppercase(self) -> None:
        pat = self._pattern_for("ordered_alpha")
        m = pat.match("B. item")
        assert m is not None
        assert m.group(1) == "B."

    def test_alpha_rejects_two_letters(self) -> None:
        pat = self._pattern_for("ordered_alpha")
        assert pat.match("ab. item") is None

    def test_roman_matches_lowercase(self) -> None:
        pat = self._pattern_for("ordered_roman")
        m = pat.match("ii. item")
        assert m is not None
        assert m.group(1) == "ii."

    def test_roman_matches_uppercase(self) -> None:
        pat = self._pattern_for("ordered_roman")
        m = pat.match("IV. item")
        assert m is not None
        assert m.group(1) == "IV."

    def test_roman_accepts_single_ivx(self) -> None:
        """i., v., x. (and uppercase) should match roman."""
        pat = self._pattern_for("ordered_roman")
        for ch in ("i", "v", "x", "I", "V", "X"):
            assert pat.match(f"{ch}. item") is not None, f"should match {ch}."

    def test_roman_rejects_single_c(self) -> None:
        """Single c/C are NOT valid roman (too large) — stay alpha."""
        pat = self._pattern_for("ordered_roman")
        for ch in ("c", "C"):
            assert pat.match(f"{ch}. item") is None, f"should not match {ch}."

    def test_roman_rejects_invalid_sequence(self) -> None:
        pat = self._pattern_for("ordered_roman")
        assert pat.match("iiii. item") is not None  # regex matches
        assert _increment_roman("iiii") is None  # but validation rejects

    def test_roman_rejects_natural_words(self) -> None:
        """Words like 'civil' look roman-ish but fail validation."""
        pat = self._pattern_for("ordered_roman")
        m = pat.match("civil. item")
        assert m is not None  # regex matches the pattern
        assert _increment_roman("civil") is None  # but validation fails

    def test_alpha_catches_single_c(self) -> None:
        """c. is not caught by roman — alpha handles it."""
        alpha_pat = self._pattern_for("ordered_alpha")
        roman_pat = self._pattern_for("ordered_roman")
        for ch in ("c", "C"):
            assert alpha_pat.match(f"{ch}. item") is not None
            assert roman_pat.match(f"{ch}. item") is None

    def test_i_goes_to_roman_not_alpha(self) -> None:
        """i. and I. should match roman first (order), not alpha."""
        kinds = [k for _, k in _CONTINUATION_PATTERNS]
        roman_idx = kinds.index("ordered_roman")
        alpha_idx = kinds.index("ordered_alpha")
        assert roman_idx < alpha_idx, "roman must be tried before alpha"

    def test_arabic_still_works(self) -> None:
        pat = self._pattern_for("ordered")
        m = pat.match("42. answer")
        assert m is not None
        assert m.group(1) == "42."

    def test_indented_alpha(self) -> None:
        pat = self._pattern_for("ordered_alpha")
        m = pat.match("   a. item")
        assert m is not None
        assert m.group(1) == "   a."

    def test_indented_roman(self) -> None:
        pat = self._pattern_for("ordered_roman")
        m = pat.match("    ii. item")
        assert m is not None
        assert m.group(1) == "    ii."

    def test_empty_marker_break(self) -> None:
        """Pressing Enter on a bare marker should be handled (not a pattern test)."""
        pat = self._pattern_for("ordered_alpha")
        m = pat.match("a. ")
        assert m is not None
        assert m.group(1) == "a."

    def test_ordered_list_order(self) -> None:
        """Roman before alpha, both after arabic."""
        kinds = [k for _, k in _CONTINUATION_PATTERNS]
        assert kinds.index("ordered") < kinds.index("ordered_roman")
        assert kinds.index("ordered_roman") < kinds.index("ordered_alpha")


class TestSchemeConstants:
    def test_three_schemes(self) -> None:
        assert _ORDERED_SCHEMES == ["ordered", "ordered_alpha", "ordered_roman"]

    def test_start_markers(self) -> None:
        assert _ORDERED_START_MARKERS == {
            "ordered": "1.",
            "ordered_alpha": "a.",
            "ordered_roman": "i.",
        }

    def test_cycling(self) -> None:
        n = len(_ORDERED_SCHEMES)
        assert _ORDERED_SCHEMES[0] == "ordered"
        assert _ORDERED_SCHEMES[1] == "ordered_alpha"
        assert _ORDERED_SCHEMES[2] == "ordered_roman"
        # cycle back
        assert _ORDERED_SCHEMES[3 % n] == "ordered"
        assert _ORDERED_SCHEMES[4 % n] == "ordered_alpha"
        assert _ORDERED_SCHEMES[5 % n] == "ordered_roman"


class TestGetListInfo:
    def test_ordered(self) -> None:
        info = _get_list_info("1. item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "ordered"
        assert indent == ""
        assert marker == "1."
        assert content == "item"

    def test_ordered_indented(self) -> None:
        info = _get_list_info("  1. item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "ordered"
        assert indent == "  "
        assert marker == "1."
        assert content == "item"

    def test_alpha(self) -> None:
        info = _get_list_info("a. item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "ordered_alpha"
        assert indent == ""
        assert marker == "a."
        assert content == "item"

    def test_alpha_indented(self) -> None:
        info = _get_list_info("    a. item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "ordered_alpha"
        assert indent == "    "
        assert marker == "a."
        assert content == "item"

    def test_roman(self) -> None:
        info = _get_list_info("ii. item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "ordered_roman"
        assert indent == ""
        assert marker == "ii."
        assert content == "item"

    def test_roman_indented(self) -> None:
        info = _get_list_info("      iii. item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "ordered_roman"
        assert indent == "      "
        assert marker == "iii."
        assert content == "item"

    def test_unordered(self) -> None:
        info = _get_list_info("- item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "list"
        assert indent == ""
        assert marker == "-"
        assert content == "item"

    def test_unordered_indented(self) -> None:
        info = _get_list_info("  - item")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "list"
        assert indent == "  "
        assert marker == "-"
        assert content == "item"

    def test_task(self) -> None:
        info = _get_list_info("- [ ] task")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "task"
        assert indent == ""
        assert marker == "- [ ]"
        assert content == "task"

    def test_task_indented(self) -> None:
        info = _get_list_info("    - [x] done")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "task"
        assert indent == "    "
        assert marker == "- [x]"
        assert content == "done"

    def test_not_a_list(self) -> None:
        assert _get_list_info("plain text") is None
        assert _get_list_info("# heading") is None
        assert _get_list_info("") is None
        assert _get_list_info("   ") is None

    def test_marker_only_no_content(self) -> None:
        """Lines with only the marker (no content)."""
        info = _get_list_info("a. ")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "ordered_alpha"
        assert indent == ""
        assert marker == "a."
        assert content == ""

    def test_marker_only_indented(self) -> None:
        info = _get_list_info("  - ")
        assert info is not None
        _, p_type, indent, marker, content = info
        assert p_type == "list"
        assert indent == "  "
        assert marker == "-"
        assert content == ""

    def test_star_bullet(self) -> None:
        info = _get_list_info("* item")
        assert info is not None
        _, p_type, _, _, content = info
        assert p_type == "list"
        assert content == "item"

    def test_plus_bullet(self) -> None:
        info = _get_list_info("+ item")
        assert info is not None
        _, p_type, _, _, content = info
        assert p_type == "list"
        assert content == "item"
