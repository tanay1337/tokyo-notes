from pathlib import Path
import re

class NotesManager:
    def __init__(self, notes_dir="notes"):
        self.notes_dir = Path(notes_dir)
        self.notes_dir.mkdir(exist_ok=True)
        self._content_cache = {}

    def get_notes(self, search_text=""):
        """Returns a list of all .md files in the notes directory, sorted by modification time.
        Optionally filters by title or content if search_text is provided."""
        notes = list(self.notes_dir.glob("*.md"))
        notes.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        note_names = [n.stem for n in notes]
        if not search_text:
            return note_names
            
        search_text = search_text.lower()
        filtered_names = []
        for name in note_names:
            # Check title match
            if search_text in name.lower():
                filtered_names.append(name)
                continue
            
            # Check content match
            content = self.read_note(name).lower()
            if search_text in content:
                filtered_names.append(name)
                
        return filtered_names

    def read_note(self, name):
        """Reads the content of a note by its name with caching."""
        note_path = self.notes_dir / f"{name}.md"
        if note_path.exists():
            # Check mtime to invalidate cache
            mtime = note_path.stat().st_mtime
            if name in self._content_cache and self._content_cache[name]['mtime'] == mtime:
                return self._content_cache[name]['content']
            
            content = note_path.read_text(encoding="utf-8")
            self._content_cache[name] = {'content': content, 'mtime': mtime}
            return content
        return ""

    def save_note(self, name, content):
        """Saves content to a note file and updates cache."""
        note_path = self.notes_dir / f"{name}.md"
        note_path.write_text(content, encoding="utf-8")
        self._content_cache[name] = {'content': content, 'mtime': note_path.stat().st_mtime}

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

    def rename_note(self, old_name, new_name):
        """Renames a note file."""
        old_path = self.notes_dir / f"{old_name}.md"
        new_path = self.notes_dir / f"{new_name}.md"
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)
            return True
        return False

    def get_all_checkboxes(self):
        """Returns all checkboxes from all notes grouped by note."""
        checkboxes = []
        for note_name in self.get_notes():
            content = self.read_note(note_name)
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
