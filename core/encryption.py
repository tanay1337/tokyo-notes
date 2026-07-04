"""Encryption primitives for private notes.

AES-256-GCM authenticated encryption with Argon2id key derivation.

File format for .enc files:
    [ 16 bytes salt ][ 12 bytes nonce ][ GCM ciphertext + 16-byte tag ]
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from gi.repository import GLib

_SALT_LEN = 16
_NONCE_LEN = 12
_ARGON2_TIME_COST = 10
_ARGON2_MEMORY_COST = 65536
_ARGON2_LANES = 4
_KEY_LEN = 32

# Bounded thread pool for async key derivation. Each Argon2 job uses 64 MiB,
# so keep concurrency low to avoid memory spikes during pre-derivation.
_POOL: concurrent.futures.ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def get_pool() -> concurrent.futures.ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, min(2, os.cpu_count() or 2)),
                )
    return _POOL


_get_pool = get_pool  # backwards compat for derive_key_async


def derive_key(password: str | bytearray | bytes, salt: bytes) -> bytes:
    """Derive a 32-byte key from *password* using Argon2id with the given salt."""
    kdf = Argon2id(
        iterations=_ARGON2_TIME_COST,
        length=_KEY_LEN,
        memory_cost=_ARGON2_MEMORY_COST,
        lanes=_ARGON2_LANES,
        salt=salt,
    )
    if isinstance(password, str):
        return kdf.derive(password.encode("utf-8"))
    return kdf.derive(bytes(password))


def derive_key_async(
    password: str | bytearray | bytes, salt: bytes, on_done: Callable[[bytes], None]
) -> None:
    """Derive a key on a background thread; calls *on_done* on the GTK main thread.

    Because Argon2id is CPU-bound (1--3 s), this avoids blocking the main thread.
    The derived key is passed to *on_done* via GLib.idle_add, so *on_done* must NOT
    wrap itself in GLib.idle_add anymore.
    """

    def _work() -> None:
        key = derive_key(password, salt)
        GLib.idle_add(on_done, key)

    _get_pool().submit(_work)


def derive_key_from_file(password: str, ciphertext_bytes: bytes) -> bytes:
    """Derive a key using the per-file salt embedded in the .enc file header."""
    file_salt = ciphertext_bytes[:_SALT_LEN]
    return derive_key(password, file_salt)


def encrypt(plaintext: str, key: bytes | bytearray, salt: bytes | None = None) -> bytes:
    """Encrypt *plaintext* with AES-256-GCM.

    Returns: salt (16B) + nonce (12B) + ciphertext+tag
    If *salt* is provided, it is used; otherwise a random salt is generated.
    """
    if salt is None:
        salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return salt + nonce + ciphertext


def decrypt(ciphertext: bytes, key: bytes | bytearray) -> str:
    """Decrypt a .enc file payload.

    Expects: salt (16B) + nonce (12B) + ciphertext+tag
    The key must have been derived using the same salt embedded in the file.
    """
    nonce = ciphertext[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    raw = ciphertext[_SALT_LEN + _NONCE_LEN :]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, raw, None)
    return plaintext.decode("utf-8")


def best_effort_overwrite(path: Path) -> None:
    """Overwrite file with zeros then unlink.

    This provides NO guarantee on modern storage (APFS / SSDs with
    wear-leveling, copy-on-write filesystems, or shingled drives).
    On those media the original data may persist on retired flash blocks
    or CoW shadow pages.  This is a privacy theatre mitigation, not a
    cryptographically assured erasure.
    """
    try:
        size = path.stat().st_size
        chunk = b"\x00" * min(size, 65536)
        with open(path, "wb") as f:
            for _ in range(size // len(chunk)):
                f.write(chunk)
            f.write(b"\x00" * (size % len(chunk)))
            f.flush()
            os.fsync(f.fileno())
        path.unlink()
    except OSError:
        path.unlink(missing_ok=True)


def zero_bytearray(value: bytearray | None) -> None:
    """Overwrite a bytearray in place if one was provided."""
    if value is None:
        return
    for i in range(len(value)):
        value[i] = 0


def shutdown_pool() -> None:
    """Shut down the Argon2id thread pool, waiting for any in-flight jobs."""
    global _POOL
    if _POOL is not None:
        _POOL.shutdown(wait=False)
        _POOL = None


# Backwards-compatible alias
secure_delete = best_effort_overwrite
