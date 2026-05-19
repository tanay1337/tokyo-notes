"""Markdown tokenizer — pure Python, no GTK."""
from __future__ import annotations

import re
from typing import Iterator

from markdown.ast import MdLine
from core.utils import (
    BLOCKQUOTE_RE as _BLOCKQUOTE,
    DEADLINE_RE as _DEADLINE,
    HR_RE as _HR,
    MD_LINK_CLICK_RE as _MD_LINK,
    TAG_RE as _TAG,
    TABLE_ROW_RE as _TABLE_ROW,
    TABLE_SEP_RE as _TABLE_SEP,
    WIKI_CLICK_RE as _WIKI,
)

# Structural patterns whose capturing groups differ from core/utils versions
# (tokenizer needs different groups for prefix extraction).
_HEADER_ATX = re.compile(r"^(#{1,6})\s+(.*)$")
_HEADER_SETEXT_UNDER = re.compile(r"^(={2,}|-{2,})\s*$")
_LIST_UL = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_LIST_OL = re.compile(r"^(\s*)(\d+\.\s+)(.*)$")
_CODE_FENCE = re.compile(r"^```(\w*)$")
_CHECKBOX = re.compile(r"^(\s*-\s*\[([ xX])\]\s*)(.*)$")

# Inline bold/italic/code/strike — not in core.utils (PDF-only there)
_INLINE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_INLINE_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*")
_INLINE_ITALIC2 = re.compile(r"(?<!_)_([^_]+)_")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_INLINE_STRIKE = re.compile(r"~~([^~]+)~~")


class LineTokenizer:
    """Converts a single markdown line into an ``MdLine``."""

    __slots__ = ("_prev_line",)

    def __init__(self) -> None:
        self._prev_line: MdLine | None = None

    def tokenize(self, line: str, in_fence: bool = False) -> tuple[MdLine, bool]:
        stripped = line.rstrip("\n\r")
        if not stripped or not stripped.strip():
            return MdLine.blank(stripped), False

        if in_fence:
            m = _CODE_FENCE.match(stripped)
            if m:
                return MdLine(stripped, "code_fence_end", marker=m.group(1)), False
            return MdLine(stripped, "code_block"), True

        m = _CODE_FENCE.match(stripped)
        if m:
            return MdLine(stripped, "code_fence_start", marker=m.group(1)), True

        ol = _LIST_OL.match(stripped)
        if ol:
            indent, marker, text = ol.group(1), ol.group(2), ol.group(3)
            return MdLine(stripped, "ol", indent=len(indent), marker=marker, text=text), False

        ul = _LIST_UL.match(stripped)
        if ul:
            indent, marker, text = ul.group(1), ul.group(2), ul.group(3)
            cb = _CHECKBOX.match(stripped)
            if cb:
                checked = cb.group(2).lower() == "x"
                return MdLine(stripped, "task", indent=len(indent), marker=marker, text=text, meta={"checked": checked}), False
            return MdLine(stripped, "ul", indent=len(indent), marker=marker, text=text), False

        if self._prev_line and self._prev_line.kind == "text" and _HEADER_SETEXT_UNDER.match(stripped):
            return MdLine(stripped, "setext_under", marker=stripped[0], text=self._prev_line.text), False

        if _HR.match(stripped):
            return MdLine(stripped, "hr"), False

        bq = _BLOCKQUOTE.match(stripped)
        if bq:
            return MdLine(stripped, "blockquote", text=bq.group(2)), False

        if _TABLE_ROW.match(stripped):
            if _TABLE_SEP.match(stripped):
                return MdLine(stripped, "table_sep"), False
            return MdLine(stripped, "table_row"), False

        h = _HEADER_ATX.match(stripped)
        if h:
            level = len(h.group(1))
            return MdLine(stripped, "h" + str(min(level, 4)), text=h.group(2)), False

        md = MdLine(stripped, "text"), False
        self._prev_line = md[0]
        return md


class DocumentTokenizer:
    """High-level helper that tokenises a list of lines."""

    __slots__ = ("_tokenizer",)

    def __init__(self) -> None:
        self._tokenizer = LineTokenizer()

    def tokenize(self, lines: list[str]) -> list[MdLine]:
        # Reset per-line state so successive calls don't bleed setext-heading
        # detection across document boundaries.
        self._tokenizer._prev_line = None
        in_fenced = False
        ok: list[MdLine] = []
        for line in lines:
            md, in_fenced = self._tokenizer.tokenize(line, in_fence=in_fenced)
            ok.append(md)
        return ok
