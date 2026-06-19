"""Markdown document cleaner — pure Python, no GTK."""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown.tokenizer import LineTokenizer

_HEADING_NOSPACE = re.compile(r"^(#{1,6})([^\s#].*)$")
_HEADING_MULTI_SPACE = re.compile(r"^(#{1,6}) {2,}(.*)$")
_UL_OTHER = re.compile(r"^(\s*)([*+])(\s+.*)$")
_CB_UPPER = re.compile(r"^(\s*-\s*\[)X(\].*)$")
_HR_OTHER = re.compile(r"^(\s*)[*_](\s*[*_]\s*){2,}$")
_FM_DELIM = re.compile(r"^---\s*$")


@dataclass
class _Line:
    raw: str
    kind: str
    in_fence: bool = False


def _classify(content: str) -> list[_Line]:
    tok = LineTokenizer()
    in_fence = False
    lines: list[_Line] = []
    for raw in content.split("\n"):
        if in_fence:
            md_line, still_in = tok.tokenize(raw, in_fence=True)
            lines.append(_Line(raw, md_line.kind, True))
            in_fence = still_in
        else:
            md_line, in_fence = tok.tokenize(raw)
            lines.append(_Line(raw, md_line.kind))
    return lines


def _detect_front_matter(lines: list[_Line]) -> tuple[int, int] | None:
    """Return (start, end) indices of front-matter block, or None."""
    if not lines or not _FM_DELIM.match(lines[0].raw):
        return None
    for i in range(1, len(lines)):
        if _FM_DELIM.match(lines[i].raw):
            return 0, i
    return None


def _as_text(lines: list[_Line]) -> str:
    return "\n".join(ln.raw for ln in lines)


# ── rules ──────────────────────────────────────────────────────────


def _strip_trailing_whitespace(lines: list[_Line]) -> list[_Line]:
    return [_Line(ln.raw.rstrip(), ln.kind, ln.in_fence) for ln in lines]


def _fix_heading_space(lines: list[_Line]) -> list[_Line]:
    result: list[_Line] = []
    for line in lines:
        if not line.in_fence:
            m = _HEADING_NOSPACE.match(line.raw)
            if m:
                line = _Line(
                    f"{m.group(1)} {m.group(2)}",
                    "h" + str(len(m.group(1))),
                    line.in_fence,
                )
            else:
                m = _HEADING_MULTI_SPACE.match(line.raw)
                if m:
                    line = _Line(
                        f"{m.group(1)} {m.group(2)}",
                        "h" + str(len(m.group(1))),
                        line.in_fence,
                    )
        result.append(line)
    return result


def _fix_ul_marker(lines: list[_Line]) -> list[_Line]:
    result: list[_Line] = []
    for line in lines:
        if not line.in_fence and line.kind in ("ul", "task"):
            m = _UL_OTHER.match(line.raw)
            if m:
                line = _Line(f"{m.group(1)}-{m.group(3)}", line.kind, line.in_fence)
        result.append(line)
    return result


def _fix_checkbox_format(lines: list[_Line]) -> list[_Line]:
    result: list[_Line] = []
    for line in lines:
        if not line.in_fence:
            m = _CB_UPPER.match(line.raw)
            if m:
                line = _Line(f"{m.group(1)}x{m.group(2)}", line.kind, line.in_fence)
        result.append(line)
    return result


def _fix_hr_style(lines: list[_Line]) -> list[_Line]:
    result: list[_Line] = []
    for line in lines:
        if not line.in_fence and line.kind == "hr":
            if _HR_OTHER.match(line.raw):
                line = _Line("---", line.kind, line.in_fence)
        result.append(line)
    return result


def _add_heading_blanks(lines: list[_Line]) -> list[_Line]:
    _HEADING_KINDS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    result: list[_Line] = []
    for i, line in enumerate(lines):
        if not line.in_fence and line.kind in _HEADING_KINDS:
            if result:
                prev = result[-1]
                if (
                    prev.kind not in ("blank", "code_fence_end")
                    and prev.kind not in _HEADING_KINDS
                    and not prev.in_fence
                ):
                    result.append(_Line("", "blank"))
            result.append(line)
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if nxt.kind != "blank" and nxt.kind not in _HEADING_KINDS:
                    if not (nxt.in_fence and nxt.kind == "code_fence_start"):
                        result.append(_Line("", "blank"))
        else:
            result.append(line)
    return result


def _add_blanks_around_block(
    lines: list[_Line],
    region_kinds: frozenset[str],
    skip_after_kind: str = "",
) -> list[_Line]:
    """Add blank lines before/after consecutive runs of *region_kinds*."""
    regions: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if not lines[i].in_fence and lines[i].kind in region_kinds:
            start = i
            while i < len(lines) and lines[i].kind in region_kinds:
                i += 1
            regions.append((start, i - 1))
        else:
            i += 1

    region_idx: set[int] = set()
    for s, e in regions:
        for k in range(s, e + 1):
            region_idx.add(k)

    result: list[_Line] = []
    i = 0
    while i < len(lines):
        if i in region_idx and lines[i].kind in region_kinds:
            if result and result[-1].kind != "blank" and not result[-1].in_fence:
                if result[-1].kind != skip_after_kind:
                    result.append(_Line("", "blank"))
            while i in region_idx:
                result.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].kind != "blank" and not lines[i].in_fence:
                result.append(_Line("", "blank"))
        elif i in region_idx:
            i += 1
        else:
            result.append(lines[i])
            i += 1
    return result


def _add_fence_blanks(lines: list[_Line]) -> list[_Line]:
    fences: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if not lines[i].in_fence and lines[i].kind == "code_fence_start":
            start = i
            i += 1
            while i < len(lines) and lines[i].kind != "code_fence_end":
                i += 1
            if i < len(lines):
                fences.append((start, i))
        i += 1

    fence_idx: set[int] = set()
    for s, e in fences:
        for k in range(s, e + 1):
            fence_idx.add(k)

    result: list[_Line] = []
    i = 0
    while i < len(lines):
        if i in fence_idx and lines[i].kind == "code_fence_start":
            if result and result[-1].kind != "blank" and not result[-1].in_fence:
                result.append(_Line("", "blank"))
            while i in fence_idx:
                result.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].kind != "blank" and not lines[i].in_fence:
                result.append(_Line("", "blank"))
        elif i in fence_idx:
            i += 1
        else:
            result.append(lines[i])
            i += 1
    return result


def _add_list_blanks(lines: list[_Line]) -> list[_Line]:
    return _add_blanks_around_block(
        lines,
        frozenset({"ul", "ol", "task"}),
        skip_after_kind="code_fence_end",
    )


def _add_table_blanks(lines: list[_Line]) -> list[_Line]:
    return _add_blanks_around_block(
        lines,
        frozenset({"table_row", "table_sep"}),
        skip_after_kind="code_fence_end",
    )


def _fix_fm_spacing(lines: list[_Line]) -> list[_Line]:
    fm = _detect_front_matter(lines)
    if fm is None:
        return lines
    _, end = fm
    if end + 1 >= len(lines):
        return lines
    if lines[end + 1].kind != "blank":
        lines.insert(end + 1, _Line("", "blank"))
    return lines


def _collapse_consecutive_blanks(lines: list[_Line]) -> list[_Line]:
    result: list[_Line] = []
    blank_run = 0
    for line in lines:
        if line.kind == "blank" and not line.in_fence:
            blank_run += 1
            if blank_run > 2:
                continue
        else:
            blank_run = 0
        result.append(line)
    return result


def _ensure_final_newline(content: str) -> str:
    if not content.endswith("\n"):
        content += "\n"
    return content


# ── public API ─────────────────────────────────────────────────────


def cleanup_document(content: str) -> str:
    """Clean up a markdown document according to best practices.

    Applies all cleanup rules in a deterministic order and returns
    the cleaned document as a string.
    """
    if not content:
        return content

    lines = _classify(content)
    lines = _strip_trailing_whitespace(lines)
    lines = _fix_heading_space(lines)
    lines = _fix_ul_marker(lines)
    lines = _fix_checkbox_format(lines)
    lines = _fix_hr_style(lines)
    lines = _add_heading_blanks(lines)
    lines = _add_list_blanks(lines)
    lines = _add_fence_blanks(lines)
    lines = _add_table_blanks(lines)
    lines = _fix_fm_spacing(lines)
    lines = _collapse_consecutive_blanks(lines)
    result = _as_text(lines)
    result = _ensure_final_newline(result)
    return result
