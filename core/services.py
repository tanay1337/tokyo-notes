"""Pure business-logic functions used by NoteLifecycleManager.

These helpers do **not** know about the app object.  They operate on
explicitly-given storage / buffer / UI references so they can be tested
independently.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Callable

from core.utils import H1_TITLE_RE


def clean_title(raw: str) -> str:
    """Strip non-filename characters from a title string."""
    return "".join(
        c for c in raw.strip() if c.isalnum() or c in (" ", "-", "_", ".", "(", ")")
    ).strip()


def derive_display_title(content: str, fallback: str) -> str:
    """Extract the first H1 heading from *content*, returning *fallback* if absent."""
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
        collision = Path(notes_manager.notes_dir) / f"{new_title}.md"
        collision_enc = Path(notes_manager.notes_dir) / f"{new_title}.md.enc"
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
    row: Any,
    *,
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


def encrypt_note_on_disk(
    *,
    note_name: str,
    password: str | bytearray | bytes,
    notes_manager,
    cfg,
) -> tuple[str, bytearray]:
    """Encrypt a plain note on disk: derive key, encrypt content, save, overwrite plain.

    Returns (plaintext_content, key_bytes) so callers can cache or display the content.
    The caller must zero key_bytes when done.
    """
    import os

    from core.encryption import _SALT_LEN, best_effort_overwrite, derive_key, encrypt

    pw = bytearray(password.encode("utf-8")) if isinstance(password, str) else password
    content = notes_manager.read_plain(note_name)
    salt = os.urandom(_SALT_LEN)
    key = derive_key(pw, salt)
    key_bytes = bytearray(key)
    ciphertext = encrypt(content, key_bytes, salt)
    notes_manager.save_encrypted(note_name, ciphertext)
    cfg.mark_encrypted(note_name)
    plain_path = notes_manager.notes_dir / f"{note_name}.md"
    if plain_path.exists():
        best_effort_overwrite(plain_path)
    notes_manager._content_cache.pop(note_name, None)
    notes_manager._metadata_cache.pop(note_name, None)
    return content, key_bytes


def save_note_content(
    *,
    note_name: str,
    content: str,
    is_encrypted: bool,
    derive_encryption_key,
    notes_manager,
    session_password_bytes: bytearray | None,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Save *content* to disk, encrypting if *is_encrypted*.

    *derive_encryption_key* is a callable
    ``(note_name) -> (key_bytes, salt, ciphertext)``
    provided by the app's session manager.

    If *on_done* is given, the disk write runs on a background thread
    and *on_done* is called on the main thread via GLib.idle_add.
    """
    import logging

    logger = logging.getLogger(__name__)

    if is_encrypted:
        if session_password_bytes is not None:
            try:
                from gi.repository import GLib  # type: ignore[import-not-found]

                from core.encryption import encrypt

                key_bytes, file_salt, _ = derive_encryption_key(note_name)
                ciphertext = encrypt(content, key_bytes, file_salt)
                if on_done:
                    fut = notes_manager.save_encrypted_async(note_name, ciphertext)
                    fut.add_done_callback(
                        lambda f: (
                            logger.error(
                                "Failed to save encrypted note '%s': %s",
                                note_name,
                                f.exception(),
                            )
                            if f.exception()
                            else GLib.idle_add(on_done)
                        )
                    )
                else:
                    try:
                        notes_manager.save_encrypted(note_name, ciphertext)
                    except OSError:
                        raise
            except Exception as e:
                if isinstance(e, OSError):
                    raise
                logger.error("Failed to encrypt note '%s' on save: %s", note_name, e)
        else:
            logger.warning(
                "Skipping save of encrypted note '%s' — session locked", note_name
            )
    else:
        if on_done:
            from gi.repository import GLib  # type: ignore[import-not-found]

            fut = notes_manager.save_note_async(note_name, content)
            fut.add_done_callback(
                lambda f: (
                    logger.error(
                        "Failed to save note '%s': %s",
                        note_name,
                        f.exception(),
                    )
                    if f.exception()
                    else GLib.idle_add(on_done)
                )
            )
        else:
            try:
                notes_manager.save_note(note_name, content)
            except OSError:
                raise


def build_stats(content: str) -> str:
    """Return a human-readable word-count / read-time string for *content*."""
    word_count = len(content.split())
    read_time = max(1, word_count // 200)
    return f"{word_count:,} words · {read_time} min read"


def get_week_boundaries(start_on_sunday: bool = False) -> tuple[str, str]:
    """Return (week_start, week_end) ISO date strings for the current week."""
    today = datetime.date.today()
    if start_on_sunday:
        offset = (today.weekday() + 1) % 7
    else:
        offset = today.weekday()
    week_start = (today - datetime.timedelta(days=offset)).isoformat()
    week_end = (today + datetime.timedelta(days=6 - offset)).isoformat()
    return week_start, week_end
