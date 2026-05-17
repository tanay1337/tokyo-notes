"""Markdown inline helpers."""
from __future__ import annotations

import re
from typing import Iterator

from markdown.ast import InlineSpan

_PATTERNS = [
    (re.compile(r"\[\[([^\]]+)\]\]"), "wiki"),
    (re.compile(r"(!?)\[([^\]]+)\]\(([^)]+)\)"), None),  # handled specially
    (re.compile(r"<([^>]+)>"), "autolink"),
    (re.compile(r"(?<!\w)#(\w+)"), "tag"),
    (re.compile(r"@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)"), "deadline"),
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
