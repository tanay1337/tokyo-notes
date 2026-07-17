"""Storage management — synchronous disk I/O with in-memory caching.

All public methods are safe to call from the GTK main thread. Writes use
an atomic write-then-rename pattern so a crash mid-write never truncates
a note file.

Performance note: reads are served from cache whenever the mtime matches
the last known value. After a full get_notes() scan all mtimes are already
known, so read_plain() / read_encrypted_raw() avoid a redundant stat()
for up to _MTIME_TRUST_SECS seconds before re-validating externally-modified files.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from core.utils import (
    CB_EXTRACT_RE,
    CB_UPDATE_RE,
    MD_URL_BALANCED,
    get_snippet,
)

# How long to trust the cached mtime before re-stating the file.
# Covers the common case where no external editor is running.
_MTIME_TRUST_SECS: float = 5.0

# Maximum number of notes to keep in memory caches. Beyond this, the
# least-recently-inserted entries are evicted.
_MAX_CACHE_SIZE = 500

logger = logging.getLogger(__name__)


class _BoundedDict(OrderedDict):
    """OrderedDict that evicts oldest items when *maxlen* is exceeded."""

    def __init__(self, maxlen: int = _MAX_CACHE_SIZE, *args, **kwargs) -> None:
        self.maxlen = maxlen
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        if len(self) > self.maxlen:
            self.popitem(last=False)


class NotesManager:
    """Manages reading, writing, caching and querying of markdown note files."""

    def __init__(self, notes_dir: str | Path = "notes") -> None:
        self.notes_dir: Path = Path(notes_dir)
        self.notes_dir.mkdir(exist_ok=True)
        self.notes_dir.chmod(0o700)
        self._lock = threading.RLock()
        self._content_cache: dict[str, dict[str, Any]] = _BoundedDict()
        self._metadata_cache: dict[str, dict[str, Any]] = _BoundedDict()
        self._mtime_cache: dict[str, float] = _BoundedDict()
        self._backlink_cache: dict[str, tuple[list[str], float]] = _BoundedDict()
        self._checkbox_index: dict[str, list[dict[str, Any]]] = {}
        self._link_index: dict[str, set[str]] = {}
        self._backlink_index: dict[str, set[str]] = {}
        self._last_full_scan: float = 0.0
        self._io_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._save_seq: dict[str, int] = {}
        self.sort_order: str = "last_modified"
        self._cleanup_stale_temps()

    def _cleanup_stale_temps(self) -> None:
        for stale in self.notes_dir.glob("**/.*.tmp"):
            try:
                stale.unlink()
            except OSError:
                pass
        # Clean up leftover .enc.new files from crashed re-encryption
        for stale in self.notes_dir.glob("**/*.md.enc.new"):
            try:
                stale.unlink()
            except OSError:
                pass

    # Note name validation — prevents path traversal via crafted note names.

    _VALID_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\]]+(/[^\\/:*?\"<>|\]]+)*$")

    @classmethod
    def validate_name(cls, name: str) -> str:
        """Validate *name* is safe for use as a filename.

        Raises ValueError for names that could escape the notes directory.
        Returns the name on success for convenient chaining.
        """
        if not name or not name.strip():
            raise ValueError("Note name cannot be empty")

        # Prevent Windows reserved names
        base = name.split("/")[-1]
        if base.lower() in (
            "con",
            "prn",
            "aux",
            "nul",
            "com1",
            "com2",
            "com3",
            "com4",
            "com5",
            "com6",
            "com7",
            "com8",
            "com9",
            "lpt1",
            "lpt2",
            "lpt3",
            "lpt4",
            "lpt5",
            "lpt6",
            "lpt7",
            "lpt8",
            "lpt9",
        ):
            raise ValueError(f"Reserved note name: {name!r}")

        if (
            ".." in name
            or "\\" in name
            or name.startswith("/")
            or name.endswith("/")
            or "//" in name
        ):
            raise ValueError(f"Invalid note name: {name!r}")
        if not cls._VALID_NAME_RE.match(name):
            bad = next((c for c in name if c in '\\/:*?"<>|]'), None)
            if bad:
                raise ValueError(f"Symbol '{bad}' is not allowed in note/folder names")
            raise ValueError(f"Invalid note name: {name!r}")
        if name.endswith(".") or name.endswith(" "):
            raise ValueError("Note/folder name cannot end with '.' or space")

        # Null byte check
        if "\0" in name:
            raise ValueError("Note name contains null byte")

        return name

    # Querying

    def get_notes(
        self, search_text: str = "", sort_by_mtime: bool = False
    ) -> list[str]:
        """Return all note stems.

        By default sorted per the user's configured sort_order.
        Pass *sort_by_mtime=True* to force mtime order (newest first)
        regardless of the configured sort_order.

        Scans both .md and .md.enc files. Encrypted notes are included
        in the list so they appear in the sidebar (with a lock icon).
        If both .md and .md.enc exist for the same note, .enc takes priority.
        """
        plain_entries: dict[str, tuple[Path, os.stat_result]] = {}
        enc_entries: dict[str, tuple[Path, os.stat_result]] = {}

        for p in self.notes_dir.glob("**/*.md"):
            try:
                # Skip files inside hidden directories (e.g. .templates/, .git/)
                rel = p.relative_to(self.notes_dir)
                if any(part.startswith(".") for part in rel.parts[:-1]):
                    continue
                name = str(p.relative_to(self.notes_dir).with_suffix(""))
                plain_entries[name] = (p, p.stat())
            except OSError:
                pass
        for p in self.notes_dir.glob("**/*.md.enc"):
            try:
                rel = p.relative_to(self.notes_dir)
                if any(part.startswith(".") for part in rel.parts[:-1]):
                    continue
                stem = rel.stem
                if stem.endswith(".md"):
                    stem = stem[:-3]
                name = str(rel.parent / stem)
                enc_entries[name] = (p, p.stat())
            except OSError:
                pass

        # Merge: encrypted takes priority over plain for the same name
        merged: dict[str, tuple[Path, os.stat_result, bool]] = {}
        for name, (p, st) in plain_entries.items():
            merged[name] = (p, st, False)
        for name, (p, st) in enc_entries.items():
            merged[name] = (p, st, True)

        entries = [(name, p, st, enc) for name, (p, st, enc) in merged.items()]
        if sort_by_mtime:
            entries.sort(key=lambda x: x[2].st_mtime, reverse=True)
        elif self.sort_order == "a_to_z":
            entries.sort(key=lambda x: x[0].lower())
        elif self.sort_order == "z_to_a":
            entries.sort(key=lambda x: x[0].lower(), reverse=True)
        elif self.sort_order == "first_modified":
            entries.sort(key=lambda x: x[2].st_mtime)
        else:  # "last_modified" (default)
            entries.sort(key=lambda x: x[2].st_mtime, reverse=True)

        with self._lock:
            for name, _p, st, _is_enc in entries:
                self._mtime_cache[name] = st.st_mtime
            self._last_full_scan = time.monotonic()

        note_names = [name for name, _p, _st, _is_enc in entries]

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
            content = cached_content["content"]
            if isinstance(content, bytes):
                return False
            return search_lower in content.lower()
        if self.is_encrypted(name):
            return False
        content = self.read_plain(name)
        return search_lower in content.lower()

    # Reading

    def is_encrypted(self, name: str) -> bool:
        """Check if *name* has an encrypted .md.enc file on disk."""
        self.validate_name(name)
        return (self.notes_dir / f"{name}.md.enc").exists()

    def get_encrypted_notes(self) -> set[str]:
        """Return the set of all note names that have .md.enc files."""
        result: set[str] = set()
        for p in self.notes_dir.glob("**/*.md.enc"):
            rel = p.relative_to(self.notes_dir)
            if any(part.startswith(".") for part in rel.parts[:-1]):
                continue
            stem = rel.stem
            if stem.endswith(".md"):
                stem = stem[:-3]
            result.add(str(rel.parent / stem))
        return result

    def get_folders(self) -> list[str]:
        """Return sorted list of all non-hidden subdirectories under *notes_dir*.

        Includes empty directories (unlike the previous file-scoped scan)
        so that newly created empty folders appear in the sidebar.
        """
        folders: set[str] = set()
        for p in self.notes_dir.glob("**/"):
            rel = p.relative_to(self.notes_dir)
            if str(rel) == ".":
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            folders.add(str(rel))
        return sorted(folders)

    def get_notes_in_folder(self, folder: str) -> list[str]:
        """Return all note names (qualified) under *folder*."""
        prefix = f"{folder}/"
        return [n for n in self.get_notes() if n.startswith(prefix) or n == folder]

    def read_plain(self, name: str) -> str:
        """Return UTF-8 text content of *name*.md from cache or disk.

        Returns empty string if the file does not exist.
        """
        self.validate_name(name)
        plain_path = self.notes_dir / f"{name}.md"
        if not plain_path.exists():
            return ""

        with self._lock:
            cached_mtime = self._mtime_cache.get(name, 0)
            cached = self._content_cache.get(name)
            scan_fresh = (time.monotonic() - self._last_full_scan) < _MTIME_TRUST_SECS

        if (
            scan_fresh
            and cached
            and not cached.get("encrypted")
            and cached["mtime"] == cached_mtime
        ):
            return cached["content"]

        current_mtime = plain_path.stat().st_mtime
        with self._lock:
            self._mtime_cache[name] = current_mtime
            cached = self._content_cache.get(name)
            if (
                cached
                and not cached.get("encrypted")
                and cached["mtime"] == current_mtime
            ):
                return cached["content"]

        content = plain_path.read_text(encoding="utf-8")
        with self._lock:
            self._content_cache[name] = {"content": content, "mtime": current_mtime}
        return content

    def read_encrypted_raw(self, name: str) -> bytes:
        """Return raw ciphertext bytes of *name*.md.enc from cache or disk.

        Raises FileNotFoundError if the encrypted file does not exist.
        Callers should check is_encrypted() first.
        """
        self.validate_name(name)
        enc_path = self.notes_dir / f"{name}.md.enc"

        with self._lock:
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

        raw = enc_path.read_bytes()
        with self._lock:
            self._content_cache[name] = {
                "content": raw,
                "mtime": current_mtime,
                "encrypted": True,
            }
        return raw

    def get_metadata(self, name: str) -> dict[str, Any]:
        """Return cached metadata for *name* (snippet, links, checkboxes, mtime).

        For encrypted notes, returns a placeholder snippet since content
        cannot be read without the session key.
        """
        self.validate_name(name)
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
            if cached_content
            and not cached_content.get("encrypted")
            and cached_content["mtime"] == current_mtime
            else self.read_plain(name)
        )

        from core.utils import WIKI_CLICK_RE

        metadata = {
            "snippet": get_snippet(content),
            "links": WIKI_CLICK_RE.findall(content),
            "checkboxes": self._extract_checkboxes(name, content),
            "mtime": current_mtime,
        }
        with self._lock:
            self._metadata_cache[name] = metadata
        return metadata

    def _get_metadata_encrypted(self, name: str, enc_path: Path) -> dict[str, Any]:
        """Return metadata for an encrypted note (placeholder snippet)."""
        with self._lock:
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
            "snippet": "Private note",
            "links": [],
            "checkboxes": [],
            "mtime": current_mtime,
            "encrypted": True,
        }
        with self._lock:
            self._metadata_cache[name] = metadata
        return metadata

    def get_all_checkboxes(
        self, exclude: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return all checkbox metadata across every note (uses incremental index)."""
        if not self._checkbox_index:
            self._rebuild_checkbox_index()
        result: list[dict[str, Any]] = []
        for name, boxes in self._checkbox_index.items():
            if exclude and name in exclude:
                continue
            result.extend(boxes)
        return result

    def _rebuild_checkbox_index(self) -> None:
        self._checkbox_index.clear()
        for name in self.get_notes():
            meta = self.get_metadata(name)
            boxes = meta.get("checkboxes", [])
            if boxes:
                self._checkbox_index[name] = boxes

    def _update_checkbox_index(self, name: str) -> None:
        if name in self._content_cache and not self._content_cache[name].get(
            "encrypted"
        ):
            content = self._content_cache[name]["content"]
            boxes = self._extract_checkboxes(name, content)
            if boxes:
                self._checkbox_index[name] = boxes
            else:
                self._checkbox_index.pop(name, None)
        elif name in self._checkbox_index:
            self._checkbox_index.pop(name, None)

    def _update_link_index(self, name: str, content: str) -> None:
        from core.utils import WIKI_CLICK_RE

        for linked in self._link_index.get(name, set()):
            if linked in self._backlink_index:
                self._backlink_index[linked].discard(name)
                if not self._backlink_index[linked]:
                    self._backlink_index.pop(linked, None)
        links = set(WIKI_CLICK_RE.findall(content))
        self._link_index[name] = links
        for linked in links:
            if linked == name:
                continue
            self._backlink_index.setdefault(linked, set()).add(name)

    # Writing (synchronous, atomic)

    def reserve_name(self, name: str = "Untitled") -> str:
        """Return a unique note name by appending a counter if needed."""
        self.validate_name(name)
        base = name
        counter = 1
        while (self.notes_dir / f"{name}.md").exists() or (
            self.notes_dir / f"{name}.md.enc"
        ).exists():
            name = f"{base} {counter}"
            counter += 1
        return name

    def save_note(self, name: str, content: str) -> None:
        """Atomic write: write to .tmp then rename over the destination.

        Writes plain UTF-8 markdown to *name*.md.
        For encrypted notes, use save_encrypted() instead.
        """
        self.validate_name(name)
        with self._lock:
            self._save_seq[name] = self._save_seq.get(name, 0) + 1
        self._sync_save_note(name, content)
        self._backlink_cache.clear()

    def save_note_async(self, name: str, content: str) -> concurrent.futures.Future:
        """Save note on a background thread. Returns a Future.

        The caller should attach a done callback (via future.add_done_callback
        or GLib.idle_add) to update UI state after completion.
        """
        self.validate_name(name)
        with self._lock:
            seq = self._save_seq.get(name, 0) + 1
            self._save_seq[name] = seq
        return self._io_executor.submit(self._sync_save_note, name, content, seq)

    def _sync_save_note(
        self, name: str, content: str, save_seq: int | None = None
    ) -> None:
        """Low-level synchronous save (runs on IO thread when called async).

        When *save_seq* is provided (async path), the write is skipped if
        a newer save was submitted after this one, preventing an inflight
        async task from overwriting a more recent synchronous save.
        """
        if save_seq is not None:
            with self._lock:
                current = self._save_seq.get(name, 0)
                if save_seq < current:
                    return

        import tempfile

        note_path = self.notes_dir / f"{name}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        safe_prefix = f".{name.replace('/', '_')}-"
        fd, tmp_name = tempfile.mkstemp(
            dir=note_path.parent, prefix=safe_prefix, suffix=".tmp"
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_name, note_path)
        except OSError:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

        mtime = note_path.stat().st_mtime
        with self._lock:
            self._content_cache[name] = {"content": content, "mtime": mtime}
            self._mtime_cache[name] = mtime
            self._metadata_cache.pop(name, None)
        self._update_checkbox_index(name)
        self._update_link_index(name, content)
        self._backlink_cache.clear()

        threading.Thread(
            target=self._cleanup_orphan_images,
            args=(name, content),
            daemon=True,
        ).start()

    def _cleanup_orphan_images(self, note_name: str, current_content: str) -> None:
        """Remove .images/ and .documents/ files not referenced in content."""
        referenced = self._extract_image_paths(current_content)

        for subdir in (".images", ".documents"):
            attach_dir = self.notes_dir / subdir
            if not attach_dir.exists():
                continue
            for f in list(attach_dir.iterdir()):
                if not f.is_file():
                    continue
                # Preserve remote cache files — they're managed by the
                # download system and referenced via URL in the markdown,
                # not by a literal path.
                if f.name.startswith("remote_"):
                    continue
                rel = f"{subdir}/{f.name}"
                if rel in referenced:
                    continue
                if self._is_image_used_by_other_notes(rel, note_name):
                    continue
                try:
                    f.unlink()
                    logger.info("Removed orphan %s: %s", subdir, rel)
                except OSError as exc:
                    logger.warning(
                        "Failed to remove orphan %s %s: %s", subdir, rel, exc
                    )

        # Remote PDF cleanup: collect all PDF URL hashes across notes
        docs_dir = self.notes_dir / ".documents"
        if docs_dir.exists():
            active_hashes: set[str] = set()
            for other in self.get_notes():
                if other == note_name:
                    content = current_content
                elif not self.is_encrypted(other):
                    try:
                        content = self.read_plain(other)
                    except Exception:
                        continue
                else:
                    continue
                active_hashes.update(self._extract_remote_pdf_hashes(content))

            for f in list(docs_dir.iterdir()):
                if not f.name.startswith("remote_"):
                    continue
                file_hash = f.name.removeprefix("remote_").removesuffix(".pdf")
                if file_hash and file_hash not in active_hashes:
                    try:
                        f.unlink()
                        logger.info("Removed orphan remote PDF: %s", f.name)
                    except OSError as exc:
                        logger.warning(
                            "Failed to remove remote PDF %s: %s", f.name, exc
                        )

    def save_encrypted(self, name: str, ciphertext: bytes) -> None:
        """Write encrypted *ciphertext* to disk for *name*."""
        self.validate_name(name)
        with self._lock:
            self._save_seq[name] = self._save_seq.get(name, 0) + 1
        self._sync_save_encrypted(name, ciphertext)
        self._backlink_cache.clear()

    def save_encrypted_async(
        self, name: str, ciphertext: bytes
    ) -> concurrent.futures.Future:
        """Save encrypted note on background thread. Returns a Future."""
        self.validate_name(name)
        with self._lock:
            seq = self._save_seq.get(name, 0) + 1
            self._save_seq[name] = seq
        return self._io_executor.submit(
            self._sync_save_encrypted, name, ciphertext, seq
        )

    def delete_note_async(self, name: str) -> concurrent.futures.Future:
        """Delete note on background thread. Returns a Future."""
        self.validate_name(name)
        return self._io_executor.submit(self._sync_delete_note, name)

    def _sync_save_encrypted(
        self, name: str, ciphertext: bytes, save_seq: int | None = None
    ) -> None:
        """Atomic write of ciphertext bytes to *name*.md.enc.

        When *save_seq* is provided (async path), the write is skipped if
        a newer save was submitted after this one.
        """
        if save_seq is not None:
            with self._lock:
                current = self._save_seq.get(name, 0)
                if save_seq < current:
                    return

        import tempfile

        enc_path = self.notes_dir / f"{name}.md.enc"
        enc_path.parent.mkdir(parents=True, exist_ok=True)
        safe_prefix = f".{name.replace('/', '_')}-"
        fd, tmp_name = tempfile.mkstemp(
            dir=enc_path.parent, prefix=safe_prefix, suffix=".tmp.enc"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(ciphertext)
            os.replace(tmp_name, enc_path)
        except OSError:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

        mtime = enc_path.stat().st_mtime
        with self._lock:
            self._content_cache[name] = {
                "content": ciphertext,
                "mtime": mtime,
                "encrypted": True,
            }
            self._mtime_cache[name] = mtime
            self._metadata_cache.pop(name, None)
        self._checkbox_index.pop(name, None)
        if name in self._link_index:
            for linked in self._link_index.pop(name, set()):
                if linked in self._backlink_index:
                    self._backlink_index[linked].discard(name)
                    if not self._backlink_index[linked]:
                        self._backlink_index.pop(linked, None)
        self._backlink_index.pop(name, None)
        self._backlink_cache.clear()

    def _extract_image_paths(self, content: str) -> set[str]:
        """Extract .images/ and .documents/ paths from markdown image syntax."""
        img_re = re.compile(r"!\[([^\]]*)\]\((" + MD_URL_BALANCED + r")\)")
        paths: set[str] = set()
        for m in img_re.finditer(content):
            path = m.group(2)
            clean = path[2:] if path.startswith("./") else path
            if clean.startswith((".images/", ".documents/")):
                paths.add(clean)
        return paths

    @staticmethod
    def _extract_remote_pdf_hashes(content: str) -> set[str]:
        """Extract MD5 hashes of remote PDF URLs referenced in markdown."""
        pdf_re = re.compile(
            r"!\[([^\]]*)\]\((https?://[^\s)]+\.pdf(?:\#[^\s)]*)?)\)",
            re.IGNORECASE,
        )
        hashes: set[str] = set()
        for m in pdf_re.finditer(content):
            url = m.group(2).split("#")[0].split("?")[0]
            h = hashlib.md5(url.encode()).hexdigest()
            hashes.add(h)
        return hashes

    def _is_image_used_by_other_notes(
        self, image_rel_path: str, exclude_note: str
    ) -> bool:
        """Check if *image_rel_path* is referenced by other notes."""
        for other in self.get_notes():
            if other == exclude_note:
                continue
            if self.is_encrypted(other):
                # Can't check encrypted notes; keep the image to be safe
                return True
            try:
                content = self.read_plain(other)
                if f"({image_rel_path})" in content:
                    return True
            except Exception:
                return True
        return False

    def delete_note(self, name: str) -> None:
        self._sync_delete_note(name)
        self._backlink_cache.clear()

    def _sync_delete_note(self, name: str) -> None:
        note_path = self.notes_dir / f"{name}.md"
        enc_path = self.notes_dir / f"{name}.md.enc"

        # Read content before deletion to find referenced images
        content: str | None = None
        try:
            if note_path.exists():
                content = note_path.read_text(encoding="utf-8")
        except OSError:
            content = None

        if enc_path.exists():
            from core.encryption import best_effort_overwrite

            best_effort_overwrite(enc_path)
        if note_path.exists():
            note_path.unlink()

        # Clean up orphaned images if we could read the content
        if content is not None:
            image_paths = self._extract_image_paths(content)
            for img_rel in image_paths:
                img_abs = (self.notes_dir / img_rel).resolve()
                if img_abs.exists() and not self._is_image_used_by_other_notes(
                    img_rel, name
                ):
                    try:
                        img_abs.unlink()
                    except OSError:
                        pass

        # Clean up empty parent directories up to notes_dir
        parent = note_path.parent
        while parent != self.notes_dir:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        with self._lock:
            self._content_cache.pop(name, None)
            self._metadata_cache.pop(name, None)
            self._mtime_cache.pop(name, None)
        self._checkbox_index.pop(name, None)
        if name in self._link_index:
            for linked in self._link_index.pop(name, set()):
                if linked in self._backlink_index:
                    self._backlink_index[linked].discard(name)
                    if not self._backlink_index[linked]:
                        self._backlink_index.pop(linked, None)
        self._backlink_index.pop(name, None)

    def rename_note(self, old_name: str, new_name: str) -> bool:
        """Synchronous rename. Returns True on success."""
        self.validate_name(old_name)
        self.validate_name(new_name)
        old_path = self.notes_dir / f"{old_name}.md"
        new_path = self.notes_dir / f"{new_name}.md"
        old_enc = self.notes_dir / f"{old_name}.md.enc"
        new_enc = self.notes_dir / f"{new_name}.md.enc"

        if old_enc.exists():
            if new_enc.exists():
                return False
            new_enc.parent.mkdir(parents=True, exist_ok=True)
            old_enc.rename(new_enc)
        elif old_path.exists():
            if new_path.exists():
                return False
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)
        else:
            return False

        # Clean up empty old parent dirs
        old_parent = old_path.parent
        while old_parent != self.notes_dir:
            try:
                old_parent.rmdir()
            except OSError:
                break
            old_parent = old_parent.parent

        with self._lock:
            self._content_cache.pop(old_name, None)
            self._metadata_cache.pop(old_name, None)
            self._mtime_cache.pop(old_name, None)
        if old_name in self._checkbox_index:
            self._checkbox_index[new_name] = self._checkbox_index.pop(old_name)
        if old_name in self._link_index:
            links = self._link_index.pop(old_name)
            self._link_index[new_name] = links
            for linked in links:
                if linked in self._backlink_index:
                    self._backlink_index[linked].discard(old_name)
                    if linked != new_name:
                        self._backlink_index[linked].add(new_name)
                    if not self._backlink_index[linked]:
                        self._backlink_index.pop(linked, None)
        incoming: set[str] = set()
        if old_name in self._backlink_index:
            incoming = self._backlink_index.pop(old_name)
            self._backlink_index[new_name] = {
                new_name if source == old_name else source for source in incoming
            }
        self._backlink_cache.clear()

        # Update [[wiki links]] in backlinking notes to point to the new name.
        if incoming:
            pat = re.compile(r"\[\[" + re.escape(old_name) + r"\]\]", re.IGNORECASE)
            repl = f"[[{new_name}]]"
            for source in incoming:
                try:
                    name = new_name if source == old_name else source
                    if self.is_encrypted(name):
                        continue
                    content = self.read_plain(name)
                    updated = pat.sub(repl, content)
                    if updated != content:
                        self.save_note(name, updated)
                except Exception:
                    logger.exception("Failed to update links in '%s'", source)

        return True

    # Checkbox / deadline helpers

    def _extract_checkboxes(self, note_name: str, content: str) -> list[dict[str, Any]]:
        boxes: list[dict[str, Any]] = []
        for line_num, line in enumerate(content.split("\n"), 1):
            m = CB_EXTRACT_RE.match(line)
            if m:
                raw_indent = m.group(1)
                indent_level = len(raw_indent.replace("\t", "  ")) // 2
                boxes.append(
                    {
                        "note": note_name,
                        "text": m.group(3).strip(),
                        "checked": m.group(2).lower() == "x",
                        "line": line_num,
                        "deadline": m.group(4),
                        "indent": indent_level,
                    }
                )
        return boxes

    def _resolve_in_content(
        self,
        content: str,
        old_line_num: int,
        text: str,
    ) -> int:
        """Like :meth:`resolve_checkbox_line` but re-uses already-read *content*.

        Avoids a second cache/disk read so there is no window for a background
        save to invalidate the cache between resolution and modification.
        """
        boxes = self._extract_checkboxes("", content)

        for b in boxes:
            if b["text"] == text and b["line"] == old_line_num:
                return old_line_num

        candidates = [b for b in boxes if b["text"] == text]
        if len(candidates) == 1:
            return candidates[0]["line"]
        if len(candidates) > 1:
            return min(candidates, key=lambda b: abs(b["line"] - old_line_num))["line"]

        return old_line_num

    def resolve_checkbox_line(
        self, note_name: str, old_line_num: int, text: str
    ) -> int:
        """Re-read the note and find the current line for the checkbox matching *text*.

        When a note is edited in the editor, lines above a checkbox can be inserted
        or deleted, shifting the checkbox's line number.  This method re-parses the
        note and matches by text so that the correct line is used for subsequent
        read/write operations.

        Priority:
          1. Exact match on (text, old_line_num) – no shift occurred.
          2. Single checkbox with matching text.
          3. Closest match among duplicates.
          4. Fallback to *old_line_num* if no text match is found.
        """
        content = self.read_plain(note_name)
        return self._resolve_in_content(content, old_line_num, text)

    def update_checkbox(
        self,
        note_name: str,
        line_num: int,
        checked: bool,
        content: str | None = None,
    ) -> bool:
        """Toggle checkbox at *line_num* in *note_name* to *checked*.

        When *content* is provided it is used directly, avoiding a
        second cache/disk read.  The caller is responsible for ensuring
        *content* reflects the current state of the note on disk.
        """
        if content is None:
            content = self.read_plain(note_name)
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
        self,
        note_name: str,
        line_num: int,
        new_deadline: str | None,
        content: str | None = None,
    ) -> bool:
        """Set the @deadline on the checkbox at *line_num*; pass None to remove it.

        When *content* is provided it is used directly, avoiding a
        second cache/disk read.
        """
        if content is None:
            content = self.read_plain(note_name)
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

        Uses the precomputed link index. Skips encrypted notes
        since their content cannot be searched without the session key.
        The index is rebuilt lazily if empty.
        """
        if not self._link_index and not self._backlink_index:
            self._rebuild_link_index()

        if note_name not in self._backlink_index:
            return []
        backlinks = [
            n
            for n in self._backlink_index[note_name]
            if n != note_name and n not in exclude_archived
        ]
        return backlinks

    def _rebuild_link_index(self) -> None:
        self._link_index.clear()
        self._backlink_index.clear()
        from core.utils import WIKI_CLICK_RE

        for name in self.get_notes():
            if self.is_encrypted(name):
                continue
            content = self.read_plain(name)
            links = set(WIKI_CLICK_RE.findall(content))
            self._link_index[name] = links
            for linked in links:
                if linked == name:
                    continue
                self._backlink_index.setdefault(linked, set()).add(name)
