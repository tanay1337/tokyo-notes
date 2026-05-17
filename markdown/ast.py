"""Markdown AST nodes and constants."""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ("MdLine", "InlineSpan")


@dataclass(slots=True, frozen=True)
class MdLine:
    """A parsed markdown line."""

    raw: str
    kind: str
    indent: int = 0
    level: int = 0
    text: str = ""
    marker: str = ""
    meta: dict | None = None

    @classmethod
    def blank(cls, raw: str = "") -> MdLine:
        return cls(raw=raw, kind="blank")


@dataclass(slots=True, frozen=True)
class InlineSpan:
    start: int
    end: int
    kind: str
    text: str = ""
    href: str = ""
