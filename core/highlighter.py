import re
from gi.repository import Gtk, Pango

class MarkdownHighlighter:
    def __init__(self, buffer, theme_name="tokyo-night"):
        self.buffer = buffer
        self.enabled = True
        self.theme_name = theme_name
        self.setup_tags()

    def get_colors(self):
        if "light" in self.theme_name:
            return {
                "h1": "#34548a",
                "h2": "#5a4a78",
                "h3": "#33605a",
                "h4": "#8c4351",
                "code_bg": "#cbccd1",
                "code_fg": "#8f5e15",
                "code_block_bg": "#cbccd1",
                "code_block_fg": "#343b58",
                "checkbox_empty": "#8c4351",
                "checkbox_checked": "#485e30",
                "internal_link": "#8f5e15",
                "external_link": "#34548a",
                "image": "#33605a",
                "tag": "#5a4a78",
                "deadline": "#965027",
                "hr": "#9699a3",
                "bullet": "#34548a",
                "number": "#5a4a78",
                "table": "#5a4a78",
                "blockquote": "#485e30",
                "dim": "#9699a3"
            }
        else:
            return {
                "h1": "#7aa2f7",
                "h2": "#bb9af7",
                "h3": "#2ac3de",
                "h4": "#b4f9f8",
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
                "dim": "#565f89"
            }

    def setup_tags(self):
        table = self.buffer.get_tag_table()
        colors = self.get_colors()
        
        # Helper to add or update tag
        def add_or_update_tag(name, **kwargs):
            tag = table.lookup(name)
            if tag:
                for prop, value in kwargs.items():
                    tag.set_property(prop, value)
            else:
                table.add(Gtk.TextTag(name=name, **kwargs))

        # Base styles for hierarchy
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

    def update_theme(self, theme_name):
        self.theme_name = theme_name
        self.setup_tags()
        self.highlight()

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
            for m in re.finditer(r'\[\[([^\]]+)\]\]|(!?)\[([^\]]+)\]\(([^)]+)\)', line):
                full_start = line_start_offset + m.start()
                full_end = line_start_offset + m.end()
                
                if m.group(1): # Internal link [[Note]]
                    self.apply_tag("internal-link", full_start, full_end)
                    if not is_cursor_line:
                        self.apply_tag("invisible", full_start, full_start + 2)
                        self.apply_tag("invisible", full_end - 2, full_end)
                else: 
                    # External link [Text](url) or Image ![Alt](url)
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
