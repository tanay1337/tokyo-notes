"""Pure business-logic functions used by NoteLifecycleManager.

These helpers do **not** know about the app object.  They operate on
explicitly-given storage / buffer / UI references so they can be tested
independently.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.utils import H1_TITLE_RE, get_snippet as _get_snippet


def clean_title(raw: str) -> str:
    return "".join(
        c for c in raw.strip() if c.isalnum() or c in (" ", "-", "_", ".", "(", ")")
    ).strip()


def derive_display_title(content: str, fallback: str) -> str:
    m = H1_TITLE_RE.search(content)
    return (clean_title(m.group(1)) if m else None) or fallback


# ── save / rename ──

def update_note_title(
    *,
    old_name: str,
    content: str,
    notes_manager,
) -> tuple[str, bool]:
    """Maybe rename a note based on H1; returns (new_name, did_rename)."""
    new_title = derive_display_title(content, "")
    if not new_title or new_title == old_name:
        return old_name, False

    base = new_title
    counter = 1
    while True:
        collision = (Path(notes_manager.notes_dir) / f"{new_title}.md")
        collision_enc = (Path(notes_manager.notes_dir) / f"{new_title}.md.enc")
        if not collision.exists() and not collision_enc.exists():
            break
        new_title = f"{base} {counter}"
        counter += 1

    if new_title == old_name:
        return old_name, False

    if not notes_manager.rename_note(old_name, new_title):
        return old_name, False

    return new_title, True


# ── sidebar patching ──

def patch_sidebar_row(
    row: Any, *,
    title: str,
    snippet: str,
) -> None:
    """In-place update of a sidebar row’s labels."""
    tl = getattr(row, "title_label", None)
    sl = getattr(row, "snippet_label", None)
    if tl is not None:
        tl.set_label(title)
    if sl is not None:
        sl.set_label(snippet)


def build_stats(content: str) -> str:
    word_count = len(content.split())
    read_time = max(1, word_count // 200)
    return f"{word_count:,} words · {read_time} min read"
