"""Storage management for note file operations and caching."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from core.utils import H1_TITLE_RE, get_snippet  # noqa: F401 – H1_TITLE_RE re-exported

logger = logging.getLogger(__name__)


class NotesManager:
    """Manages reading, writing, caching and querying of markdown note files."""

    # Compiled at class level — shared across all instances.
    _CB_EXTRACT_RE: re.Pattern = re.compile(
        r"^(\s*)-\s*\[([ xX])\]\s*(.+?)(?:\s+@(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?))?$"
    )
    _CB_UPDATE_RE: re.Pattern = re.compile(r"^(\s*-\s*\[)([ xX])(\].*)")
    _LINK_RE: re.Pattern = re.compile(r"\[\[([^\]]+)\]\]")

    def __init__(self, notes_dir: str | Path = "notes") -> None:
        self.notes_dir: Path = Path(notes_dir)
        self.notes_dir.mkdir(exist_ok=True)
        self._content_cache: dict[str, dict[str, Any]] = {}
        self._metadata_cache: dict[str, dict[str, Any]] = {}
        self._mtime_cache: dict[str, float] = {}
        self._cleanup_stale_temps()

    def _cleanup_stale_temps(self) -> None:
        """Remove any .*.tmp files left by a previous crashed write."""
        for stale in self.notes_dir.glob(".*.tmp"):
            try:
                stale.unlink()
                logger.warning("Removed stale temp file: %s", stale)
            except OSError as e:
                logger.warning("Could not remove stale temp file %s: %s", stale, e)

    # ------------------------------------------------------------------ #
    # Querying
    # ------------------------------------------------------------------ #

    def get_notes(self, search_text: str = "") -> list[str]:
        """Return all note stems sorted by modification time (newest first).

        If *search_text* is given, only notes whose name or content contains
        the string (case-insensitive) are returned.
        """
        entries = [(p, p.stat()) for p in self.notes_dir.glob("*.md")]
        entries.sort(key=lambda x: x[1].st_mtime, reverse=True)

        for p, st in entries:
            self._mtime_cache[p.stem] = st.st_mtime

        note_names: list[str] = [p.stem for p, _ in entries]

        if not search_text:
            return note_names

        search_lower = search_text.lower()
        filtered: list[str] = []
        for name in note_names:
            if search_lower in name.lower():
                filtered.append(name)
                continue
            if search_lower in self.read_note(name).lower():
                filtered.append(name)
        return filtered

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def read_note(self, name: str) -> str:
        """Return the content of *name*.md, served from cache when still fresh.

        Always performs a stat() call to detect external edits — the mtime
        cache is only used to avoid redundant reads within the same get_notes()
        call cycle, not to skip the freshness check entirely.
        """
        note_path = self.notes_dir / f"{name}.md"
        if not note_path.exists():
            return ""

        current_mtime = note_path.stat().st_mtime
        self._mtime_cache[name] = current_mtime

        cached = self._content_cache.get(name)
        if cached and cached["mtime"] == current_mtime:
            return cached["content"]

        content = note_path.read_text(encoding="utf-8")
        self._content_cache[name] = {"content": content, "mtime": current_mtime}
        return content

    def get_metadata(self, name: str) -> dict[str, Any]:
        """Return cached metadata dict for *name* (snippet, links, checkboxes, mtime)."""
        note_path = self.notes_dir / f"{name}.md"
        if not note_path.exists():
            return {"snippet": "", "links": [], "checkboxes": [], "mtime": 0}

        current_mtime = note_path.stat().st_mtime
        self._mtime_cache[name] = current_mtime

        cached_meta = self._metadata_cache.get(name)
        if cached_meta and cached_meta["mtime"] == current_mtime:
            return cached_meta

        cached_content = self._content_cache.get(name)
        content = (
            cached_content["content"]
            if cached_content and cached_content["mtime"] == current_mtime
            else self.read_note(name)
        )

        metadata = {
            "snippet": get_snippet(content),
            "links": self._LINK_RE.findall(content),
            "checkboxes": self._extract_checkboxes(name, content),
            "mtime": current_mtime,
        }
        self._metadata_cache[name] = metadata
        return metadata

    def get_all_checkboxes(self, exclude: set[str] | None = None) -> list[dict[str, Any]]:
        """Return every checkbox from every non-excluded note."""
        result: list[dict[str, Any]] = []
        for note_name in self.get_notes():
            if exclude and note_name in exclude:
                continue
            result.extend(self.get_metadata(note_name).get("checkboxes", []))
        return result

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    def reserve_name(self, name: str = "Untitled") -> str:
        """Return a unique note stem without touching the filesystem.

        The name is only reserved in memory — no file is created until
        the user types content and the first save fires.
        """
        base_name = name
        counter = 1
        while (self.notes_dir / f"{name}.md").exists():
            name = f"{base_name} {counter}"
            counter += 1
        return name

    def create_note(self, name: str = "Untitled", content: str = "") -> str:
        """Create a note file with given name and content, returning the stem.

        Intended for the MCP server and other callers that supply content
        up front. Uses the same atomic write as save_note.
        """
        base_name = name
        counter = 1
        while (self.notes_dir / f"{name}.md").exists():
            name = f"{base_name} {counter}"
            counter += 1
        self.save_note(name, content)
        return name

    def save_note(self, name: str, content: str) -> None:
        """Write *content* to *name*.md using an atomic write-then-rename.

        The content is first written to a hidden temp file (.name.tmp) in the
        same directory. Once the write is complete, the temp file is renamed
        over the destination. On POSIX systems os.replace() (which Path.replace
        uses internally) is atomic, so the destination is never partially
        written — it transitions instantly from the old version to the new one.

        If the write fails, the temp file is deleted and the exception is
        re-raised so the caller can handle it.
        """
        note_path = self.notes_dir / f"{name}.md"
        tmp_path = self.notes_dir / f".{name}.tmp"
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(note_path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise

        mtime = note_path.stat().st_mtime
        self._content_cache[name] = {"content": content, "mtime": mtime}
        self._mtime_cache[name] = mtime
        self._metadata_cache.pop(name, None)

    def delete_note(self, name: str) -> None:
        """Delete *name*.md and purge it from all caches."""
        note_path = self.notes_dir / f"{name}.md"
        if note_path.exists():
            note_path.unlink()
        self._content_cache.pop(name, None)
        self._metadata_cache.pop(name, None)
        self._mtime_cache.pop(name, None)

    def rename_note(self, old_name: str, new_name: str) -> bool:
        """Rename *old_name*.md to *new_name*.md. Returns True on success."""
        old_path = self.notes_dir / f"{old_name}.md"
        new_path = self.notes_dir / f"{new_name}.md"
        if old_path.exists() and not new_path.exists():
            old_path.rename(new_path)
            self._content_cache.pop(old_name, None)
            self._metadata_cache.pop(old_name, None)
            self._mtime_cache.pop(old_name, None)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Checkbox / deadline helpers
    # ------------------------------------------------------------------ #

    def _extract_checkboxes(self, note_name: str, content: str) -> list[dict[str, Any]]:
        checkboxes: list[dict[str, Any]] = []
        for line_num, line in enumerate(content.split("\n"), 1):
            match = self._CB_EXTRACT_RE.match(line)
            if match:
                checkboxes.append({
                    "note":     note_name,
                    "text":     match.group(3).strip(),
                    "checked":  match.group(2).lower() == "x",
                    "line":     line_num,
                    "deadline": match.group(4),
                })
        return checkboxes

    def update_checkbox(self, note_name: str, line_num: int, checked: bool) -> bool:
        """Toggle the checked state on the checkbox at *line_num* (1-based)."""
        content = self.read_note(note_name)
        lines = content.split("\n")
        if 0 < line_num <= len(lines):
            match = self._CB_UPDATE_RE.match(lines[line_num - 1])
            if match:
                lines[line_num - 1] = (
                    f"{match.group(1)}{'x' if checked else ' '}{match.group(3)}"
                )
                self.save_note(note_name, "\n".join(lines))
                return True
        return False

    def update_deadline(
        self, note_name: str, line_num: int, new_deadline: str | None
    ) -> bool:
        """Replace (or remove) the @deadline tag on the checkbox at *line_num*."""
        content = self.read_note(note_name)
        lines = content.split("\n")
        if 0 < line_num <= len(lines):
            prefix = re.sub(
                r"\s*@\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?.*$",
                "",
                lines[line_num - 1],
            )
            lines[line_num - 1] = (
                f"{prefix.rstrip()} @{new_deadline}" if new_deadline else prefix.rstrip()
            )
            self.save_note(note_name, "\n".join(lines))
            return True
        return False
