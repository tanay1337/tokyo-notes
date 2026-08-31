"""Index local image and PDF attachments used by notes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlparse

from core.utils import MD_URL_BALANCED

_EMBED_RE = re.compile(r"!\[[^\]]*\]\((" + MD_URL_BALANCED + r")\)")
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\((" + MD_URL_BALANCED + r")\)")
_DIAGRAM_RE = re.compile(r"!\[diagram\]\(([^)]+)\)", re.IGNORECASE)
_IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
)


@dataclass(frozen=True)
class MediaAsset:
    path: Path
    kind: str
    filename: str
    modified: float
    note_names: tuple[str, ...] = ()
    source_refs: tuple[tuple[str, str], ...] = ()
    title: str = ""

    @property
    def referenced(self) -> bool:
        return bool(self.note_names)


def find_media_reference_line(content: str, reference: str) -> int | None:
    """Return the one-based line containing the first media reference."""
    position = content.find(reference)
    if position < 0:
        return None
    return content.count("\n", 0, position) + 1


def _clean_reference(value: str) -> str:
    return value.strip().split("#", 1)[0].split("?", 1)[0].strip("<>")


def _remote_pdf_cache(notes_dir: Path, url: str) -> Path:
    digest = hashlib.md5(url.encode()).hexdigest()
    return notes_dir / ".documents" / f"remote_{digest}.pdf"


def _resolve_reference(notes_dir: Path, reference: str) -> Path | None:
    clean = _clean_reference(reference)
    if not clean or clean.startswith(("http://", "https://")):
        return None
    path = Path(clean).expanduser()
    if not path.is_absolute():
        path = notes_dir / path
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(notes_dir.resolve()):
            return None
        return resolved
    except OSError:
        return None


def _reference_path(notes_dir: Path, reference: str) -> Path | None:
    raw = reference.strip().strip("<>").split("#", 1)[0]
    if raw.startswith(("http://", "https://")):
        if urlparse(raw).path.lower().endswith(".pdf"):
            return _remote_pdf_cache(notes_dir, raw)
        return None
    return _resolve_reference(notes_dir, raw)


def _diagram_reference_path(notes_dir: Path, reference: str) -> Path | None:
    """Resolve the short diagram IDs accepted by the editor embed renderer."""
    clean = _clean_reference(reference)
    if not clean or clean.startswith(("http://", "https://")):
        return None
    if "/" in clean or Path(clean).suffix:
        return None
    path = (notes_dir / ".diagrams" / f"{clean}.json").resolve()
    return path if path.is_file() else None


def _asset_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    return None


def build_media_index(
    notes_dir: str | Path,
    note_names: Iterable[str],
    read_note: Callable[[str], str],
    *,
    readable_notes: set[str] | None = None,
) -> list[MediaAsset]:
    """Scan attachment directories and associate readable Markdown references."""
    root = Path(notes_dir).resolve()
    note_names = list(note_names)
    readable = set(note_names) if readable_notes is None else readable_notes
    references: dict[Path, set[tuple[str, str]]] = {}

    for note_name in note_names:
        if note_name not in readable:
            continue
        try:
            content = read_note(note_name) or ""
        except Exception:
            continue
        matches = list(_EMBED_RE.finditer(content)) + list(_LINK_RE.finditer(content))
        for match in matches:
            path = _diagram_reference_path(root, match.group(1))
            if path is None:
                path = _reference_path(root, match.group(1))
            if path is not None:
                references.setdefault(path, set()).add((note_name, match.group(1)))
        for match in _DIAGRAM_RE.finditer(content):
            diagram_id = match.group(1).strip()
            path = (root / ".diagrams" / f"{diagram_id}.json").resolve()
            references.setdefault(path, set()).add((note_name, diagram_id))

    assets: list[MediaAsset] = []
    seen_paths: set[Path] = set()
    for directory in (root / ".images", root / ".documents", root / ".diagrams"):
        if not directory.is_dir():
            continue
        try:
            files = (p for p in directory.rglob("*") if p.is_file())
            for path in files:
                kind = _asset_kind(path)
                if directory.name == ".diagrams":
                    if path.suffix.lower() != ".json":
                        continue
                    kind = "diagram"
                if kind is None:
                    continue
                try:
                    resolved = path.resolve()
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                title = ""
                if kind == "diagram":
                    try:
                        title = str(
                            json.loads(path.read_text(encoding="utf-8")).get(
                                "title", ""
                            )
                        )
                    except (OSError, json.JSONDecodeError, AttributeError):
                        continue
                assets.append(
                    MediaAsset(
                        path=resolved,
                        kind=kind,
                        filename=path.name,
                        modified=modified,
                        note_names=tuple(
                            sorted(
                                note for note, _ref in references.get(resolved, set())
                            )
                        ),
                        source_refs=tuple(sorted(references.get(resolved, set()))),
                        title=title,
                    )
                )
        except OSError:
            continue

    assets.sort(key=lambda asset: (-asset.modified, asset.filename.lower()))
    return assets
