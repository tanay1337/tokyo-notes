import re
from gi.repository import Gtk, Pango

class MarkdownHighlighter:
    def __init__(self, buffer):
        self.buffer = buffer
        self.enabled = True
        self.setup_tags()

    def setup_tags(self):
        table = self.buffer.get_tag_table()
        
        # Base styles for hierarchy
        if not table.lookup("h1"):
            table.add(Gtk.TextTag(name="h1", weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground="#7aa2f7", left_margin=20))
            table.add(Gtk.TextTag(name="h2", weight=Pango.Weight.BOLD, size=18 * Pango.SCALE, foreground="#bb9af7", left_margin=20))
            table.add(Gtk.TextTag(name="h3", weight=Pango.Weight.BOLD, size=16 * Pango.SCALE, foreground="#2ac3de", left_margin=20))
            table.add(Gtk.TextTag(name="h4", weight=Pango.Weight.BOLD, size=14 * Pango.SCALE, foreground="#b4f9f8", left_margin=20))

            table.add(Gtk.TextTag(name="body", left_margin=30))


            table.add(Gtk.TextTag(name="code", family="Monospace", background="#292e42", foreground="#e0af68"))
            table.add(Gtk.TextTag(name="code_block", family="Monospace", background="#1a1b26", foreground="#a9b1d6"))
            table.add(Gtk.TextTag(name="code_fence", foreground="#565f89", weight=Pango.Weight.BOLD))
            
            table.add(Gtk.TextTag(name="checkbox_empty", foreground="#f7768e", weight=Pango.Weight.BOLD))
            table.add(Gtk.TextTag(name="checkbox_checked", foreground="#9ece6a", weight=Pango.Weight.BOLD))
            
            table.add(Gtk.TextTag(name="bold", weight=Pango.Weight.BOLD))
            table.add(Gtk.TextTag(name="italic", style=Pango.Style.ITALIC))
            table.add(Gtk.TextTag(name="link", foreground="#7aa2f7", weight=Pango.Weight.BOLD))
            table.add(Gtk.TextTag(name="image", foreground="#2ac3de", style=Pango.Style.ITALIC))
            
            table.add(Gtk.TextTag(name="tag", foreground="#bb9af7", weight=Pango.Weight.BOLD))
            table.add(Gtk.TextTag(name="strikethrough", strikethrough=True))
            table.add(Gtk.TextTag(name="deadline", foreground="#ff9e64", style=Pango.Style.ITALIC))
            table.add(Gtk.TextTag(name="hr", foreground="#565f89", weight=Pango.Weight.BOLD))
            
            table.add(Gtk.TextTag(name="list_bullet", foreground="#7aa2f7", weight=Pango.Weight.BOLD))
            table.add(Gtk.TextTag(name="list_number", foreground="#bb9af7", weight=Pango.Weight.BOLD))
            
            table.add(Gtk.TextTag(name="table_row", foreground="#bb9af7", weight=Pango.Weight.BOLD))
            table.add(Gtk.TextTag(name="table_sep", foreground="#565f89", weight=Pango.Weight.BOLD))
            
            table.add(Gtk.TextTag(name="blockquote", foreground="#9ece6a", style=Pango.Style.ITALIC))
            
            table.add(Gtk.TextTag(name="setext_header", weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground="#7aa2f7"))
            table.add(Gtk.TextTag(name="setext_underline", foreground="#565f89"))
            table.add(Gtk.TextTag(name="setext_h1", weight=Pango.Weight.BOLD, size=22 * Pango.SCALE, foreground="#7aa2f7"))
            table.add(Gtk.TextTag(name="setext_h2", weight=Pango.Weight.BOLD, size=18 * Pango.SCALE, foreground="#bb9af7"))
            
            table.add(Gtk.TextTag(name="autolink", foreground="#7aa2f7", underline=Pango.Underline.SINGLE))
            table.add(Gtk.TextTag(name="inline_html", foreground="#f7768e"))
            table.add(Gtk.TextTag(name="line_break", weight=Pango.Weight.BOLD))
            
            table.add(Gtk.TextTag(name="invisible", invisible=True))
            table.add(Gtk.TextTag(name="dim", foreground="#565f89"))

    def get_iter_at_line(self, line):
        result = self.buffer.get_iter_at_line(line)
        return result[1] if isinstance(result, tuple) else result

    def get_iter_at_offset(self, offset):
        result = self.buffer.get_iter_at_offset(offset)
        return result[1] if isinstance(result, tuple) else result

    def highlight(self, cursor_line=None):
        if not self.enabled:
            return

        start, end = self.buffer.get_bounds()
        self.buffer.remove_all_tags(start, end)
        
        text = self.buffer.get_text(start, end, True)
        
        # Multi-line Code Blocks (fenced with ```)
        for match in re.finditer(r'```(\w*)\n?([\s\S]*?)```', text):
            full_start = match.start()
            full_end = match.end()
            
            lang = match.group(1)
            code_start = match.start(2)
            code_end = match.end(2)
            
            code_s_iter = self.get_iter_at_offset(code_start)
            code_e_iter = self.get_iter_at_offset(code_end)
            self.buffer.apply_tag_by_name("code_block", code_s_iter, code_e_iter)
            
            fence_start_s = self.get_iter_at_offset(full_start)
            fence_start_e = self.get_iter_at_offset(code_start)
            self.buffer.apply_tag_by_name("invisible", fence_start_s, fence_start_e)
            
            fence_end_s = self.get_iter_at_offset(code_end)
            fence_end_e = self.get_iter_at_offset(full_end)
            self.buffer.apply_tag_by_name("invisible", fence_end_s, fence_end_e)

        lines = text.split('\n')
        for i, line in enumerate(lines):
            line_iter = self.get_iter_at_line(i)
            line_start_offset = line_iter.get_offset()
            line_end_offset = line_start_offset + len(line)
            is_cursor_line = (cursor_line == i)

            # Setext headings (underline below text)
            if i > 0:
                prev_line = lines[i-1]
                # Require 3+ dashes or equals for Setext headings and exclude list bullets
                setext_underline = re.match(r'^(\s*)(={3,}|-{3,})\s*$', line)
                is_list_bullet = re.match(r'^(\s*)([-*+])\s+', prev_line)
                
                if setext_underline and prev_line.strip() and not prev_line.strip().startswith('#') and not is_list_bullet:
                    # This is a setext heading underline
                    self.apply_tag("setext_underline", line_start_offset, line_start_offset + len(line))
                    # Also style the previous line as a heading
                    prev_offset = self.get_iter_at_line(i-1).get_offset()
                    level = 1 if setext_underline.group(2)[0] == '=' else 2
                    tag = "setext_h1" if level == 1 else "setext_h2"
                    self.apply_tag(tag, prev_offset, prev_offset + len(prev_line))
                    continue

            # Horizontal rules (---, ***, ___ with 3+ characters)
            if re.match(r'^(\s*[-*_]){3,}\s*$', line):
                self.apply_tag("hr", line_start_offset, line_start_offset + len(line))
                continue

            # Block quotes (lines starting with >)
            blockquote_match = re.match(r'^(\s*>)\s*(.*)$', line)
            if blockquote_match:
                self.apply_tag("blockquote", line_start_offset, line_start_offset + len(blockquote_match.group(1)))

            # Unordered lists (-, *, +)
            unordered_match = re.match(r'^(\s*)([-*+])\s+(.+)$', line)
            if unordered_match:
                indent_len = len(unordered_match.group(1))
                bullet_len = len(unordered_match.group(2))
                self.apply_tag("list_bullet", line_start_offset + indent_len, line_start_offset + indent_len + bullet_len + 1)

            # Ordered lists (1., 2., etc.)
            ordered_match = re.match(r'^(\s*)(\d+\.)\s+(.+)$', line)
            if ordered_match:
                indent_len = len(ordered_match.group(1))
                number_len = len(ordered_match.group(2))
                self.apply_tag("list_number", line_start_offset + indent_len, line_start_offset + indent_len + number_len + 1)

            # Tables (lines with | that are not list items)
            if '|' in line and not unordered_match and not ordered_match:
                # Highlight the header row (first row with content)
                if re.match(r'^\s*\|.*\|\s*$', line):
                    # Check if it's a separator row (contains only |, -, :, spaces)
                    if re.search(r'\||\-', line):
                        if re.match(r'^\s*\|?[\s\-:|]+\|?\s*$', line):
                            # Separator row
                            for m in re.finditer(r'\|', line):
                                self.apply_tag("table_sep", line_start_offset + m.start(), line_start_offset + m.start() + 1)
                        else:
                            # Data row
                            for m in re.finditer(r'\|', line):
                                self.apply_tag("table_row", line_start_offset + m.start(), line_start_offset + m.start() + 1)
            # Detect headings
            header_match = re.match(r'^(#+)( .+)$', line)
            if header_match:
                level = len(header_match.group(1))
                tag = f"h{min(level, 4)}"
                self.apply_tag(tag, line_start_offset, line_end_offset)

                # Apply dim tags if not cursor line
                if not is_cursor_line:
                    self.apply_tag("invisible", line_start_offset, line_start_offset + level)
                else:
                    self.apply_tag("dim", line_start_offset, line_start_offset + level)
                continue

            # Apply 'body' tag (left_margin=80) to all non-heading lines
            self.apply_tag("body", line_start_offset, line_end_offset)


            for m in re.finditer(r'\[ \]', line):
                self.apply_tag("checkbox_empty", line_start_offset + m.start(), line_start_offset + m.end())
            for m in re.finditer(r'\[x\]', line):
                self.apply_tag("checkbox_checked", line_start_offset + m.start(), line_start_offset + m.end())
            
            # Deadlines
            for m in re.finditer(r'@\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?', line):
                self.apply_tag("deadline", line_start_offset + m.start(), line_start_offset + m.end())

            # Tags
            for m in re.finditer(r'(?<!\w)#(\w+)', line):
                self.apply_tag("tag", line_start_offset + m.start(), line_start_offset + m.end())

            # Links and Images
            for m in re.finditer(r'(!?)\[([^\]]+)\]\(([^)]+)\)', line):
                is_image = bool(m.group(1))
                link_text = m.group(2)
                url = m.group(3)
                
                full_match_start = line_start_offset + m.start()
                text_start = full_match_start + 1
                text_end = text_start + len(link_text)
                
                if is_image:
                    self.apply_tag("image", full_match_start, full_match_start + 2)
                else:
                    self.apply_tag("link", text_start, text_end)
                
                markdown_start = full_match_start
                markdown_end = line_start_offset + m.end()
                
                if not is_cursor_line:
                    self.apply_tag("invisible", markdown_start, text_start)
                    self.apply_tag("invisible", text_end, markdown_end)
                else:
                    self.apply_tag("dim", markdown_start, text_start)
                    self.apply_tag("dim", text_end, markdown_end)

            # Inline styles - FIX: Use lookarounds to prevent overlap
            self.apply_inline_style(r'(\*\*)([^*]+)(\*\*)', "bold", line, line_start_offset, is_cursor_line)
            self.apply_inline_style(r'(__)([^_]+)(__)', "bold", line, line_start_offset, is_cursor_line)
            self.apply_inline_style(r'(?<!\*)\*([^*]+)\*(?!\*)', "italic", line, line_start_offset, is_cursor_line, True)
            self.apply_inline_style(r'(?<!_)_([^_]+)_(?!_)', "italic", line, line_start_offset, is_cursor_line, True)
            self.apply_inline_style(r'(`)([^`]+)(`)', "code", line, line_start_offset, is_cursor_line)
            self.apply_inline_style(r'(~~)([^~]+)(~~)', "strikethrough", line, line_start_offset, is_cursor_line)
            
            # Autolinks (<https://...> or <email@example.com>)
            for m in re.finditer(r'<([^>]+)>', line):
                content = m.group(1)
                if content.startswith('http://') or content.startswith('https://') or '@' in content:
                    self.apply_tag("autolink", line_start_offset + m.start(), line_start_offset + m.end())
                    self.apply_tag("invisible", line_start_offset + m.start(), line_start_offset + m.start() + 1)
                    self.apply_tag("invisible", line_start_offset + m.end() - 1, line_start_offset + m.end())

            # Inline HTML (<tag>...</tag> or <tag />)
            for m in re.finditer(r'<[^>]+>', line):
                content = m.group(0)
                is_autolink = (content.startswith('<http') or content.startswith('<https') or ('@' in content and '<' in content))
                if not is_autolink and not content.startswith('<!') and not content.startswith('<?'):
                    self.apply_tag("inline_html", line_start_offset + m.start(), line_start_offset + m.end())

            # Line breaks (backslash at end of line)
            if line.rstrip().endswith('\\'):
                self.apply_tag("line_break", line_start_offset + len(line.rstrip()), line_start_offset + len(line))

    def apply_inline_style(self, pattern, tag, line, line_offset, is_cursor_line, is_single_marker=False):
        for m in re.finditer(pattern, line):
            self.apply_tag(tag, line_offset + m.start(), line_offset + m.end())
            
            if not is_cursor_line:
                if is_single_marker:
                    # For *italic*, markers are at start and end
                    self.apply_tag("invisible", line_offset + m.start(), line_offset + m.start() + 1)
                    self.apply_tag("invisible", line_offset + m.end() - 1, line_offset + m.end())
                else:
                    # Group 1 and 3 are markers
                    self.apply_tag("invisible", line_offset + m.start(1), line_offset + m.end(1))
                    self.apply_tag("invisible", line_offset + m.start(3), line_offset + m.end(3))
            else:
                if is_single_marker:
                    self.apply_tag("dim", line_offset + m.start(), line_offset + m.start() + 1)
                    self.apply_tag("dim", line_offset + m.end() - 1, line_offset + m.end())
                else:
                    self.apply_tag("dim", line_offset + m.start(1), line_offset + m.end(1))
                    self.apply_tag("dim", line_offset + m.start(3), line_offset + m.end(3))

    def apply_tag(self, tag_name, start_offset, end_offset):
        if start_offset >= end_offset:
            return
        start_iter = self.get_iter_at_offset(start_offset)
        end_iter = self.get_iter_at_offset(end_offset)
        self.buffer.apply_tag_by_name(tag_name, start_iter, end_iter)

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            start, end = self.buffer.get_bounds()
            self.buffer.remove_all_tags(start, end)
        else:
            self.highlight()
