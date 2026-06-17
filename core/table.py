"""Pipe-table parsing and serialisation for the table editor."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class Table:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    col_alignments: list[str] = field(default_factory=list)
    # Original markdown lines before parsing (for in-buffer replacement).
    raw_lines: list[str] = field(default_factory=list)


_SEP_RE = re.compile(r"^\|[-:| ]+\|$")


def parse_table(lines: list[str]) -> Table | None:
    """Parse a list of consecutive pipe-table lines into a *Table*.

    Returns ``None`` if *lines* do not form a valid pipe table.
    """
    if not lines:
        return None

    # Locate the separator row (the one with --- between pipes).
    sep_idx: int | None = None
    for i, line in enumerate(lines):
        if _SEP_RE.match(line.strip()):
            sep_idx = i
            break
    if sep_idx is None or sep_idx == 0:
        return None

    # Header row(s) before separator.
    headers_raw = lines[:sep_idx]
    if not headers_raw:
        return None

    # Data rows after separator.
    data_raw = lines[sep_idx + 1 :]

    # Parse alignment from separator.
    alignments = _parse_alignments(lines[sep_idx])

    # Extract cells from all rows.
    header_cells = _split_row(headers_raw[0])
    if not header_cells:
        return None

    rows: list[list[str]] = []
    for raw in data_raw:
        cells = _split_row(raw)
        if cells:
            rows.append(cells)

    # Pad shorter rows to match header count.
    ncols = len(header_cells)
    for row in rows:
        while len(row) < ncols:
            row.append("")

    return Table(
        headers=header_cells,
        rows=rows,
        col_alignments=alignments,
        raw_lines=list(lines),
    )


def _parse_alignments(sep: str) -> list[str]:
    """Return alignment strings from a pipe separator."""
    parts = sep.strip().strip("|").split("|")
    result: list[str] = []
    for p in parts:
        p = p.strip()
        if p.startswith(":") and p.endswith(":"):
            result.append("center")
        elif p.endswith(":"):
            result.append("right")
        elif p.startswith(":"):
            result.append("left")
        else:
            result.append("left")
    return result


def _split_row(line: str) -> list[str]:
    """Split a pipe-table row into cell strings, trimming surrounding whitespace."""
    raw = line.strip()
    if not raw.startswith("|") or not raw.endswith("|"):
        return []
    inner = raw[1:-1]
    return [c.strip() for c in inner.split("|")]


def table_to_markdown(table: Table) -> str:
    """Serialize a *Table* back to pipe-aligned markdown.

    Cells are padded to the maximum width of each column so that
    pipe characters align vertically when rendered in a monospace font.
    """
    ncols = len(table.headers)
    alignments = table.col_alignments or ["left"] * ncols
    while len(alignments) < ncols:
        alignments.append("left")

    # Collect all cells per column to compute max widths.
    all_rows: list[list[str]] = [table.headers]
    all_rows.extend(table.rows)
    col_widths: list[int] = []
    for ci in range(ncols):
        widths = [len(row[ci]) if ci < len(row) else 0 for row in all_rows]
        col_widths.append(max(max(widths) if widths else 0, 3))

    sep_parts: list[str] = []
    for ci, a in enumerate(alignments):
        w = max(col_widths[ci], 3)
        if a == "center":
            sep_parts.append(":" + "-" * w + ":")
        elif a == "right":
            sep_parts.append("-" * (w + 1) + ":")
        else:
            sep_parts.append(":" + "-" * (w + 1))
    sep = "|" + "|".join(sep_parts) + "|"

    lines: list[str] = [_format_row(table.headers, col_widths)]
    lines.append(sep)
    for row in table.rows:
        lines.append(_format_row(row, col_widths))

    return "\n".join(lines) + "\n"


def _format_row(cells: Sequence[str], col_widths: list[int] | None = None) -> str:
    parts: list[str] = []
    for ci, cell in enumerate(cells):
        width = col_widths[ci] if col_widths and ci < len(col_widths) else len(cell)
        parts.append(cell.ljust(width))
    return "| " + " | ".join(parts) + " |"


def find_tables(text: str) -> list[Table]:
    """Scan *text* for all contiguous pipe-table blocks and return parsed *Table*s."""
    tables: list[Table] = []
    lines = text.split("\n")
    buf: list[str] = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        is_pipe = stripped.startswith("|") and "|" in stripped[1:]
        if is_pipe:
            buf.append(line)
            in_table = True
        else:
            if in_table and buf:
                tbl = parse_table(buf)
                if tbl is not None:
                    tables.append(tbl)
                buf = []
            in_table = False

    if in_table and buf:
        tbl = parse_table(buf)
        if tbl is not None:
            tables.append(tbl)

    return tables
