"""Encryption primitives for private notes.

AES-256-GCM authenticated encryption with Argon2id key derivation.
All functions are pure Python with no GTK dependencies.

File format for .enc files:
    [ 16 bytes salt ][ 12 bytes nonce ][ GCM ciphertext + 16-byte tag ]
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

_SALT_LEN = 16
_NONCE_LEN = 12
_ARGON2_TIME_COST = 10
_ARGON2_MEMORY_COST = 65536
_ARGON2_LANES = 4
_KEY_LEN = 32


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from *password* using Argon2id with the given salt."""
    kdf = Argon2id(
        iterations=_ARGON2_TIME_COST,
        length=_KEY_LEN,
        memory_cost=_ARGON2_MEMORY_COST,
        lanes=_ARGON2_LANES,
        salt=salt,
    )
    return kdf.derive(password.encode("utf-8"))


def derive_key_from_file(password: str, ciphertext_bytes: bytes) -> bytes:
    """Derive a key using the per-file salt embedded in the .enc file header."""
    file_salt = ciphertext_bytes[:_SALT_LEN]
    return derive_key(password, file_salt)


def encrypt(plaintext: str, key: bytes, salt: bytes | None = None) -> bytes:
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


def decrypt(ciphertext: bytes, key: bytes) -> str:
    """Decrypt a .enc file payload.

    Expects: salt (16B) + nonce (12B) + ciphertext+tag
    The key must have been derived using the same salt embedded in the file.
    """
    nonce = ciphertext[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    raw = ciphertext[_SALT_LEN + _NONCE_LEN :]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, raw, None)
    return plaintext.decode("utf-8")


def secure_delete(path: Path) -> None:
    """Overwrite file with zeros then unlink. Best-effort."""
    try:
        size = path.stat().st_size
        with open(path, "wb") as f:
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
        path.unlink()
    except OSError:
        path.unlink(missing_ok=True)
