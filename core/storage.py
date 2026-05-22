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

import os
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
        self._backlink_cache: dict[str, tuple[list[str], float]] = {}
        self._content_index: dict[str, set[str]] = {}
        self._last_full_scan: float = 0.0
        self._cleanup_stale_temps()

    def _cleanup_stale_temps(self) -> None:
        for stale in self.notes_dir.glob(".*.tmp"):
            try:
                stale.unlink()
            except OSError:
                pass
        # Clean up leftover .enc.new files from crashed re-encryption
        for stale in self.notes_dir.glob("*.md.enc.new"):
            try:
                stale.unlink()
            except OSError:
                pass

    # Querying

    def get_notes(self, search_text: str = "") -> list[str]:
        """Return all note stems sorted by mtime (newest first).

        Scans both .md and .md.enc files. Encrypted notes are included
        in the list so they appear in the sidebar (with a lock icon).
        If both .md and .md.enc exist for the same note, .enc takes priority.
        """
        plain_entries: dict[str, tuple[Path, os.stat_result]] = {}
        enc_entries: dict[str, tuple[Path, os.stat_result]] = {}

        for p in self.notes_dir.glob("*.md"):
            try:
                plain_entries[p.stem] = (p, p.stat())
            except OSError:
                pass
        for p in self.notes_dir.glob("*.md.enc"):
            try:
                name = Path(p.stem).stem
                enc_entries[name] = (p, p.stat())
            except OSError:
                pass

        # Merge: encrypted takes priority over plain for the same name
        merged: dict[str, tuple[Path, os.stat_result, bool]] = {}
        for name, (p, st) in plain_entries.items():
            merged[name] = (p, st, False)
        for name, (p, st) in enc_entries.items():
            merged[name] = (p, st, True)

        entries = sorted(merged.values(), key=lambda x: x[1].st_mtime, reverse=True)

        with self._lock:
            for p, st, _is_enc in entries:
                name = Path(p.stem).stem if _is_enc else p.stem
                self._mtime_cache[name] = st.st_mtime
            self._last_full_scan = time.monotonic()

        note_names = []
        for p, _, is_enc in entries:
            name = Path(p.stem).stem if is_enc else p.stem
            note_names.append(name)

        if not search_text:
            return note_names

        sl = search_text.lower()
        filtered: list[str] = []
        for name in note_names:
            if sl in name.lower():
                filtered.append(name)
                continue
            if self._content_index_matches(name, sl):
                filtered.append(name)
        return filtered

    def _content_index_matches(self, name: str, search_lower: str) -> bool:
        """Check if *search_lower* appears in note content, using cached index."""
        cached_content = self._content_cache.get(name)
        if cached_content:
            return search_lower in cached_content["content"].lower()
        if self.is_encrypted(name):
            return False
        content = self.read_note(name)
        return search_lower in content.lower()

    # Reading

    def is_encrypted(self, name: str) -> bool:
        """Check if *name* has an encrypted .md.enc file on disk."""
        return (self.notes_dir / f"{name}.md.enc").exists()

    def get_encrypted_notes(self) -> set[str]:
        """Return the set of all note names that have .md.enc files."""
        result: set[str] = set()
        for p in self.notes_dir.glob("*.md.enc"):
            result.add(Path(p.stem).stem)
        return result

    def read_note(self, name: str) -> str:
        """Return content of *name*.md from cache or disk.

        If an .enc file exists, returns the raw ciphertext bytes as a
        latin-1 string (so it survives the cache without decoding errors).
        The caller (main.py) is responsible for decrypting with the session key.
        """
        enc_path = self.notes_dir / f"{name}.md.enc"
        plain_path = self.notes_dir / f"{name}.md"

        if enc_path.exists():
            return self._read_encrypted(name, enc_path)
        if not plain_path.exists():
            return ""

        with self._lock:
            cached_mtime = self._mtime_cache.get(name, 0)
            cached = self._content_cache.get(name)
            scan_fresh = (time.monotonic() - self._last_full_scan) < _MTIME_TRUST_SECS

        if scan_fresh and cached and cached["mtime"] == cached_mtime:
            return cached["content"]

        current_mtime = plain_path.stat().st_mtime
        with self._lock:
            self._mtime_cache[name] = current_mtime
            cached = self._content_cache.get(name)
            if cached and cached["mtime"] == current_mtime:
                return cached["content"]

        content = plain_path.read_text(encoding="utf-8")
        with self._lock:
            self._content_cache[name] = {"content": content, "mtime": current_mtime}
        return content

    def _read_encrypted(self, name: str, enc_path: Path) -> str:
        """Read encrypted file content. Returns raw bytes as latin-1 string."""
        with self._lock:
            cached_mtime = self._mtime_cache.get(name, 0)
            cached = self._content_cache.get(name)
            scan_fresh = (time.monotonic() - self._last_full_scan) < _MTIME_TRUST_SECS

        current_mtime = enc_path.stat().st_mtime
        if scan_fresh and cached and cached["mtime"] == current_mtime:
            return cached["content"]

        with self._lock:
            self._mtime_cache[name] = current_mtime
            cached = self._content_cache.get(name)
            if cached and cached["mtime"] == current_mtime:
                return cached["content"]

        raw_bytes = enc_path.read_bytes()
        content = raw_bytes.decode("latin-1")
        with self._lock:
            self._content_cache[name] = {"content": content, "mtime": current_mtime, "encrypted": True}
        return content

    def get_metadata(self, name: str) -> dict[str, Any]:
        """Return cached metadata for *name* (snippet, links, checkboxes, mtime).

        For encrypted notes, returns a placeholder snippet since content
        cannot be read without the session key.
        """
        enc_path = self.notes_dir / f"{name}.md.enc"
        plain_path = self.notes_dir / f"{name}.md"

        if enc_path.exists():
            return self._get_metadata_encrypted(name, enc_path)
        if not plain_path.exists():
            return {"snippet": "", "links": [], "checkboxes": [], "mtime": 0}

        with self._lock:
            cached_mtime = self._mtime_cache.get(name, 0)
            cached_meta = self._metadata_cache.get(name)
            scan_fresh = (time.monotonic() - self._last_full_scan) < _MTIME_TRUST_SECS

        if scan_fresh and cached_meta and cached_meta["mtime"] == cached_mtime:
            return cached_meta

        current_mtime = plain_path.stat().st_mtime
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

    def _get_metadata_encrypted(self, name: str, enc_path: Path) -> dict[str, Any]:
        """Return metadata for an encrypted note (placeholder snippet)."""
        with self._lock:
            cached_mtime = self._mtime_cache.get(name, 0)
            cached_meta = self._metadata_cache.get(name)
            scan_fresh = (time.monotonic() - self._last_full_scan) < _MTIME_TRUST_SECS

        current_mtime = enc_path.stat().st_mtime
        if scan_fresh and cached_meta and cached_meta["mtime"] == current_mtime:
            return cached_meta

        with self._lock:
            self._mtime_cache[name] = current_mtime
            cached_meta = self._metadata_cache.get(name)
            if cached_meta and cached_meta["mtime"] == current_mtime:
                return cached_meta

        metadata = {
            "snippet":    "Private note",
            "links":      [],
            "checkboxes": [],
            "mtime":      current_mtime,
            "encrypted":  True,
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
        while (self.notes_dir / f"{name}.md").exists() or (self.notes_dir / f"{name}.md.enc").exists():
            name = f"{base} {counter}"
            counter += 1
        return name

    def save_note(self, name: str, content: str, encrypt: bool = False) -> None:
        """Atomic write: write to .tmp then rename over the destination.

        If *encrypt* is True, *content* is treated as raw ciphertext bytes
        (encoded as latin-1) and written to .md.enc.
        """
        if encrypt:
            self._save_encrypted(name, content)
            return

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
        self._backlink_cache.clear()

    def _save_encrypted(self, name: str, ciphertext_latin1: str) -> None:
        """Save ciphertext to .md.enc file atomically."""
        enc_path = self.notes_dir / f"{name}.md.enc"
        tmp_path = self.notes_dir / f".{name}.tmp.enc"
        raw_bytes = ciphertext_latin1.encode("latin-1")
        try:
            tmp_path.write_bytes(raw_bytes)
            tmp_path.replace(enc_path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
        mtime = enc_path.stat().st_mtime
        with self._lock:
            self._content_cache[name] = {"content": ciphertext_latin1, "mtime": mtime, "encrypted": True}
            self._mtime_cache[name] = mtime
            self._metadata_cache.pop(name, None)
        self._backlink_cache.clear()

    def delete_note(self, name: str) -> None:
        note_path = self.notes_dir / f"{name}.md"
        enc_path = self.notes_dir / f"{name}.md.enc"
        if enc_path.exists():
            from core.encryption import secure_delete
            secure_delete(enc_path)
        if note_path.exists():
            note_path.unlink()
        with self._lock:
            self._content_cache.pop(name, None)
            self._metadata_cache.pop(name, None)
            self._mtime_cache.pop(name, None)
        self._backlink_cache.clear()

    def rename_note(self, old_name: str, new_name: str) -> bool:
        """Synchronous rename. Returns True on success."""
        old_path = self.notes_dir / f"{old_name}.md"
        new_path = self.notes_dir / f"{new_name}.md"
        old_enc = self.notes_dir / f"{old_name}.md.enc"
        new_enc = self.notes_dir / f"{new_name}.md.enc"

        if old_enc.exists():
            if new_enc.exists():
                return False
            old_enc.rename(new_enc)
        elif old_path.exists():
            if new_path.exists():
                return False
            old_path.rename(new_path)
        else:
            return False

        with self._lock:
            self._content_cache.pop(old_name, None)
            self._metadata_cache.pop(old_name, None)
            self._mtime_cache.pop(old_name, None)
        self._backlink_cache.clear()
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
                r"\s*@\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?",
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

    def get_backlinks(self, note_name: str, exclude_archived: set[str]) -> list[str]:
        """Return list of notes that link to *note_name* via [[wiki links]].

        Skips encrypted notes since their content cannot be searched without
        the session key. Results are cached; the cache is cleared on every
        save/delete/rename so no mtime invalidation is needed inside this method.
        """
        if note_name in self._backlink_cache:
            return self._backlink_cache[note_name][0]

        backlinks = []
        scan_time = time.monotonic()
        pattern = re.compile(rf"\[\[{re.escape(note_name)}\]\]")
        for note in self.get_notes():
            if note == note_name or note in exclude_archived:
                continue
            if self.is_encrypted(note):
                continue
            content = self.read_note(note)
            if pattern.search(content):
                backlinks.append(note)
        self._backlink_cache[note_name] = (backlinks, scan_time)
        return backlinks
