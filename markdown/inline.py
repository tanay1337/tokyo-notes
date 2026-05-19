"""Markdown inline helpers."""
from __future__ import annotations

import re
from typing import Iterator

from markdown.ast import InlineSpan
from core.utils import (
    DEADLINE_RE,
    MD_LINK_CLICK_RE,
    TAG_RE,
    WIKI_CLICK_RE,
)

_PATTERNS = [
    (WIKI_CLICK_RE, "wiki"),
    (MD_LINK_CLICK_RE, None),  # handled specially (img vs link)
    (re.compile(r"<([^>]+)>"), "autolink"),
    (TAG_RE, "tag"),
    (DEADLINE_RE, "deadline"),
    (re.compile(r"\*\*([^*]+)\*\*"), "bold"),
    (re.compile(r"(?<!\*)\*([^*]+)\*"), "italic"),
    (re.compile(r"(?<!_)_([^_]+)_"), "italic"),
    (re.compile(r"`([^`]+)`"), "code"),
    (re.compile(r"~~([^~]+)~~"), "strike"),
]

def inline_spans(text: str) -> list[InlineSpan]:
    """Return all inline spans within *text*, sorted by start position."""
    spans: list[InlineSpan] = []
    for pat, kind in _PATTERNS:
        if kind is None:
            # markdown links / images
            for m in pat.finditer(text):
                is_img = bool(m.group(1))
                spans.append(
                    InlineSpan(
                        m.start(),
                        m.end(),
                        "img" if is_img else "link",
                        text=m.group(2),
                        href=m.group(3),
                    )
                )
        else:
            for m in pat.finditer(text):
                spans.append(
                    InlineSpan(m.start(), m.end(), kind, text=m.group(1))
                )
    spans.sort(key=lambda s: s.start)
    return spans
