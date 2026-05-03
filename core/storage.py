from core.utils import get_snippet
import re
from pathlib import Path

class NotesManager:
    def __init__(self, notes_dir="notes"):
        self.notes_dir = Path(notes_dir)
        self.notes_dir.mkdir(exist_ok=True)
        self._content_cache = {}
        self._metadata_cache = {}
        self._mtime_cache = {}

    def get_notes(self, search_text="", archived_notes=None):
        """Returns a list of all .md files in the notes directory, sorted by modification time."""
        entries = [(p, p.stat()) for p in self.notes_dir.glob("*.md")]
        entries.sort(key=lambda x: x[1].st_mtime, reverse=True)
        
        # Pre-warm the mtime cache
        for p, st in entries:
            self._mtime_cache[p.stem] = st.st_mtime

        note_names = [p.stem for p, _ in entries]
        
        # Filter archived
        if archived_notes is not None:
            note_names = [n for n in note_names if n not in archived_notes]

        if not search_text:
            return note_names
            
        search_text = search_text.lower()
        filtered_names = []
        for name in note_names:
            if search_text in name.lower():
                filtered_names.append(name)
                continue
            
            content = self.read_note(name).lower()
            if search_text in content:
                filtered_names.append(name)
                
        return filtered_names

    def read_note(self, name):
        """Reads the content of a note by its name with caching."""
        note_path = self.notes_dir / f"{name}.md"
        if not note_path.exists():
            return ""
        
        mtime = self._mtime_cache.get(name) or note_path.stat().st_mtime
        cached = self._content_cache.get(name)
        if cached and cached['mtime'] == mtime:
            return cached['content']
            
        content = note_path.read_text(encoding="utf-8")
        self._content_cache[name] = {'content': content, 'mtime': mtime}
        self._mtime_cache[name] = mtime
        return content

    def get_metadata(self, name):
        """Returns metadata for a note (snippet, links, checkboxes, mtime) with caching."""
        note_path = self.notes_dir / f"{name}.md"
        if not note_path.exists():
            return {"snippet": "", "links": [], "checkboxes": [], "mtime": 0}
            
        mtime = self._mtime_cache.get(name) or note_path.stat().st_mtime
        cached_content = self._content_cache.get(name)
        cached_meta = self._metadata_cache.get(name)
        
        if cached_meta and cached_meta['mtime'] == mtime:
            return cached_meta
            
        # If cache miss or outdated, regenerate metadata
        # Use existing content cache if possible
        content = cached_content['content'] if cached_content and cached_content['mtime'] == mtime else self.read_note(name)
        snippet = get_snippet(content)
        links = re.findall(r'\[\[([^\]]+)\]\]', content)
        checkboxes = self._extract_checkboxes(name, content)
        
        metadata = {
            "snippet": snippet,
            "links": links,
            "checkboxes": checkboxes,
            "mtime": mtime
        }
        self._metadata_cache[name] = metadata
        return metadata

    def _extract_checkboxes(self, note_name, content):
        checkboxes = []
        lines = content.split('\n')
        for line_num, line in enumerate(lines, 1):
            match = re.match(r'^(\s*)-\s*\[([ x])\]\s*(.+?)(?:\s+@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?))?\s*$', line)
            if match:
                checked = match.group(2) == 'x'
                text = match.group(3).strip()
                deadline = match.group(4) if match.group(4) else None
                checkboxes.append({
                    'note': note_name,
                    'text': text,
                    'checked': checked,
                    'line': line_num,
                    'deadline': deadline
                })
        return checkboxes

    def save_note(self, name, content):
        """Saves content to a note file and updates cache."""
        note_path = self.notes_dir / f"{name}.md"
        note_path.write_text(content, encoding="utf-8")
        mtime = note_path.stat().st_mtime
        self._content_cache[name] = {'content': content, 'mtime': mtime}
        # Update metadata cache too
        self._metadata_cache[name] = {
            "snippet": get_snippet(content),
            "links": re.findall(r'\[\[([^\]]+)\]\]', content),
            "checkboxes": self._extract_checkboxes(name, content),
            "mtime": mtime
        }

    def create_note(self, name="Untitled"):
        """Returns a unique name for a new note, but does not create it on disk yet."""
        base_name = name
        counter = 1
        while (self.notes_dir / f"{name}.md").exists():
            name = f"{base_name} {counter}"
            counter += 1
        
        return name

    def delete_note(self, name):
        """Deletes a note file."""
        note_path = self.notes_dir / f"{name}.md"
        if note_path.exists():
            note_path.unlink()
        self._content_cache.pop(name, None)
        self._metadata_cache.pop(name, None)
        self._mtime_cache.pop(name, None)

    def rename_note(self, old_name, new_name):
        """Renames a note file."""
        old_path = self.notes_dir / f"{old_name}.md"
        new_path = self.notes_dir / f"{new_name}.md"
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)
            # Invalidate caches
            self._content_cache.pop(old_name, None)
            self._metadata_cache.pop(old_name, None)
            self._mtime_cache.pop(old_name, None)
            return True
        return False

    def get_all_checkboxes(self, exclude=None):
        """Returns all checkboxes from all notes grouped by note, optionally excluding some."""
        all_checkboxes = []
        for note_name in self.get_notes():
            if exclude and note_name in exclude:
                continue
            metadata = self.get_metadata(note_name)
            all_checkboxes.extend(metadata.get('checkboxes', []))
        return all_checkboxes

    def update_checkbox(self, note_name, line_num, checked):
        """Updates a checkbox state in a note."""
        content = self.read_note(note_name)
        lines = content.split('\n')
        if 0 < line_num <= len(lines):
            match = re.match(r'^(\s*-\s*\[)([ x])(\].*)$', lines[line_num - 1])
            if match:
                lines[line_num - 1] = f"{match.group(1)}{'x' if checked else ' '}{match.group(3)}"
                self.save_note(note_name, '\n'.join(lines))
                return True
        return False

    def update_deadline(self, note_name, line_num, new_deadline):
        """Updates a checkbox deadline in a note, replacing any existing deadline."""
        content = self.read_note(note_name)
        lines = content.split('\n')
        if 0 < line_num <= len(lines):
            line = lines[line_num - 1]
            # Match existing checkbox line and remove any existing deadline.
            # We look for the @ symbol and everything following it to strip it out,
            # allowing for any amount of preceding whitespace.
            prefix = re.sub(r'\s*@\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?.*$', '', line)
            new_line = f"{prefix.rstrip()} @{new_deadline}" if new_deadline else prefix.rstrip()
            lines[line_num - 1] = new_line
            self.save_note(note_name, '\n'.join(lines))
            return True
        return False
