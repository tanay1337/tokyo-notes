"""Tests for core/encryption.py — Argon2id + AES-GCM round-trip.

Requires the ``cryptography`` package.  Skipped automatically in CI.
"""

from __future__ import annotations

import pytest

cryptography = pytest.importorskip(
    "cryptography", reason="requires pip install cryptography"
)

from core.encryption import (
    best_effort_overwrite,
    decrypt,
    derive_key,
    encrypt,
    zero_bytearray,
)


class TestKeyDerivation:
    def test_derive_key_returns_bytes(self):
        salt = b"\x00" * 16
        key = derive_key("password", salt)
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_derive_key_deterministic_with_same_salt(self):
        salt = b"\x01" * 16
        k1 = derive_key("hello", salt)
        k2 = derive_key("hello", salt)
        assert k1 == k2

    def test_derive_key_different_with_different_salt(self):
        k1 = derive_key("hello", b"\x01" * 16)
        k2 = derive_key("hello", b"\x02" * 16)
        assert k1 != k2


class TestEncryptDecrypt:
    def test_round_trip(self):
        salt = b"\xaa" * 16
        key = derive_key("secret", salt)
        plaintext = "Hello, encrypted world!"
        cipher = encrypt(plaintext, key, salt=salt)
        decrypted = decrypt(cipher, key)
        assert decrypted == plaintext

    def test_round_trip_with_random_salt(self):
        key = derive_key("p4ssword", b"\xbb" * 16)
        plaintext = "Another test"
        cipher = encrypt(plaintext, key)  # no salt → random
        assert decrypt(cipher, key) == plaintext

    def test_different_key_fails(self):
        key_a = derive_key("password_a", b"\xcc" * 16)
        key_b = derive_key("password_b", b"\xcc" * 16)
        cipher = encrypt("secret data", key_a, salt=b"\xcc" * 16)
        with pytest.raises(Exception):
            decrypt(cipher, key_b)

    def test_encrypted_format(self):
        key = derive_key("key", b"\xdd" * 16)
        cipher = encrypt("data", key, salt=b"\xdd" * 16)
        # Format: salt(16) + nonce(12) + ciphertext+tag
        assert len(cipher) >= 16 + 12
        # First 16 bytes are the salt we provided
        assert cipher[:16] == b"\xdd" * 16

    def test_empty_plaintext(self):
        key = derive_key("k", b"\xee" * 16)
        cipher = encrypt("", key, salt=b"\xee" * 16)
        assert decrypt(cipher, key) == ""

    def test_unicode(self):
        key = derive_key("k", b"\xff" * 16)
        text = "日本語 & émoji 🎉"
        cipher = encrypt(text, key, salt=b"\xff" * 16)
        assert decrypt(cipher, key) == text


class TestBestEffortOverwrite:
    def test_overwrite_then_unlink(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("sensitive data")
        assert f.exists()
        best_effort_overwrite(f)
        assert not f.exists()

    def test_overwrite_nonexistent(self, tmp_path):
        f = tmp_path / "ghost.txt"
        best_effort_overwrite(f)  # should not raise


class TestZeroBytearray:
    def test_zeroes_bytearray_in_place(self):
        secret = bytearray(b"super-secret")

        zero_bytearray(secret)

        assert secret == bytearray(len(secret))

    def test_none_is_noop(self):
        zero_bytearray(None)
