"""Syntax highlighting management for markdown editor."""
from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from gi.repository import Gtk, Pango

if TYPE_CHECKING:
    pass

_COLOR_KEYS: tuple[str, ...] = (
    "h1", "h2", "h3", "h4", "code_bg", "code_fg", "code_block_bg", 
    "code_block_fg", "checkbox_empty", "checkbox_checked", 
    "internal_link", "external_link", "image", "tag", "deadline", 
    "hr", "bullet", "number", "table", "blockquote", "dim"
)

class MarkdownHighlighter:
    def __init__(self, buffer: Gtk.TextBuffer, theme_name: str = "tokyo-night") -> None:
        self.buffer: Gtk.TextBuffer = buffer
        self.enabled: bool = True
        self.theme_name: str = theme_name
        
        # Pre-compile regexes for performance
        self.re_fenced_code: re.Pattern = re.compile(r'```(\w*)\n?([\s\S]*?)```')
        self.re_setext_underline: re.Pattern = re.compile(r'^(\s*)(={3,}|-{3,})\s*$')
        self.re_list_bullet: re.Pattern = re.compile(r'^(\s*)([-*+])\s+')
        self.re_hr: re.Pattern = re.compile(r'^(\s*[-*_]){3,}\s*$')
        self.re_blockquote: re.Pattern = re.compile(r'^(\s*>)\s*(.*)$')
        self.re_unordered: re.Pattern = re.compile(r'^(\s*)([-*+])\s+(.+)$')
        self.re_ordered: re.Pattern = re.compile(r'^(\s*)(\d+\.)\s+(.+)$')
        self.re_table_row: re.Pattern = re.compile(r'^\s*\|.*\|\s*$')
        self.re_table_sep: re.Pattern = re.compile(r'^\s*\|?[\s\-:|]+\|?\s*$')
        self.re_header: re.Pattern = re.compile(r'^(#+)( .+)$')
        self.re_checkbox_empty: re.Pattern = re.compile(r'\[ \]')
        self.re_checkbox_checked: re.Pattern = re.compile(r'\[x\]')
        self.re_deadline: re.Pattern = re.compile(r'@\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?')
        self.re_tag: re.Pattern = re.compile(r'(?<!\w)#(\w+)')
        self.re_links: re.Pattern = re.compile(r'\[\[([^\]]+)\]\]|(!?)\[([^\]]+)\]\(([^)]+)\)')
        self.re_autolink: re.Pattern = re.compile(r'<([^>]+)>')
        self.re_html: re.Pattern = re.compile(r'<[^>]+>')
        
        # Inline styles
        self.re_bold1: re.Pattern = re.compile(r'(\*\*)([^*]+)(\*\*)')
        self.re_bold2: re.Pattern = re.compile(r'(__)([^_]+)(__)')
        self.re_italic1: re.Pattern = re.compile(r'(?<!\*)\*([^*]+)\*(?!\*)')
        self.re_italic2: re.Pattern = re.compile(r'(?<!_)_([^_]+)_(?!_)')
        self.re_code: re.Pattern = re.compile(r'(`)([^`]+)(`)')
        self.re_strikethrough: re.Pattern = re.compile(r'(~~)([^~]+)(~~)')
        
        self.setup_tags()

    def get_colors(self) -> dict[str, str]:
        """Returns theme colors."""
        if "light" in self.theme_name:
            return {
                "h1": "#34548a", "h2": "#5a4a78", "h3": "#33605a", "h4": "#8c4351",
                "code_bg": "#cbccd1", "code_fg": "#8f5e15", "code_block_bg": "#cbccd1",
                "code_block_fg": "#343b58", "checkbox_empty": "#8c4351",
                "checkbox_checked": "#485e30", "internal_link": "#8f5e15",
                "external_link": "#34548a", "image": "#33605a", "tag": "#5a4a78",
                "deadline": "#965027", "hr": "#9699a3", "bullet": "#34548a",
                "number": "#5a4a78", "table": "#5a4a78", "blockquote": "#485e30",
                "dim": "#9699a3"
            }
        return {
            "h1": "#7aa2f7", "h2": "#bb9af7", "h3": "#2ac3de", "h4": "#b4f9f8",
            "code_bg": "#292e42", "code_fg": "#e0af68", "code_block_bg": "#1a1b26",
            "code_block_fg": "#a9b1d6", "checkbox_empty": "#f7768e",
            "checkbox_checked": "#9ece6a", "internal_link": "#e0af68",
            "external_link": "#7aa2f7", "image": "#2ac3de", "tag": "#bb9af7",
            "deadline": "#ff9e64", "hr": "#565f89", "bullet": "#7aa2f7",
            "number": "#bb9af7", "table": "#bb9af7", "blockquote": "#9ece6a",
            "dim": "#565f89"
        }

    def setup_tags(self) -> None:
        """Sets up or updates text tags in the buffer."""
        table = self.buffer.get_tag_table()
        colors = self.get_colors()
        
        def add_or_update_tag(name: str, **kwargs: Any) -> None:
            tag = table.lookup(name)
            if tag:
                for prop, value in kwargs.items():
                    tag.set_property(prop, value)
            else:
                table.add(Gtk.TextTag(name=name, **kwargs))

        add_or_update_tag("h1", weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground=colors["h1"], left_margin=20)
        add_or_update_tag("h2", weight=Pango.Weight.BOLD, size=18 * Pango.SCALE, foreground=colors["h2"], left_margin=20)
        add_or_update_tag("h3", weight=Pango.Weight.BOLD, size=16 * Pango.SCALE, foreground=colors["h3"], left_margin=20)
        add_or_update_tag("h4", weight=Pango.Weight.BOLD, size=14 * Pango.SCALE, foreground=colors["h4"], left_margin=20)
        add_or_update_tag("body", left_margin=30)
        add_or_update_tag("code", family="Monospace", background=colors["code_bg"], foreground=colors["code_fg"])
        add_or_update_tag("code_block", family="Monospace", background=colors["code_block_bg"], foreground=colors["code_block_fg"])
        add_or_update_tag("code_fence", foreground=colors["dim"], weight=Pango.Weight.BOLD)
        add_or_update_tag("checkbox_empty", foreground=colors["checkbox_empty"], weight=Pango.Weight.BOLD)
        add_or_update_tag("checkbox_checked", foreground=colors["checkbox_checked"], weight=Pango.Weight.BOLD)
        add_or_update_tag("bold", weight=Pango.Weight.BOLD)
        add_or_update_tag("italic", style=Pango.Style.ITALIC)
        add_or_update_tag("internal-link", foreground=colors["internal_link"], weight=Pango.Weight.BOLD)
        add_or_update_tag("external-link", foreground=colors["external_link"], weight=Pango.Weight.BOLD)
        add_or_update_tag("image", foreground=colors["image"], style=Pango.Style.ITALIC)
        add_or_update_tag("tag", foreground=colors["tag"], weight=Pango.Weight.BOLD)
        add_or_update_tag("strikethrough", strikethrough=True)
        add_or_update_tag("deadline", foreground=colors["deadline"], style=Pango.Style.ITALIC)
        add_or_update_tag("hr", foreground=colors["hr"], weight=Pango.Weight.BOLD)
        add_or_update_tag("list_bullet", foreground=colors["bullet"], weight=Pango.Weight.BOLD)
        add_or_update_tag("list_number", foreground=colors["number"], weight=Pango.Weight.BOLD)
        add_or_update_tag("table_row", foreground=colors["table"], weight=Pango.Weight.BOLD)
        add_or_update_tag("table_sep", foreground=colors["hr"], weight=Pango.Weight.BOLD)
        add_or_update_tag("blockquote", foreground=colors["blockquote"], style=Pango.Style.ITALIC)
        add_or_update_tag("setext_header", weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground=colors["h1"])
        add_or_update_tag("setext_underline", foreground=colors["hr"])
        add_or_update_tag("setext_h1", weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground=colors["h1"])
        add_or_update_tag("setext_h2", weight=Pango.Weight.BOLD, size=18 * Pango.SCALE, foreground=colors["h2"])
        add_or_update_tag("autolink", foreground=colors["external_link"], underline=Pango.Underline.SINGLE)
        add_or_update_tag("inline_html", foreground=colors["checkbox_empty"])
        add_or_update_tag("line_break", weight=Pango.Weight.BOLD)
        add_or_update_tag("invisible", invisible=True)
        add_or_update_tag("dim", foreground=colors["dim"])

    def update_theme(self, theme_name: str) -> None:
        """Updates the theme and re-highlights."""
        self.theme_name = theme_name
        self.setup_tags()
        self.highlight()

    def get_iter_at_line(self, line: int) -> Gtk.TextIter:
        """Returns iter at line, handling the tuple-unwrap API safely."""
        result = self.buffer.get_iter_at_line(line)
        return result[1] if isinstance(result, tuple) else result

    def get_iter_at_offset(self, offset: int) -> Gtk.TextIter:
        """Returns iter at offset, handling the tuple-unwrap API safely."""
        result = self.buffer.get_iter_at_offset(offset)
        return result[1] if isinstance(result, tuple) else result

    def highlight(self, start_line: int = 0, end_line: int | None = None, cursor_line: int | None = None) -> None:
        """Performs syntax highlighting on a range of lines."""
        if not self.enabled:
            return

        total_lines = self.buffer.get_line_count()
        if end_line is None or end_line > total_lines:
            end_line = total_lines

        # Remove tags in the specified range
        start_iter = self.get_iter_at_line(start_line)
        end_iter = self.get_iter_at_line(end_line)
        if end_line == total_lines:
            end_iter = self.buffer.get_end_iter()
        self.buffer.remove_all_tags(start_iter, end_iter)
        
        text_range = self.buffer.get_text(start_iter, end_iter, True)
        
        # Handle Fenced Code Blocks (intentional: only partial re-highlights skip code block detection)
        if start_line == 0 and end_line == total_lines:
            for match in self.re_fenced_code.finditer(text_range):
                full_start = match.start()
                full_end = match.end()
                code_start = match.start(2)
                code_end = match.end(2)
                
                self.apply_tag("code_block", code_start, code_end)
                self.apply_tag("invisible", full_start, code_start)
                self.apply_tag("invisible", code_end, full_end)

        lines = text_range.split('\n')
        line_start_offset = start_iter.get_offset()
        
        for i, line in enumerate(lines):
            curr_line_num = start_line + i
            is_cursor_line = (cursor_line == curr_line_num)

            # Setext headings
            if curr_line_num > 0:
                # We need the previous line for Setext headings
                prev_line_iter = self.get_iter_at_line(curr_line_num - 1)
                prev_line_end = self.get_iter_at_line(curr_line_num)
                prev_line = self.buffer.get_text(prev_line_iter, prev_line_end, True).strip('\n')
                
                setext_underline = self.re_setext_underline.match(line)
                is_list_bullet = self.re_list_bullet.match(prev_line)
                
                if setext_underline and prev_line.strip() and not prev_line.strip().startswith('#') and not is_list_bullet:
                    self.apply_tag("setext_underline", line_start_offset, line_start_offset + len(line))
                    prev_offset = prev_line_iter.get_offset()
                    level = 1 if setext_underline.group(2)[0] == '=' else 2
                    tag = "setext_h1" if level == 1 else "setext_h2"
                    self.apply_tag(tag, prev_offset, prev_offset + len(prev_line))
                    line_start_offset += len(line) + 1
                    continue

            # Horizontal rules
            if self.re_hr.match(line):
                self.apply_tag("hr", line_start_offset, line_start_offset + len(line))
                line_start_offset += len(line) + 1
                continue

            # Block quotes
            blockquote_match = self.re_blockquote.match(line)
            if blockquote_match:
                self.apply_tag("blockquote", line_start_offset, line_start_offset + len(blockquote_match.group(1)))

            # Unordered lists
            unordered_match = self.re_unordered.match(line)
            if unordered_match:
                indent_len = len(unordered_match.group(1))
                bullet_len = len(unordered_match.group(2))
                self.apply_tag("list_bullet", line_start_offset + indent_len, line_start_offset + indent_len + bullet_len + 1)

            # Ordered lists
            ordered_match = self.re_ordered.match(line)
            if ordered_match:
                indent_len = len(ordered_match.group(1))
                number_len = len(ordered_match.group(2))
                self.apply_tag("list_number", line_start_offset + indent_len, line_start_offset + indent_len + number_len + 1)

            # Tables
            if '|' in line and not unordered_match and not ordered_match:
                if self.re_table_row.match(line):
                    if self.re_table_sep.match(line):
                        for m in re.finditer(r'\|', line):
                            self.apply_tag("table_sep", line_start_offset + m.start(), line_start_offset + m.start() + 1)
                    else:
                        for m in re.finditer(r'\|', line):
                            self.apply_tag("table_row", line_start_offset + m.start(), line_start_offset + m.start() + 1)

            # Headings
            header_match = self.re_header.match(line)
            if header_match:
                level = len(header_match.group(1))
                tag = f"h{min(level, 4)}"
                self.apply_tag(tag, line_start_offset, line_start_offset + len(line))
                if not is_cursor_line:
                    self.apply_tag("invisible", line_start_offset, line_start_offset + level)
                else:
                    self.apply_tag("dim", line_start_offset, line_start_offset + level)
                line_start_offset += len(line) + 1
                continue

            self.apply_tag("body", line_start_offset, line_start_offset + len(line))

            # Checkboxes
            for m in self.re_checkbox_empty.finditer(line):
                self.apply_tag("checkbox_empty", line_start_offset + m.start(), line_start_offset + m.end())
            for m in self.re_checkbox_checked.finditer(line):
                self.apply_tag("checkbox_checked", line_start_offset + m.start(), line_start_offset + m.end())
            
            # Deadlines
            for m in self.re_deadline.finditer(line):
                self.apply_tag("deadline", line_start_offset + m.start(), line_start_offset + m.end())

            # Tags
            for m in self.re_tag.finditer(line):
                self.apply_tag("tag", line_start_offset + m.start(), line_start_offset + m.end())

            # Links and Images
            for m in self.re_links.finditer(line):
                full_start = line_start_offset + m.start()
                full_end = line_start_offset + m.end()
                if m.group(1): # Internal link
                    self.apply_tag("internal-link", full_start, full_end)
                    if not is_cursor_line:
                        self.apply_tag("invisible", full_start, full_start + 2)
                        self.apply_tag("invisible", full_end - 2, full_end)
                else: 
                    is_image = bool(m.group(2))
                    link_text = m.group(3)
                    if is_image:
                        self.apply_tag("image", full_start, full_end)
                    else:
                        text_start = full_start + 1
                        text_end = text_start + len(link_text)
                        self.apply_tag("external-link", text_start, text_end)
                        if not is_cursor_line:
                            self.apply_tag("invisible", full_start, text_start)
                            self.apply_tag("invisible", text_end, full_end)
                        else:
                            self.apply_tag("dim", full_start, text_start)
                            self.apply_tag("dim", text_end, full_end)

            # Inline styles
            self.apply_inline_style(self.re_bold1, "bold", line, line_start_offset, is_cursor_line)
            self.apply_inline_style(self.re_bold2, "bold", line, line_start_offset, is_cursor_line)
            self.apply_inline_style(self.re_italic1, "italic", line, line_start_offset, is_cursor_line, True)
            self.apply_inline_style(self.re_italic2, "italic", line, line_start_offset, is_cursor_line, True)
            self.apply_inline_style(self.re_code, "code", line, line_start_offset, is_cursor_line)
            self.apply_inline_style(self.re_strikethrough, "strikethrough", line, line_start_offset, is_cursor_line)
            
            # Autolinks
            for m in self.re_autolink.finditer(line):
                self.apply_tag("autolink", line_start_offset + m.start(), line_start_offset + m.end())
                self.apply_tag("invisible", line_start_offset + m.start(), line_start_offset + m.start() + 1)
                self.apply_tag("invisible", line_start_offset + m.end() - 1, line_start_offset + m.end())

            # Inline HTML
            for m in self.re_html.finditer(line):
                content = m.group(0)
                is_autolink = (content.startswith('<http') or content.startswith('<https') or ('@' in content and '<' in content))
                if not is_autolink and not content.startswith('<!') and not content.startswith('<?'):
                    self.apply_tag("inline_html", line_start_offset + m.start(), line_start_offset + m.end())

            # Line breaks
            if line.rstrip().endswith('\\'):
                self.apply_tag("line_break", line_start_offset + len(line.rstrip()), line_start_offset + len(line))
            
            line_start_offset += len(line) + 1

    def apply_inline_style(self, pattern: re.Pattern, tag: str, line: str, line_offset: int, is_cursor_line: bool, is_single_marker: bool = False) -> None:
        """Applies an inline style tag."""
        for m in pattern.finditer(line):
            self.apply_tag(tag, line_offset + m.start(), line_offset + m.end())
            if not is_cursor_line:
                if is_single_marker:
                    self.apply_tag("invisible", line_offset + m.start(), line_offset + m.start() + 1)
                    self.apply_tag("invisible", line_offset + m.end() - 1, line_offset + m.end())
                else:
                    self.apply_tag("invisible", line_offset + m.start(1), line_offset + m.end(1))
                    self.apply_tag("invisible", line_offset + m.start(3), line_offset + m.end(3))
            else:
                if is_single_marker:
                    self.apply_tag("dim", line_offset + m.start(), line_offset + m.start() + 1)
                    self.apply_tag("dim", line_offset + m.end() - 1, line_offset + m.end())
                else:
                    self.apply_tag("dim", line_offset + m.start(1), line_offset + m.end(1))
                    self.apply_tag("dim", line_offset + m.start(3), line_offset + m.end(3))

    def apply_tag(self, tag_name: str, start_offset: int, end_offset: int) -> None:
        """Applies a tag, ensuring valid offsets."""
        if start_offset >= end_offset:
            return
        start_iter = self.get_iter_at_offset(start_offset)
        end_iter = start_iter.copy()
        end_iter.forward_chars(end_offset - start_offset)
        self.buffer.apply_tag_by_name(tag_name, start_iter, end_iter)

    def set_enabled(self, enabled: bool) -> None:
        """Enables or disables syntax highlighting."""
        self.enabled = enabled
        if not enabled:
            start, end = self.buffer.get_bounds()
            self.buffer.remove_all_tags(start, end)
        else:
            self.highlight()
