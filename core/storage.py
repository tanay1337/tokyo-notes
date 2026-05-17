"""Storage management — synchronous disk I/O with in-memory caching.

All public methods are safe to call from the GTK main thread. Writes use
an atomic write-then-rename pattern so a crash mid-write never truncates
a note file.

Performance note: reads are served from cache whenever the mtime matches
the last known value. After a full get_notes() scan all mtimes are already
known, so read_note() avoids a redundant stat() for up to _MTIME_TRUST_SECS
seconds before re-validating externally-modified files.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from core.utils import (
    CB_EXTRACT_RE,
    CB_UPDATE_RE,
    H1_TITLE_RE,
    get_snippet,
)

# How long to trust the cached mtime before re-stating the file.
# Covers the common case where no external editor is running.
_MTIME_TRUST_SECS: float = 30.0


class NotesManager:
    """Manages reading, writing, caching and querying of markdown note files."""

    def __init__(self, notes_dir: str | Path = "notes") -> None:
        self.notes_dir: Path = Path(notes_dir)
        self.notes_dir.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self._content_cache: dict[str, dict[str, Any]] = {}
        self._metadata_cache: dict[str, dict[str, Any]] = {}
        self._mtime_cache: dict[str, float] = {}
        self._last_full_scan: float = 0.0
        self._cleanup_stale_temps()

    def _cleanup_stale_temps(self) -> None:
        for stale in self.notes_dir.glob(".*.tmp"):
            try:
                stale.unlink()
            except OSError:
                pass

    # Querying

    def get_notes(self, search_text: str = "") -> list[str]:
        """Return all note stems sorted by mtime (newest first).

        As a side-effect, refreshes the mtime cache for every note found so
        that subsequent read_note() calls can skip stat() for up to
        _MTIME_TRUST_SECS seconds.
        """
        entries = [(p, p.stat()) for p in self.notes_dir.glob("*.md")]
        entries.sort(key=lambda x: x[1].st_mtime, reverse=True)

        with self._lock:
            for p, st in entries:
                self._mtime_cache[p.stem] = st.st_mtime
            self._last_full_scan = time.monotonic()

        note_names = [p.stem for p, _ in entries]

        if not search_text:
            return note_names

        sl = search_text.lower()
        filtered: list[str] = []
        for name in note_names:
            if sl in name.lower():
                filtered.append(name)
                continue
            if sl in self.read_note(name).lower():
                filtered.append(name)
        return filtered

    # Reading

    def read_note(self, name: str) -> str:
        """Return content of *name*.md from cache or disk.

        Skips stat() if the mtime was refreshed recently by get_notes() and
        the cache already has a matching entry. This makes search fast on
        large note collections.
        """
        note_path = self.notes_dir / f"{name}.md"
        if not note_path.exists():
            return ""

        with self._lock:
            cached_mtime = self._mtime_cache.get(name, 0)
            cached = self._content_cache.get(name)
            scan_fresh = (time.monotonic() - self._last_full_scan) < _MTIME_TRUST_SECS

        # Fast path: mtime from the recent scan matches cached content.
        if scan_fresh and cached and cached["mtime"] == cached_mtime:
            return cached["content"]

        # Slow path: stat to detect external edits (lock released only after read).
        current_mtime = note_path.stat().st_mtime
        with self._lock:
            self._mtime_cache[name] = current_mtime
            cached = self._content_cache.get(name)
            if cached and cached["mtime"] == current_mtime:
                return cached["content"]

        content = note_path.read_text(encoding="utf-8")
        with self._lock:
            self._content_cache[name] = {"content": content, "mtime": current_mtime}
        return content

    def get_metadata(self, name: str) -> dict[str, Any]:
        """Return cached metadata for *name* (snippet, links, checkboxes, mtime)."""
        note_path = self.notes_dir / f"{name}.md"
        if not note_path.exists():
            return {"snippet": "", "links": [], "checkboxes": [], "mtime": 0}

        with self._lock:
            cached_mtime = self._mtime_cache.get(name, 0)
            cached_meta = self._metadata_cache.get(name)
            scan_fresh = (time.monotonic() - self._last_full_scan) < _MTIME_TRUST_SECS

        if scan_fresh and cached_meta and cached_meta["mtime"] == cached_mtime:
            return cached_meta

        current_mtime = note_path.stat().st_mtime
        with self._lock:
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

        from core.utils import WIKI_CLICK_RE
        metadata = {
            "snippet":    get_snippet(content),
            "links":      WIKI_CLICK_RE.findall(content),
            "checkboxes": self._extract_checkboxes(name, content),
            "mtime":      current_mtime,
        }
        with self._lock:
            self._metadata_cache[name] = metadata
        return metadata

    def get_all_checkboxes(self, exclude: set[str] | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name in self.get_notes():
            if exclude and name in exclude:
                continue
            result.extend(self.get_metadata(name).get("checkboxes", []))
        return result

    # Writing (synchronous, atomic)

    def reserve_name(self, name: str = "Untitled") -> str:
        base = name
        counter = 1
        while (self.notes_dir / f"{name}.md").exists():
            name = f"{base} {counter}"
            counter += 1
        return name

    def create_note(self, name: str = "Untitled", content: str = "") -> str:
        base = name
        counter = 1
        while (self.notes_dir / f"{name}.md").exists():
            name = f"{base} {counter}"
            counter += 1
        self.save_note(name, content)
        return name

    def save_note(self, name: str, content: str) -> None:
        """Atomic write: write to .tmp then rename over the destination."""
        note_path = self.notes_dir / f"{name}.md"
        tmp_path  = self.notes_dir / f".{name}.tmp"
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(note_path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
        mtime = note_path.stat().st_mtime
        with self._lock:
            self._content_cache[name]  = {"content": content, "mtime": mtime}
            self._mtime_cache[name]    = mtime
            self._metadata_cache.pop(name, None)

    def delete_note(self, name: str) -> None:
        note_path = self.notes_dir / f"{name}.md"
        if note_path.exists():
            note_path.unlink()
        with self._lock:
            self._content_cache.pop(name, None)
            self._metadata_cache.pop(name, None)
            self._mtime_cache.pop(name, None)

    def rename_note(self, old_name: str, new_name: str) -> bool:
        """Synchronous rename. Returns True on success."""
        old_path = self.notes_dir / f"{old_name}.md"
        new_path = self.notes_dir / f"{new_name}.md"
        if not old_path.exists() or new_path.exists():
            return False
        old_path.rename(new_path)
        with self._lock:
            self._content_cache.pop(old_name, None)
            self._metadata_cache.pop(old_name, None)
            self._mtime_cache.pop(old_name, None)
        return True

    # Checkbox / deadline helpers

    def _extract_checkboxes(self, note_name: str, content: str) -> list[dict[str, Any]]:
        boxes: list[dict[str, Any]] = []
        for line_num, line in enumerate(content.split("\n"), 1):
            m = CB_EXTRACT_RE.match(line)
            if m:
                boxes.append({
                    "note":     note_name,
                    "text":     m.group(3).strip(),
                    "checked":  m.group(2).lower() == "x",
                    "line":     line_num,
                    "deadline": m.group(4),
                })
        return boxes

    def update_checkbox(self, note_name: str, line_num: int, checked: bool) -> bool:
        content = self.read_note(note_name)
        lines = content.split("\n")
        if 0 < line_num <= len(lines):
            m = CB_UPDATE_RE.match(lines[line_num - 1])
            if m:
                lines[line_num - 1] = (
                    f"{m.group(1)}{'x' if checked else ' '}{m.group(3)}"
                )
                self.save_note(note_name, "\n".join(lines))
                return True
        return False

    def update_deadline(
        self, note_name: str, line_num: int, new_deadline: str | None
    ) -> bool:
        content = self.read_note(note_name)
        lines = content.split("\n")
        if 0 < line_num <= len(lines):
            prefix = re.sub(
                r"\s*@\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?.*$",
                "",
                lines[line_num - 1],
            )
            lines[line_num - 1] = (
                f"{prefix.rstrip()} @{new_deadline}"
                if new_deadline
                else prefix.rstrip()
            )
            self.save_note(note_name, "\n".join(lines))
            return True
        return False
