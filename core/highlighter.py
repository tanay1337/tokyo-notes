"""Markdown syntax highlighting for the GTK TextBuffer."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from gi.repository import Gdk, Gtk, Pango

if TYPE_CHECKING:
    from core.spell_checker import SpellChecker

from core.utils import (
    _EMBED_SIZE_RE,
    BLOCKQUOTE_RE,
    CALLOUT_RE,
    CB_CHECKED_RE,
    CB_EMPTY_RE,
    DEADLINE_RE,
    FENCED_CODE_RE,
    HEADER_ATX_RE,
    HR_RE,
    LIST_OL_RE,
    LIST_UL_RE,
    MD_URL_BALANCED,
    TABLE_ROW_RE,
    TABLE_SEP_RE,
    TAG_RE,
    resolve_callout_type,
)

logger = logging.getLogger(__name__)

_DEFAULT_SYNTAX_COLORS: dict[str, str] = {
    "h1": "#7aa2f7",
    "h2": "#bb9af7",
    "h3": "#2ac3de",
    "h4": "#b4f9f8",
    "h5": "#ff9e64",
    "h6": "#e0af68",
    "code_bg": "#292e42",
    "code_fg": "#e0af68",
    "code_block_bg": "#1a1b26",
    "code_block_fg": "#a9b1d6",
    "checkbox_empty": "#f7768e",
    "checkbox_checked": "#9ece6a",
    "internal_link": "#e0af68",
    "external_link": "#7aa2f7",
    "image": "#2ac3de",
    "tag": "#bb9af7",
    "deadline": "#ff9e64",
    "hr": "#565f89",
    "bullet": "#7aa2f7",
    "number": "#bb9af7",
    "table": "#bb9af7",
    "blockquote": "#9ece6a",
    "dim": "#565f89",
    "front_matter_key": "#bb9af7",
    "front_matter_value": "#e0af68",
    # Callout type colours (13 types, some share a hue)
    "callout_note": "#7aa2f7",
    "callout_abstract": "#2ac3de",
    "callout_info": "#7aa2f7",
    "callout_todo": "#7aa2f7",
    "callout_tip": "#2ac3de",
    "callout_success": "#9ece6a",
    "callout_question": "#e0af68",
    "callout_warning": "#ff9e64",
    "callout_failure": "#f7768e",
    "callout_danger": "#f7768e",
    "callout_bug": "#f7768e",
    "callout_example": "#bb9af7",
    "callout_quote": "#565f89",
}


class MarkdownHighlighter:
    """Applies syntax-highlighting tags to a Gtk.TextBuffer in-place."""

    def __init__(
        self, buffer: Gtk.TextBuffer, theme_manager, theme_name: str = "tokyo-night"
    ) -> None:
        self.buffer = buffer
        self.theme_manager = theme_manager
        self.enabled = True
        self.theme_name = theme_name
        self.spell_checker: SpellChecker | None = None
        self.spell_check_enabled: bool = False
        self.always_show_markdown: bool = False

        # Standard patterns imported from core.utils to ensure consistency.
        self.re_fenced_code = FENCED_CODE_RE
        self.re_hr = HR_RE
        self.re_blockquote = BLOCKQUOTE_RE
        self.re_callout = CALLOUT_RE
        self.re_unordered = LIST_UL_RE
        self.re_ordered = LIST_OL_RE
        self.re_table_row = TABLE_ROW_RE
        self.re_table_sep = TABLE_SEP_RE
        self.re_header = HEADER_ATX_RE
        self.re_checkbox_empty = CB_EMPTY_RE
        self.re_checkbox_checked = CB_CHECKED_RE
        self.re_deadline = DEADLINE_RE
        self.re_tag = TAG_RE
        self.re_links = re.compile(
            r"\[\[([^\]]+)\]\]|(!?)\[([^\]]+)\]\((" + MD_URL_BALANCED + r")\)"
        )
        self.re_autolink = re.compile(r"<([^>]+)>")
        self.re_html = re.compile(r"<[^>]+>")
        self.re_bold1 = re.compile(r"(\*\*)([^*]+)(\*\*)")
        self.re_bold2 = re.compile(r"(__)([^_]+)(__)")
        self.re_italic1 = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
        self.re_italic2 = re.compile(r"(?<!_)_([^_]+)_(?!_)")
        self.re_code = re.compile(r"(`)([^`]+)(`)")
        self.re_strikethrough = re.compile(r"(~~)([^~]+)(~~)")

        # Cache for _code_block_line_set — invalidated when buffer content changes.
        self._code_block_cache: set[int] = set()
        self._code_block_stamp: int = -1
        self._in_fence: bool = False
        self._hanging_tag_cache: dict[int, str] = {}

        # Cache for _front_matter_range — invalidated when buffer content changes.
        self._fm_cached: tuple[int, int] | None = None
        self._fm_stamp: int = -1

        self.setup_tags()

    def get_colors(self) -> dict[str, str]:
        css_colors = self.theme_manager.get_syntax_colors(self.theme_name)
        return {**_DEFAULT_SYNTAX_COLORS, **css_colors}

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

        tag(
            "h1",
            weight=Pango.Weight.BOLD,
            size=22 * Pango.SCALE,
            foreground=c["h1"],
            left_margin=20,
        )
        tag(
            "h2",
            weight=Pango.Weight.BOLD,
            size=18 * Pango.SCALE,
            foreground=c["h2"],
            left_margin=20,
        )
        tag(
            "h3",
            weight=Pango.Weight.BOLD,
            size=16 * Pango.SCALE,
            foreground=c["h3"],
            left_margin=20,
        )
        tag(
            "h4",
            weight=Pango.Weight.BOLD,
            size=14 * Pango.SCALE,
            foreground=c["h4"],
            left_margin=20,
        )
        tag(
            "h5",
            weight=Pango.Weight.BOLD,
            size=13 * Pango.SCALE,
            foreground=c["h5"],
            left_margin=20,
        )
        tag(
            "h6",
            weight=Pango.Weight.BOLD,
            size=12 * Pango.SCALE,
            foreground=c["h6"],
            left_margin=20,
        )
        tag("body", left_margin=30)
        tag(
            "code", family="Monospace", background=c["code_bg"], foreground=c["code_fg"]
        )
        tag(
            "code_block",
            family="Monospace",
            background=c["code_block_bg"],
            foreground=c["code_block_fg"],
        )
        tag("code_fence", foreground=c["dim"], weight=Pango.Weight.BOLD)
        tag("checkbox_empty", foreground=c["checkbox_empty"], weight=Pango.Weight.BOLD)
        tag(
            "checkbox_checked",
            foreground=c["checkbox_checked"],
            weight=Pango.Weight.BOLD,
        )
        tag("bold", weight=Pango.Weight.BOLD)
        tag("italic", style=Pango.Style.ITALIC)
        tag("internal-link", foreground=c["internal_link"], weight=Pango.Weight.BOLD)
        tag("external-link", foreground=c["external_link"], weight=Pango.Weight.BOLD)
        tag("image", foreground=c["image"], style=Pango.Style.ITALIC)
        tag("tag", foreground=c["tag"], weight=Pango.Weight.BOLD)
        tag("strikethrough", strikethrough=True)
        tag("deadline", foreground=c["deadline"], style=Pango.Style.ITALIC)
        tag("hr", foreground=c["hr"], weight=Pango.Weight.BOLD)
        tag("list_bullet", foreground=c["bullet"], weight=Pango.Weight.BOLD)
        tag("list_number", foreground=c["number"], weight=Pango.Weight.BOLD)
        tag(
            "table_row",
            foreground=c["table"],
            weight=Pango.Weight.BOLD,
            family="Monospace",
        )
        tag(
            "table_sep",
            foreground=c["hr"],
            weight=Pango.Weight.BOLD,
            family="Monospace",
        )
        tag("table_data_row", family="Monospace")
        bg_rgba = Gdk.RGBA()
        bg_rgba.parse(c["blockquote"])
        r = int(bg_rgba.red * 255)
        g = int(bg_rgba.green * 255)
        b = int(bg_rgba.blue * 255)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        bg_rgba.alpha = 0.04 if luminance > 180 else 0.08
        tag(
            "blockquote",
            left_margin=40,
            paragraph_background_rgba=bg_rgba,
        )
        marker_bg = Gdk.RGBA()
        marker_bg.parse(c["blockquote"])
        marker_bg.alpha = 0.25
        tag(
            "blockquote_marker",
            background_rgba=marker_bg,
            foreground=c["dim"],
        )
        _callout_types = (
            "note",
            "abstract",
            "info",
            "todo",
            "tip",
            "success",
            "question",
            "warning",
            "failure",
            "danger",
            "bug",
            "example",
            "quote",
        )
        for _ct in _callout_types:
            _fg = c.get(f"callout_{_ct}", c["blockquote"])
            _bg = Gdk.RGBA()
            _bg.parse(_fg)
            _bg.alpha = 0.15
            tag(
                f"callout_type_{_ct}",
                foreground=_fg,
                weight=Pango.Weight.BOLD,
                background_rgba=_bg,
            )
        for _ct in _callout_types:
            _fg = c.get(f"callout_{_ct}", c["blockquote"])
            _pbg = Gdk.RGBA()
            _pbg.parse(_fg)
            _pbg.alpha = 0.08
            tag(
                f"callout_bg_{_ct}",
                paragraph_background_rgba=_pbg,
            )
            _mbg = Gdk.RGBA()
            _mbg.parse(_fg)
            _mbg.alpha = 0.25
            tag(
                f"callout_marker_{_ct}",
                background_rgba=_mbg,
                foreground=c["dim"],
            )
        tag("callout_title", weight=Pango.Weight.BOLD)
        tag("autolink", foreground=c["external_link"], underline=Pango.Underline.SINGLE)
        tag("inline_html", foreground=c["checkbox_empty"])
        tag("line_break", weight=Pango.Weight.BOLD)
        tag("invisible", invisible=True)
        tag("transparent", foreground_rgba=Gdk.RGBA(0, 0, 0, 0))
        tag("dim", foreground=c["dim"])
        tag("front_matter_key", foreground=c["front_matter_key"])
        tag("front_matter_value", foreground=c["front_matter_value"])
        tag("misspelled", underline=Pango.Underline.ERROR)

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
                logger.debug(
                    "get_iter_at_offset(%d) failed, using end-of-buffer", offset
                )
            return it
        return result

    # Code-block membership helpers

    def _code_block_line_set(self) -> set[int]:
        """Return the set of 0-based line numbers that fall inside a fenced code block.

        The result is cached and keyed on the buffer's character count so
        repeated calls during the same highlight pass cost nothing.
        """
        stamp = self.buffer.get_line_count()
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

    def _front_matter_range(self) -> tuple[int, int] | None:
        """Return (start_line, end_line) of front matter block, or None."""
        stamp = self.buffer.get_line_count()
        if stamp == self._fm_stamp:
            return self._fm_cached
        start, end = self.buffer.get_bounds()
        raw = self.buffer.get_text(start, end, True)
        lines = raw.split("\n")
        result = None
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    result = (0, i)
                    break
        self._fm_cached = result
        self._fm_stamp = stamp
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
        self._current_callout_type: str | None = None

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
                self.apply_tag("dim", match.start(), match.start(2))
                self.apply_tag("dim", match.end(2), match.end())
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
        passed_table_sep = False
        for i, line in enumerate(lines):
            curr_line_num = start_line + i
            is_cursor = cursor_line == curr_line_num
            line_start_offset = self.get_iter_at_line(curr_line_num).get_offset()
            line_end_offset = line_start_offset + len(line)

            # Skip lines inside a fenced code block — already tagged above.
            # code_block_lines is computed once before the loop for partial passes.
            if not is_full_pass and curr_line_num in code_block_lines:
                continue

            # Fence marker lines must keep their dim tag in partial passes.
            if not is_full_pass and line.strip().startswith("```"):
                self.apply_tag("dim", line_start_offset, line_end_offset)
                continue

            # During a full pass we also skip code-block interior lines here
            # because they were tagged by the fenced-block pass above.
            # Use a running O(1) toggle instead of summing fences per line.
            if is_full_pass:
                inside_fence = self._in_fence
                if line.strip().startswith("```"):
                    self._in_fence = not self._in_fence
                if inside_fence:
                    continue

            # Front matter block at document start (--- ... ---)
            fm = self._front_matter_range()
            if fm is not None and fm[0] <= curr_line_num <= fm[1]:
                self.apply_tag("dim", line_start_offset, line_end_offset)
                if curr_line_num == fm[0] or curr_line_num == fm[1]:
                    continue  # opening or closing ---
                if ":" in line:
                    colon_pos = line.index(":")
                    self.apply_tag(
                        "front_matter_key",
                        line_start_offset,
                        line_start_offset + colon_pos,
                    )
                    val_start = line_start_offset + colon_pos + 1
                    if val_start < line_end_offset:
                        self.apply_tag("front_matter_value", val_start, line_end_offset)
                continue

            # Horizontal rules
            if self.re_hr.match(line):
                self.apply_tag("hr", line_start_offset, line_end_offset)
                continue

            # Block quotes
            bq = self.re_blockquote.match(line)
            if bq:
                callout = self.re_callout.match(line)
                if callout:
                    ctype = resolve_callout_type(callout.group(2))
                    self._current_callout_type = ctype
                    type_start = line_start_offset + callout.start(2) - 2
                    type_end = line_start_offset + callout.end(2) + 1
                    self.apply_tag(f"callout_type_{ctype}", type_start, type_end)
                    title_text = callout.group(4)
                    if title_text.strip():
                        title_start = line_start_offset + callout.start(4)
                        title_end = line_start_offset + callout.end(4)
                        self.apply_tag("callout_title", title_start, title_end)
                if self._current_callout_type:
                    self.apply_tag(
                        f"callout_bg_{self._current_callout_type}",
                        line_start_offset,
                        line_end_offset,
                    )
                self.apply_tag(
                    "blockquote",
                    line_start_offset,
                    line_end_offset,
                )
                marker_end = line_start_offset + len(bq.group(1))
                self.apply_tag(
                    "blockquote_marker",
                    line_start_offset,
                    marker_end,
                )
                if self._current_callout_type:
                    self.apply_tag(
                        f"callout_marker_{self._current_callout_type}",
                        line_start_offset,
                        marker_end,
                    )
            else:
                self._current_callout_type = None

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

            # Tables — header row gets bold accent, body rows get normal weight.
            is_table = (
                "|" in line and not ul and not ol and self.re_table_row.match(line)
            )
            if is_table:
                is_sep = bool(self.re_table_sep.match(line))
                if is_sep:
                    pipe_tag = "table_sep"
                    passed_table_sep = True
                elif passed_table_sep:
                    pipe_tag = "table_data_row"
                else:
                    pipe_tag = "table_row"
                self.apply_tag(pipe_tag, line_start_offset, line_end_offset)
                self._dim_table_pipes(line, line_start_offset, line_end_offset)
            else:
                passed_table_sep = False

            # ATX headings
            h = self.re_header.match(line)
            if h:
                level = len(h.group(1))
                self.apply_tag(f"h{level}", line_start_offset, line_end_offset)
                marker_end = line_start_offset + level
                self.apply_tag(
                    self._marker_tag(is_cursor),
                    line_start_offset,
                    marker_end,
                )
                continue

            self._apply_list_hanging(line, line_start_offset, line_end_offset)
            self._apply_indent_hanging(line, line_start_offset, line_end_offset)
            self.apply_tag("body", line_start_offset, line_end_offset)

            self._apply_inline_tags(line, line_start_offset, line_end_offset, is_cursor)

        if self.spell_checker and self.spell_check_enabled:
            code_block_lines = self._code_block_line_set()
            self._spell_check_pass(start_line, end_line, code_block_lines)

    # Helpers

    def _marker_tag(self, is_cursor: bool) -> str:
        """Return 'dim' or 'invisible' based on cursor state and setting."""
        return "dim" if (is_cursor or self.always_show_markdown) else "invisible"

    def _dim_table_pipes(
        self, line: str, line_start_offset: int, line_end_offset: int
    ) -> None:
        """Apply dim tag to pipe characters in a table line."""
        for ci, ch in enumerate(line):
            if ch == "|":
                self.apply_tag(
                    "dim", line_start_offset + ci, line_start_offset + ci + 1
                )

    def _is_data_row(self, line_num: int) -> bool:
        """Return True if *line_num* is a table data row (after a separator row)."""
        for scan in range(line_num - 1, -1, -1):
            scan_it = self.get_iter_at_line(scan)
            scan_it_end = scan_it.copy()
            if not scan_it_end.ends_line():
                scan_it_end.forward_to_line_end()
            scan_text = self.buffer.get_text(scan_it, scan_it_end, True)
            if self.re_table_sep.match(scan_text):
                return True
            if not (scan_text.strip().startswith("|") and "|" in scan_text[1:]):
                return False
        return False

    def _inline(
        self,
        pattern: re.Pattern,
        tag: str,
        line: str,
        line_offset: int,
        is_cursor: bool,
        single: bool = False,
        exclude_ranges: set[tuple[int, int]] | None = None,
    ) -> None:
        """Apply an inline style tag and dim/hide its markers.

        If *exclude_ranges* is given, any match overlapping an excluded range
        is skipped — used to prevent inline formatting inside link/image URLs.
        """
        for m in pattern.finditer(line):
            ms = line_offset + m.start()
            me = line_offset + m.end()
            if exclude_ranges:
                excluded = False
                for es, ee in exclude_ranges:
                    if ms < ee and me > es:
                        excluded = True
                        break
                if excluded:
                    continue
            self.apply_tag(tag, ms, me)
            marker_tag = self._marker_tag(is_cursor)
            if single:
                self.apply_tag(
                    marker_tag, line_offset + m.start(), line_offset + m.start() + 1
                )
                self.apply_tag(
                    marker_tag, line_offset + m.end() - 1, line_offset + m.end()
                )
            else:
                self.apply_tag(
                    marker_tag, line_offset + m.start(1), line_offset + m.end(1)
                )
                self.apply_tag(
                    marker_tag, line_offset + m.start(3), line_offset + m.end(3)
                )

    def apply_tag(self, tag_name: str, start_offset: int, end_offset: int) -> None:
        if start_offset >= end_offset:
            return
        start_iter = self.get_iter_at_offset(start_offset)
        end_iter = self.get_iter_at_offset(end_offset)
        self.buffer.apply_tag_by_name(tag_name, start_iter, end_iter)

    def _apply_list_hanging(
        self, line: str, line_start_offset: int, line_end_offset: int
    ) -> None:
        """Apply a hanging indent to wrapped lines in list/checkbox items.

        Uses ``indent`` only (no ``left_margin``) so the body tag's
        ``left_margin=30`` still applies.  A negative ``indent`` leaves the
        first line at full width and indents subsequent wrapped lines by
        ``|indent|`` pixels — which is the standard hanging-indent behaviour.
        """
        ul_m = self.re_unordered.match(line)
        ol_m = self.re_ordered.match(line)
        cb_e = self.re_checkbox_empty.search(line)
        cb_c = self.re_checkbox_checked.search(line)

        if not (ul_m or ol_m or cb_e or cb_c):
            return

        # Find where the actual content text starts
        if cb_e or cb_c:
            cb_m = cb_e or cb_c
            pos = cb_m.end()
            while pos < len(line) and line[pos] == " ":
                pos += 1
            content_start = pos
        elif ul_m:
            content_start = ul_m.start(3)
        else:
            content_start = ol_m.start(3)

        # Estimate pixel width of the leading text.
        # Spaces are narrower than other characters; applying a flat
        # per-char multiplier overestimates for deeply-nested items.
        leading_text = line[:content_start]
        raw = sum(4 if c == " " else 6 for c in leading_text)
        indent_px = min(raw, 40)

        cache = self._hanging_tag_cache
        name = cache.get(indent_px)
        if name is None:
            name = f"_h_{indent_px}"
            self.buffer.create_tag(name, indent=-indent_px)
            cache[indent_px] = name

        self.apply_tag(name, line_start_offset, line_end_offset)

    def _apply_indent_hanging(
        self, line: str, line_start_offset: int, line_end_offset: int
    ) -> None:
        """Apply hanging indent to continuation wraps of indented paragraphs.

        Lines starting with 2+ spaces get a negative ``indent`` so wrapped
        continuation lines start at the same horizontal position as the
        leading whitespace rather than at column 0.
        """
        # Skip lines already handled by _apply_list_hanging
        if (
            self.re_unordered.match(line)
            or self.re_ordered.match(line)
            or self.re_checkbox_empty.search(line)
            or self.re_checkbox_checked.search(line)
        ):
            return

        stripped = line.lstrip()
        if stripped == line:
            return
        leading_spaces = len(line) - len(stripped)
        if leading_spaces < 2:
            return

        indent_px = leading_spaces * 4
        cache = self._hanging_tag_cache
        name = cache.get(indent_px)
        if name is None:
            name = f"_h_{indent_px}"
            self.buffer.create_tag(name, indent=-indent_px)
            cache[indent_px] = name

        self.apply_tag(name, line_start_offset, line_end_offset)

    def _tag_for_line(self, md_line, line_num=None) -> str | None:
        """Return the GTK text tag name for a markdown line kind."""
        if md_line.kind == "table_row":
            if line_num is not None and self._is_data_row(line_num):
                return "table_data_row"
            return "table_row"
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

    def _apply_line_tags(
        self,
        line_start_offset,
        line_end_offset,
        line,
        md_line,
        is_cursor=False,
        line_num=None,
    ):
        """Apply tags for a single parsed markdown line."""
        # Structural tag
        tag_name = self._tag_for_line(md_line, line_num=line_num)
        if tag_name:
            self.apply_tag(tag_name, line_start_offset, line_end_offset)

        # Blockquote marker styling (visual accent bar at the `>` position)
        if md_line.kind == "blockquote":
            bq_match = self.re_blockquote.match(line)
            if bq_match:
                self.apply_tag(
                    "blockquote_marker",
                    line_start_offset,
                    line_start_offset + len(bq_match.group(1)),
                )
            callout_match = self.re_callout.match(line)
            if callout_match:
                ctype = resolve_callout_type(callout_match.group(2))
                type_start = line_start_offset + callout_match.start(2) - 2
                type_end = line_start_offset + callout_match.end(2) + 1
                self.apply_tag(f"callout_type_{ctype}", type_start, type_end)
                title_text = callout_match.group(4)
                if title_text.strip():
                    title_start = line_start_offset + callout_match.start(4)
                    title_end = line_start_offset + callout_match.end(4)
                    self.apply_tag("callout_title", title_start, title_end)

        # Pipe dimming for table lines
        if md_line.kind in ("table_row", "table_sep"):
            self._dim_table_pipes(line, line_start_offset, line_end_offset)

        # Lists: also tag the bullet/number separately
        if md_line.kind in ("ul", "ol") and md_line.marker:
            self.apply_tag(
                "list_bullet" if md_line.kind == "ul" else "list_number",
                line_start_offset + md_line.indent,
                line_start_offset + md_line.indent + len(md_line.marker),
            )

        self._apply_inline_tags(line, line_start_offset, line_end_offset, is_cursor)
        self._apply_list_hanging(line, line_start_offset, line_end_offset)
        self._apply_indent_hanging(line, line_start_offset, line_end_offset)

    def _collect_link_ranges(
        self, line: str, line_start_offset: int
    ) -> set[tuple[int, int]]:
        """Collect absolute buffer ranges for markdown links and images."""
        ranges: set[tuple[int, int]] = set()
        for m in self.re_links.finditer(line):
            ranges.add(
                (
                    line_start_offset + m.start(),
                    line_start_offset + m.end(),
                )
            )
        for m in self.re_autolink.finditer(line):
            ranges.add(
                (
                    line_start_offset + m.start(),
                    line_start_offset + m.end(),
                )
            )
        return ranges

    def _apply_inline_tags(
        self, line: str, line_start_offset: int, line_end_offset: int, is_cursor: bool
    ) -> None:
        """Apply inline patterns shared between the full and partial
        highlight passes."""

        # Collect link/image ranges FIRST so inline styles can skip them.
        link_ranges = self._collect_link_ranges(line, line_start_offset)

        # Checkboxes
        for m in self.re_checkbox_empty.finditer(line):
            self.apply_tag(
                "checkbox_empty",
                line_start_offset + m.start(),
                line_start_offset + m.end(),
            )
        for m in self.re_checkbox_checked.finditer(line):
            self.apply_tag(
                "checkbox_checked",
                line_start_offset + m.start(),
                line_start_offset + m.end(),
            )

        # Inline styles — skip matches that overlap link/image URLs
        self._inline(
            self.re_bold1,
            "bold",
            line,
            line_start_offset,
            is_cursor,
            exclude_ranges=link_ranges,
        )
        self._inline(
            self.re_bold2,
            "bold",
            line,
            line_start_offset,
            is_cursor,
            exclude_ranges=link_ranges,
        )
        self._inline(
            self.re_italic1,
            "italic",
            line,
            line_start_offset,
            is_cursor,
            single=True,
            exclude_ranges=link_ranges,
        )
        self._inline(
            self.re_italic2,
            "italic",
            line,
            line_start_offset,
            is_cursor,
            single=True,
            exclude_ranges=link_ranges,
        )
        self._inline(
            self.re_code,
            "code",
            line,
            line_start_offset,
            is_cursor,
            exclude_ranges=link_ranges,
        )
        self._inline(
            self.re_strikethrough,
            "strikethrough",
            line,
            line_start_offset,
            is_cursor,
            exclude_ranges=link_ranges,
        )

        # Links and images
        for m in self.re_links.finditer(line):
            fs = line_start_offset + m.start()
            fe = line_start_offset + m.end()
            mt = self._marker_tag(is_cursor)
            if m.group(1):  # [[wiki link]]
                self.apply_tag("internal-link", fs, fe)
                self.apply_tag(mt, fs, fs + 2)
                self.apply_tag(mt, fe - 2, fe)
            else:
                if m.group(2):  # ![image](...)
                    alt_s = fs + 2
                    alt_e = alt_s + len(m.group(3))
                    self.apply_tag("image", alt_s, alt_e)
                    self.apply_tag(mt, fs, fs + 1)  # !
                    self.apply_tag(mt, fs + 1, fs + 2)  # [
                    self.apply_tag(mt, alt_e, alt_e + 1)  # ]
                    self.apply_tag(mt, alt_e + 1, alt_e + 2)  # (
                    self.apply_tag(mt, alt_e + 2, fe - 1)  # url
                    self.apply_tag(mt, fe - 1, fe)  # )
                    em = _EMBED_SIZE_RE.match(m.group(3))
                    if em:
                        dim_start = alt_s + len(em.group(1))
                        self.apply_tag("dim", dim_start, alt_e)
                else:  # [text](url)
                    text_s = fs + 1
                    text_e = text_s + len(m.group(3))
                    self.apply_tag("external-link", text_s, text_e)
                    self.apply_tag(mt, fs, text_s)
                    self.apply_tag(mt, text_e, fe)

        # Deadlines and tags
        for m in self.re_deadline.finditer(line):
            self.apply_tag(
                "deadline", line_start_offset + m.start(), line_start_offset + m.end()
            )
        for m in self.re_tag.finditer(line):
            self.apply_tag(
                "tag", line_start_offset + m.start(), line_start_offset + m.end()
            )

        # Autolinks
        for m in self.re_autolink.finditer(line):
            self.apply_tag(
                "autolink", line_start_offset + m.start(), line_start_offset + m.end()
            )
            mt = "dim" if self.always_show_markdown else "invisible"
            self.apply_tag(
                mt, line_start_offset + m.start(), line_start_offset + m.start() + 1
            )
            self.apply_tag(
                mt, line_start_offset + m.end() - 1, line_start_offset + m.end()
            )

        # Inline HTML
        for m in self.re_html.finditer(line):
            content = m.group(0)
            is_autolink = "http" in content or ("@" in content and "<" in content)
            if not is_autolink and not content.startswith(("<!", "<?")):
                self.apply_tag(
                    "inline_html",
                    line_start_offset + m.start(),
                    line_start_offset + m.end(),
                )

        # Hard line breaks
        if line.rstrip().endswith("\\"):
            self.apply_tag(
                "line_break", line_start_offset + len(line.rstrip()), line_end_offset
            )

    # Incremental highlighter: only re-tag a single line range

    def highlight_line_range(
        self, start_line: int, end_line: int, cursor_line: int | None = None
    ):
        """Re-apply tags for lines from start_line to end_line (inclusive)."""
        from markdown.tokenizer import LineTokenizer

        tokenizer = LineTokenizer()
        code_block_lines = self._code_block_line_set()
        in_block = False
        # If start_line is inside a fenced code block, set in_block=True so the
        # tokenizer correctly identifies code lines (fences may start earlier).
        if start_line in code_block_lines:
            in_block = True
        callout_type: str | None = None
        # Scan backward from start_line to find active callout type when
        # the incremental pass starts in the middle of a callout block.
        for scan_line in range(start_line - 1, -1, -1):
            scan_it = self.get_iter_at_line(scan_line)
            scan_end = scan_it.copy()
            if not scan_end.ends_line():
                scan_end.forward_to_line_end()
            scan_text = self.buffer.get_text(scan_it, scan_end, True)
            if self.re_blockquote.match(scan_text):
                scan_callout = self.re_callout.match(scan_text)
                if scan_callout:
                    callout_type = resolve_callout_type(scan_callout.group(2))
                    break
            else:
                break
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

            # Front matter block at document start (--- ... ---)
            fm = self._front_matter_range()
            if fm is not None and fm[0] <= line_num <= fm[1]:
                self.apply_tag("dim", line_start, line_end)
                if line_num == fm[0] or line_num == fm[1]:
                    continue
                if ":" in line:
                    colon_pos = line.index(":")
                    self.apply_tag(
                        "front_matter_key", line_start, line_start + colon_pos
                    )
                    val_start = line_start + colon_pos + 1
                    if val_start < line_end:
                        self.apply_tag("front_matter_value", val_start, line_end)
                continue

            md, in_block = tokenizer.tokenize(line, in_fence=in_block)

            if md.kind == "code_block":
                self.apply_tag("code_block", line_start, line_end)
                continue

            if md.kind in ("code_fence_start", "code_fence_end"):
                self.apply_tag("dim", line_start, line_end)
                continue

            # Apply tags using the same logic as highlight() but line-local
            self._apply_line_tags(
                line_start,
                line_end,
                line,
                md,
                is_cursor=(line_num == cursor_line),
                line_num=line_num,
            )

            # Callout background & marker override
            if md.kind == "blockquote":
                callout_match = self.re_callout.match(line)
                if callout_match:
                    callout_type = resolve_callout_type(callout_match.group(2))
                    self.apply_tag(f"callout_bg_{callout_type}", line_start, line_end)
                elif callout_type:
                    self.apply_tag(f"callout_bg_{callout_type}", line_start, line_end)
                if callout_type:
                    bq_match = self.re_blockquote.match(line)
                    if bq_match:
                        marker_end = line_start + len(bq_match.group(1))
                        self.apply_tag(
                            f"callout_marker_{callout_type}",
                            line_start,
                            marker_end,
                        )
            else:
                callout_type = None

    def set_spell_checker(
        self, spell_checker: SpellChecker | None, enabled: bool = True
    ) -> None:
        self.spell_checker = spell_checker
        self.spell_check_enabled = enabled if spell_checker else False
        if self.enabled:
            self.highlight()

    _SPELL_WORD_RE = re.compile(r"[a-zA-Z\u00C0-\u024F]+(?:['\u2019][a-zA-Z]+)?")
    _INLINE_CODE_RE = re.compile(r"`[^`]*`")
    _AUTOLINK_RE = re.compile(r"<[^>]+>")
    _MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]+\)")
    _IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")

    def _spell_check_pass(
        self, start_line: int, end_line: int, code_block_lines: set[int] | None = None
    ) -> None:
        """Apply misspelled tag to misspelled words in [start_line, end_line)."""
        if not self.spell_checker or not self.spell_check_enabled:
            return

        if code_block_lines is None:
            code_block_lines = self._code_block_line_set()

        for line_num in range(start_line, end_line):
            if line_num in code_block_lines:
                continue
            it = self.get_iter_at_line(line_num)
            it_end = it.copy()
            if not it_end.ends_line():
                it_end.forward_to_line_end()
            line = self.buffer.get_text(it, it_end, True)
            line_start = it.get_offset()

            skip_ranges: set[tuple[int, int]] = set()
            for pattern in (
                self._INLINE_CODE_RE,
                self._AUTOLINK_RE,
                self._MD_LINK_RE,
                self._IMAGE_RE,
            ):
                for m in pattern.finditer(line):
                    skip_ranges.add((m.start(), m.end()))

            def in_skip(pos: int) -> bool:
                for s, e in skip_ranges:
                    if s <= pos < e:
                        return True
                return False

            # Batch all unique words, then check against dictionary once
            unique_words: set[str] = set()
            word_spans: list[tuple[str, int, int]] = []
            for m in self._SPELL_WORD_RE.finditer(line):
                word = m.group()
                if len(word) <= 1:
                    continue
                if word.isupper() and len(word) > 2:
                    continue
                if any(ch.isdigit() for ch in word):
                    continue
                if in_skip(m.start()):
                    continue
                unique_words.add(word.lower())
                word_spans.append((word, line_start + m.start(), line_start + m.end()))
            known = self.spell_checker.all_known_words(list(unique_words))
            for word, ws, we in word_spans:
                if word.lower() not in known:
                    self.apply_tag("misspelled", ws, we)

    def toggle_cursor_markers(self, prev_line: int, curr_line: int) -> None:
        """Toggle marker visibility tags when cursor moves between lines."""
        if prev_line != -1:
            self._set_line_markers(prev_line, is_cursor=False)
        self._set_line_markers(curr_line, is_cursor=True)

    def _set_line_markers(self, line_num: int, is_cursor: bool) -> None:
        """Apply only dim/invisible marker tags for a single line."""
        if self.always_show_markdown:
            return
        it = self.get_iter_at_line(line_num)
        it_end = it.copy()
        if not it_end.ends_line():
            it_end.forward_to_line_end()
        line = self.buffer.get_text(it, it_end, True)
        line_start = it.get_offset()

        # Remove existing dim/invisible tags from this line
        dim_tag = self.buffer.get_tag_table().lookup("dim")
        inv_tag = self.buffer.get_tag_table().lookup("invisible")
        if dim_tag:
            self.buffer.remove_tag(dim_tag, it, it_end)
        if inv_tag:
            self.buffer.remove_tag(inv_tag, it, it_end)

        # Code fence markers are always dim regardless of cursor
        if line.strip().startswith("```"):
            self.apply_tag("dim", line_start, line_start + len(line.rstrip()))
            return

        # Front matter lines — always dim, no cursor-sensitive markers.
        fm = self._front_matter_range()
        if fm is not None and fm[0] <= line_num <= fm[1]:
            self.apply_tag("dim", line_start, line_start + len(line.rstrip()))
            return

        mt = "dim" if is_cursor else "invisible"

        # ATX heading markers
        for m in self.re_header.finditer(line):
            self.apply_tag(mt, line_start, line_start + len(m.group(1)))

        # Inline formatting markers
        for pattern, single in (
            (self.re_bold1, False),
            (self.re_bold2, False),
            (self.re_italic1, True),
            (self.re_italic2, True),
            (self.re_code, False),
            (self.re_strikethrough, False),
        ):
            for m in pattern.finditer(line):
                if single:
                    self.apply_tag(
                        mt, line_start + m.start(), line_start + m.start() + 1
                    )
                    self.apply_tag(mt, line_start + m.end() - 1, line_start + m.end())
                else:
                    self.apply_tag(mt, line_start + m.start(1), line_start + m.end(1))
                    self.apply_tag(mt, line_start + m.start(3), line_start + m.end(3))

        # Links and images
        for m in self.re_links.finditer(line):
            ms = line_start + m.start()
            me = line_start + m.end()
            if m.group(1):  # [[wiki link]]
                if not is_cursor:
                    self.apply_tag("invisible", ms, ms + 2)
                    self.apply_tag("invisible", me - 2, me)
            elif m.group(2):  # ![image](...)
                alt_s = ms + 2
                alt_e = alt_s + len(m.group(3))
                self.apply_tag(mt, ms, ms + 1)
                self.apply_tag(mt, ms + 1, ms + 2)
                self.apply_tag(mt, alt_e, alt_e + 1)
                self.apply_tag(mt, alt_e + 1, alt_e + 2)
                self.apply_tag(mt, alt_e + 2, me - 1)
                self.apply_tag(mt, me - 1, me)
            else:  # [text](url)
                text_s = ms + 1
                text_e = text_s + len(m.group(3))
                self.apply_tag(mt, ms, text_s)
                self.apply_tag(mt, text_e, me)

        # Autolinks: brackets always invisible
        for m in self.re_autolink.finditer(line):
            self.apply_tag(
                "invisible", line_start + m.start(), line_start + m.start() + 1
            )
            self.apply_tag("invisible", line_start + m.end() - 1, line_start + m.end())

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self.highlight()
        else:
            start, end = self.buffer.get_bounds()
            self.buffer.remove_all_tags(start, end)
