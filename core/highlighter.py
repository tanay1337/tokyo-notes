"""Markdown syntax highlighting for the GTK TextBuffer."""
from __future__ import annotations

import logging
import re
from typing import Any

from gi.repository import Gtk, Pango

from core.utils import (
    BLOCKQUOTE_RE,
    CB_CHECKED_RE,
    CB_EMPTY_RE,
    DEADLINE_RE,
    FENCED_CODE_RE,
    HEADER_ATX_RE,
    HR_RE,
    LIST_OL_RE,
    LIST_UL_RE,
    SETEXT_RE,
    TABLE_ROW_RE,
    TABLE_SEP_RE,
    TAG_RE,
    WIKI_CLICK_RE,
)

logger = logging.getLogger(__name__)

_LIGHT_COLORS: dict[str, str] = {
    "h1": "#34548a", "h2": "#5a4a78", "h3": "#33605a", "h4": "#8c4351", "h5": "#965027", "h6": "#8f5e15",
    "code_bg": "#cbccd1", "code_fg": "#8f5e15", "code_block_bg": "#cbccd1",
    "code_block_fg": "#343b58", "checkbox_empty": "#8c4351",
    "checkbox_checked": "#485e30", "internal_link": "#8f5e15",
    "external_link": "#34548a", "image": "#33605a", "tag": "#5a4a78",
    "deadline": "#965027", "hr": "#9699a3", "bullet": "#34548a",
    "number": "#5a4a78", "table": "#5a4a78", "blockquote": "#485e30",
    "dim": "#9699a3",
}

_DARK_COLORS: dict[str, str] = {
    "h1": "#7aa2f7", "h2": "#bb9af7", "h3": "#2ac3de", "h4": "#b4f9f8", "h5": "#ff9e64", "h6": "#e0af68",
    "code_bg": "#292e42", "code_fg": "#e0af68", "code_block_bg": "#1a1b26",
    "code_block_fg": "#a9b1d6", "checkbox_empty": "#f7768e",
    "checkbox_checked": "#9ece6a", "internal_link": "#e0af68",
    "external_link": "#7aa2f7", "image": "#2ac3de", "tag": "#bb9af7",
    "deadline": "#ff9e64", "hr": "#565f89", "bullet": "#7aa2f7",
    "number": "#bb9af7", "table": "#bb9af7", "blockquote": "#9ece6a",
    "dim": "#565f89",
}

# Theme name → palette. Unlisted themes fall back to the dark palette.
_THEME_COLORS: dict[str, dict[str, str]] = {
    "tokyo-light": _LIGHT_COLORS,
}


class MarkdownHighlighter:
    """Applies syntax-highlighting tags to a Gtk.TextBuffer in-place."""

    def __init__(self, buffer: Gtk.TextBuffer, theme_name: str = "tokyo-night") -> None:
        self.buffer = buffer
        self.enabled = True
        self.theme_name = theme_name

        # Standard patterns imported from core.utils to ensure consistency.
        self.re_fenced_code      = FENCED_CODE_RE
        self.re_setext_underline = SETEXT_RE
        self.re_hr               = HR_RE
        self.re_blockquote       = BLOCKQUOTE_RE
        self.re_unordered        = LIST_UL_RE
        self.re_ordered          = LIST_OL_RE
        self.re_table_row        = TABLE_ROW_RE
        self.re_table_sep        = TABLE_SEP_RE
        self.re_header           = HEADER_ATX_RE
        self.re_checkbox_empty   = CB_EMPTY_RE
        self.re_checkbox_checked = CB_CHECKED_RE
        self.re_deadline         = DEADLINE_RE
        self.re_tag              = TAG_RE
        self.re_links            = re.compile(
            r"\[\[([^\]]+)\]\]|(!?)\[([^\]]+)\]\(([^)]+)\)"
        )
        self.re_autolink         = re.compile(r"<([^>]+)>")
        self.re_html             = re.compile(r"<[^>]+>")
        self.re_bold1            = re.compile(r"(\*\*)([^*]+)(\*\*)")
        self.re_bold2            = re.compile(r"(__)([^_]+)(__)")
        self.re_italic1          = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
        self.re_italic2          = re.compile(r"(?<!_)_([^_]+)_(?!_)")
        self.re_code             = re.compile(r"(`)([^`]+)(`)")
        self.re_strikethrough    = re.compile(r"(~~)([^~]+)(~~)")
        self.re_pipe             = re.compile(r"\|")

        # Cache for _code_block_line_set — invalidated when buffer content changes.
        self._code_block_cache: set[int] = set()
        self._code_block_stamp: int = -1
        self._in_fence: bool = False

        self.setup_tags()

    def get_colors(self) -> dict[str, str]:
        return _THEME_COLORS.get(self.theme_name, _DARK_COLORS)

    def setup_tags(self) -> None:
        """Create or update all TextTags to match the current theme palette."""
        table = self.buffer.get_tag_table()
        c = self.get_colors()

        def tag(name: str, **props: Any) -> None:
            existing = table.lookup(name)
            if existing:
                for k, v in props.items():
                    existing.set_property(k, v)
            else:
                table.add(Gtk.TextTag(name=name, **props))

        tag("h1", weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground=c["h1"], left_margin=20)
        tag("h2", weight=Pango.Weight.BOLD, size=18 * Pango.SCALE, foreground=c["h2"], left_margin=20)
        tag("h3", weight=Pango.Weight.BOLD, size=16 * Pango.SCALE, foreground=c["h3"], left_margin=20)
        tag("h4", weight=Pango.Weight.BOLD, size=14 * Pango.SCALE, foreground=c["h4"], left_margin=20)
        tag("h5", weight=Pango.Weight.BOLD, size=13 * Pango.SCALE, foreground=c["h5"], left_margin=20)
        tag("h6", weight=Pango.Weight.BOLD, size=12 * Pango.SCALE, foreground=c["h6"], left_margin=20)
        tag("body",              left_margin=30)
        tag("code",              family="Monospace", background=c["code_bg"],       foreground=c["code_fg"])
        tag("code_block",        family="Monospace", background=c["code_block_bg"], foreground=c["code_block_fg"])
        tag("code_fence",        foreground=c["dim"], weight=Pango.Weight.BOLD)
        tag("checkbox_empty",    foreground=c["checkbox_empty"],   weight=Pango.Weight.BOLD)
        tag("checkbox_checked",  foreground=c["checkbox_checked"], weight=Pango.Weight.BOLD)
        tag("bold",              weight=Pango.Weight.BOLD)
        tag("italic",            style=Pango.Style.ITALIC)
        tag("internal-link",     foreground=c["internal_link"], weight=Pango.Weight.BOLD)
        tag("external-link",     foreground=c["external_link"], weight=Pango.Weight.BOLD)
        tag("image",             foreground=c["image"], style=Pango.Style.ITALIC)
        tag("tag",               foreground=c["tag"],  weight=Pango.Weight.BOLD)
        tag("strikethrough",     strikethrough=True)
        tag("deadline",          foreground=c["deadline"], style=Pango.Style.ITALIC)
        tag("hr",                foreground=c["hr"],  weight=Pango.Weight.BOLD)
        tag("list_bullet",       foreground=c["bullet"], weight=Pango.Weight.BOLD)
        tag("list_number",       foreground=c["number"], weight=Pango.Weight.BOLD)
        tag("table_row",         foreground=c["table"], weight=Pango.Weight.BOLD)
        tag("table_sep",         foreground=c["hr"],  weight=Pango.Weight.BOLD)
        tag("blockquote",        foreground=c["blockquote"], style=Pango.Style.ITALIC)
        tag("setext_underline",  foreground=c["hr"])
        tag("setext_h1",         weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground=c["h1"])
        tag("setext_h2",         weight=Pango.Weight.BOLD, size=18 * Pango.SCALE, foreground=c["h2"])
        tag("autolink",          foreground=c["external_link"], underline=Pango.Underline.SINGLE)
        tag("inline_html",       foreground=c["checkbox_empty"])
        tag("line_break",        weight=Pango.Weight.BOLD)
        tag("invisible",         invisible=True)
        tag("dim",               foreground=c["dim"])


    def update_theme(self, theme_name: str) -> None:
        self.theme_name = theme_name
        self.setup_tags()
        self.highlight()

    # StatementBuffer returns (success, iter) -- unpack the iter.

    def get_iter_at_line(self, line: int) -> Gtk.TextIter:
        result = self.buffer.get_iter_at_line(line)
        if isinstance(result, tuple):
            success, it = result
            if not success:
                logger.debug("get_iter_at_line(%d) failed, using end-of-buffer", line)
            return it
        return result

    def get_iter_at_offset(self, offset: int) -> Gtk.TextIter:
        result = self.buffer.get_iter_at_offset(offset)
        if isinstance(result, tuple):
            success, it = result
            if not success:
                logger.debug("get_iter_at_offset(%d) failed, using end-of-buffer", offset)
            return it
        return result

    # Code-block membership helpers

    def _code_block_line_set(self) -> set[int]:
        """Return the set of 0-based line numbers that fall inside a fenced code block.

        The result is cached and keyed on the buffer's character count so
        repeated calls during the same highlight pass cost nothing.
        """
        stamp = self.buffer.get_char_count()
        if stamp == self._code_block_stamp:
            return self._code_block_cache

        start, end = self.buffer.get_bounds()
        raw = self.buffer.get_text(start, end, True)
        lines = raw.rstrip("\n").split("\n")
        result: set[int] = set()
        in_block = False
        for i, line in enumerate(lines):
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                result.add(i)

        self._code_block_cache = result
        self._code_block_stamp = stamp
        return result

    # Main highlight pass

    def highlight(
        self,
        start_line: int = 0,
        end_line: int | None = None,
        cursor_line: int | None = None,
    ) -> None:
        if not self.enabled:
            return

        total_lines = self.buffer.get_line_count()
        if end_line is None or end_line > total_lines:
            end_line = total_lines

        is_full_pass = start_line == 0 and end_line == total_lines
        self._in_fence = False

        start_iter = self.get_iter_at_line(start_line)
        end_iter = (
            self.buffer.get_end_iter()
            if end_line == total_lines
            else self.get_iter_at_line(end_line)
        )
        self.buffer.remove_all_tags(start_iter, end_iter)

        text_range = self.buffer.get_text(start_iter, end_iter, True)

        # Fenced code blocks span multiple lines, so they are only correctly
        # handled during a full-document pass. For partial passes (cursor move),
        # we re-apply the code_block tag to any line that falls inside a block
        # so that remove_all_tags above doesn't strip it.
        if is_full_pass:
            for match in self.re_fenced_code.finditer(text_range):
                self.apply_tag("code_block", match.start(2), match.end(2))
                self.apply_tag("dim",  match.start(),  match.start(2))
                self.apply_tag("dim",  match.end(2),   match.end())
        else:
            # Partial pass: compute the code-block membership set once (O(n))
            # then do O(1) lookups per line instead of O(n) per line.
            code_block_lines = self._code_block_line_set()
            for i in range(end_line - start_line):
                if (start_line + i) in code_block_lines:
                    line_iter = self.get_iter_at_line(start_line + i)
                    line_end = line_iter.copy()
                    if not line_end.ends_line():
                        line_end.forward_to_line_end()
                    self.buffer.apply_tag_by_name("code_block", line_iter, line_end)

        lines = text_range.split("\n")
        line_start_offset = start_iter.get_offset()
        # For partial passes, code_block_lines was already computed above.
        # Define it here (unused) so the name always exists.
        code_block_lines: set[int] = set()

        for i, line in enumerate(lines):
            curr_line_num = start_line + i
            is_cursor = cursor_line == curr_line_num
            line_end_offset = line_start_offset + len(line)

            # Skip lines inside a fenced code block — already tagged above.
            # code_block_lines is computed once before the loop for partial passes.
            if not is_full_pass and curr_line_num in code_block_lines:
                line_start_offset += len(line) + 1
                continue

            # Fence marker lines must keep their dim tag in partial passes.
            if not is_full_pass and line.strip().startswith("```"):
                self.apply_tag("dim", line_start_offset, line_end_offset)
                line_start_offset += len(line) + 1
                continue

            # During a full pass we also skip code-block interior lines here
            # because they were tagged by the fenced-block pass above.
            # Use a running O(1) toggle instead of summing fences per line.
            if is_full_pass:
                inside_fence = self._in_fence
                if line.strip().startswith("```"):
                    self._in_fence = not self._in_fence
                if inside_fence:
                    line_start_offset += len(line) + 1
                    continue

            # Setext headings — use the already-split previous line to avoid
            # O(n²) buffer reads.
            if i > 0 and curr_line_num > 0:
                prev_line = lines[i - 1]
                setext = self.re_setext_underline.match(line)
                if (
                    setext
                    and prev_line.strip()
                    and not prev_line.strip().startswith("#")
                    and not self.re_unordered.match(prev_line)
                ):
                    self.apply_tag("setext_underline", line_start_offset, line_end_offset)
                    prev_offset = line_start_offset - len(prev_line) - 1
                    level = 1 if setext.group(2)[0] == "=" else 2
                    self.apply_tag(
                        "setext_h1" if level == 1 else "setext_h2",
                        prev_offset,
                        prev_offset + len(prev_line),
                    )
                    line_start_offset += len(line) + 1
                    continue

            # Horizontal rules
            if self.re_hr.match(line):
                self.apply_tag("hr", line_start_offset, line_end_offset)
                line_start_offset += len(line) + 1
                continue

            # Block quotes
            bq = self.re_blockquote.match(line)
            if bq:
                self.apply_tag(
                    "blockquote",
                    line_start_offset,
                    line_start_offset + len(bq.group(1)),
                )

            # Lists
            ul = self.re_unordered.match(line)
            if ul:
                indent = len(ul.group(1))
                self.apply_tag(
                    "list_bullet",
                    line_start_offset + indent,
                    line_start_offset + indent + len(ul.group(2)) + 1,
                )

            ol = self.re_ordered.match(line)
            if ol:
                indent = len(ol.group(1))
                self.apply_tag(
                    "list_number",
                    line_start_offset + indent,
                    line_start_offset + indent + len(ol.group(2)) + 1,
                )

            # Tables — one pass over the line, branch on separator type.
            if "|" in line and not ul and not ol and self.re_table_row.match(line):
                pipe_tag = "table_sep" if self.re_table_sep.match(line) else "table_row"
                for m in self.re_pipe.finditer(line):
                    self.apply_tag(
                        pipe_tag,
                        line_start_offset + m.start(),
                        line_start_offset + m.start() + 1,
                    )

            # ATX headings
            h = self.re_header.match(line)
            if h:
                level = len(h.group(1))
                self.apply_tag(f"h{level}", line_start_offset, line_end_offset)
                marker_end = line_start_offset + level
                self.apply_tag(
                    "dim" if is_cursor else "invisible",
                    line_start_offset,
                    marker_end,
                )
                line_start_offset += len(line) + 1
                continue

            self.apply_tag("body", line_start_offset, line_end_offset)

            self._apply_inline_tags(line, line_start_offset, line_end_offset, is_cursor)

            line_start_offset += len(line) + 1

    # Helpers

    def _inline(
        self,
        pattern: re.Pattern,
        tag: str,
        line: str,
        line_offset: int,
        is_cursor: bool,
        single: bool = False,
    ) -> None:
        """Apply an inline style tag and dim/hide its markers."""
        for m in pattern.finditer(line):
            self.apply_tag(tag, line_offset + m.start(), line_offset + m.end())
            marker_tag = "dim" if is_cursor else "invisible"
            if single:
                self.apply_tag(marker_tag, line_offset + m.start(),     line_offset + m.start() + 1)
                self.apply_tag(marker_tag, line_offset + m.end() - 1,   line_offset + m.end())
            else:
                self.apply_tag(marker_tag, line_offset + m.start(1), line_offset + m.end(1))
                self.apply_tag(marker_tag, line_offset + m.start(3), line_offset + m.end(3))

    def apply_tag(self, tag_name: str, start_offset: int, end_offset: int) -> None:
        if start_offset >= end_offset:
            return
        start_iter = self.get_iter_at_offset(start_offset)
        end_iter = self.get_iter_at_offset(end_offset)
        self.buffer.apply_tag_by_name(tag_name, start_iter, end_iter)

    def _tag_for_line(self, md_line) -> str | None:
        """Return the GTK text tag name for a markdown line kind."""
        return {
            "h1": "h1",
            "h2": "h2",
            "h3": "h3",
            "h4": "h4",
            "h5": "h5",
            "h6": "h6",
            "hr": "hr",
            "blockquote": "blockquote",
            "table_row": "table_row",
            "table_sep": "table_sep",
            "code_block": "code_block",
        }.get(md_line.kind)

    def _apply_line_tags(self, line_start_offset, line_end_offset, line, md_line, is_cursor=False):
        """Apply tags for a single parsed markdown line."""
        # Structural tag
        tag_name = self._tag_for_line(md_line)
        if tag_name:
            self.apply_tag(tag_name, line_start_offset, line_end_offset)

        # Lists: also tag the bullet/number separately
        if md_line.kind in ("ul", "ol") and md_line.marker:
            self.apply_tag(
                "list_bullet" if md_line.kind == "ul" else "list_number",
                line_start_offset + md_line.indent,
                line_start_offset + md_line.indent + len(md_line.marker),
            )

        self._apply_inline_tags(line, line_start_offset, line_end_offset, is_cursor)

    def _apply_inline_tags(self, line: str, line_start_offset: int, line_end_offset: int, is_cursor: bool) -> None:
        """Apply inline patterns shared between the full and partial highlight passes."""

        # Checkboxes
        for m in self.re_checkbox_empty.finditer(line):
            self.apply_tag("checkbox_empty", line_start_offset + m.start(), line_start_offset + m.end())
        for m in self.re_checkbox_checked.finditer(line):
            self.apply_tag("checkbox_checked", line_start_offset + m.start(), line_start_offset + m.end())

        # Inline styles
        self._inline(self.re_bold1, "bold", line, line_start_offset, is_cursor)
        self._inline(self.re_bold2, "bold", line, line_start_offset, is_cursor)
        self._inline(self.re_italic1, "italic", line, line_start_offset, is_cursor, single=True)
        self._inline(self.re_italic2, "italic", line, line_start_offset, is_cursor, single=True)
        self._inline(self.re_code, "code", line, line_start_offset, is_cursor)
        self._inline(self.re_strikethrough, "strikethrough", line, line_start_offset, is_cursor)

        # Links and images
        for m in self.re_links.finditer(line):
            fs = line_start_offset + m.start()
            fe = line_start_offset + m.end()
            if m.group(1):  # [[wiki link]]
                self.apply_tag("internal-link", fs, fe)
                if not is_cursor:
                    self.apply_tag("invisible", fs, fs + 2)
                    self.apply_tag("invisible", fe - 2, fe)
            else:
                if m.group(2):  # ![image](...)
                    self.apply_tag("image", fs, fe)
                else:  # [text](url)
                    text_s = fs + 1
                    text_e = text_s + len(m.group(3))
                    self.apply_tag("external-link", text_s, text_e)
                    brackets = "dim" if is_cursor else "invisible"
                    self.apply_tag(brackets, fs, text_s)
                    self.apply_tag(brackets, text_e, fe)

        # Deadlines and tags
        for m in self.re_deadline.finditer(line):
            self.apply_tag("deadline", line_start_offset + m.start(), line_start_offset + m.end())
        for m in self.re_tag.finditer(line):
            self.apply_tag("tag", line_start_offset + m.start(), line_start_offset + m.end())

        # Autolinks
        for m in self.re_autolink.finditer(line):
            self.apply_tag("autolink",
                           line_start_offset + m.start(),
                           line_start_offset + m.end())
            self.apply_tag("invisible",
                           line_start_offset + m.start(),
                           line_start_offset + m.start() + 1)
            self.apply_tag("invisible",
                           line_start_offset + m.end() - 1,
                           line_start_offset + m.end())

        # Inline HTML
        for m in self.re_html.finditer(line):
            content = m.group(0)
            is_autolink = "http" in content or ("@" in content and "<" in content)
            if not is_autolink and not content.startswith(("<!", "<?")):
                self.apply_tag("inline_html",
                               line_start_offset + m.start(),
                               line_start_offset + m.end())

        # Hard line breaks
        if line.rstrip().endswith("\\"):
            self.apply_tag("line_break",
                           line_start_offset + len(line.rstrip()),
                           line_end_offset)

    # Incremental highlighter: only re-tag a single line range

    def highlight_line_range(self, start_line: int, end_line: int, cursor_line: int | None = None):
        """Re-apply tags for lines from start_line to end_line (inclusive)."""
        from markdown.tokenizer import LineTokenizer
        tokenizer = LineTokenizer()
        code_block_lines = self._code_block_line_set()
        in_block = False
        # If start_line is inside a fenced code block, set in_block=True so the
        # tokenizer correctly identifies code lines (fences may start earlier).
        if start_line in code_block_lines:
            in_block = True
        for line_num in range(start_line, end_line + 1):
            it = self.get_iter_at_line(line_num)
            it_end = it.copy()
            if not it_end.ends_line():
                it_end.forward_to_line_end()
            line = self.buffer.get_text(it, it_end, True)
            line_start = it.get_offset()
            line_end = it_end.get_offset()

            # Clear existing tags on this line
            self.buffer.remove_all_tags(it, it_end)

            md, in_block = tokenizer.tokenize(line, in_fence=in_block)

            if md.kind == "code_block":
                self.apply_tag("code_block", line_start, line_end)
                continue

            if md.kind in ("code_fence_start", "code_fence_end"):
                self.apply_tag("dim", line_start, line_end)
                continue

            # Apply tags using the same logic as highlight() but line-local
            self._apply_line_tags(line_start, line_end, line, md, is_cursor=(line_num == cursor_line))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self.highlight()
        else:
            start, end = self.buffer.get_bounds()
            self.buffer.remove_all_tags(start, end)
