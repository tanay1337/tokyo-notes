"""Tests for core/services.py — pure business-logic functions."""

from __future__ import annotations

import datetime
from concurrent.futures import Future
from unittest.mock import MagicMock

import core.services as services
from core.services import (
    build_stats,
    clean_title,
    derive_display_title,
    encrypt_note_on_disk,
    get_week_boundaries,
    patch_sidebar_row,
    save_note_content,
)


class TestCleanTitle:
    def test_normal_title(self) -> None:
        assert clean_title("Hello World") == "Hello World"

    def test_strips_special_chars(self) -> None:
        assert clean_title("foo/bar:baz") == "foobarbaz"

    def test_allows_filename_chars(self) -> None:
        assert clean_title("My Note (2024) - draft.md") == "My Note (2024) - draft.md"

    def test_strips_whitespace(self) -> None:
        assert clean_title("  hello  ") == "hello"

    def test_empty_string(self) -> None:
        assert clean_title("") == ""


class TestDeriveDisplayTitle:
    def test_extracts_h1(self) -> None:
        content = "# My Note\n\nSome body text."
        assert derive_display_title(content, "Untitled") == "My Note"

    def test_fallback_when_no_h1(self) -> None:
        assert derive_display_title("body text", "Untitled") == "Untitled"

    def test_fallback_on_empty_content(self) -> None:
        assert derive_display_title("", "Untitled") == "Untitled"

    def test_cleans_h1_title(self) -> None:
        content = "# Hello: World\n"
        assert derive_display_title(content, "x") == "Hello World"


class TestPatchSidebarRow:
    def test_does_not_invalidate_unchanged_labels(self) -> None:
        row = MagicMock()
        row.title_label.get_label.return_value = "Sample Note"
        row.snippet_label.get_label.return_value = "Example body"

        patch_sidebar_row(
            row,
            title="Sample Note",
            snippet="Example body",
        )

        row.title_label.set_label.assert_not_called()
        row.snippet_label.set_label.assert_not_called()


class TestBuildStats:
    def test_empty(self) -> None:
        result = build_stats("")
        assert "words" in result

    def test_single_word(self) -> None:
        assert "1 word" in build_stats("hello")

    def test_read_time_minimum(self) -> None:
        result = build_stats("hello world")
        assert "1 min" in result

    def test_longer_content(self) -> None:
        words = " ".join("word" for _ in range(500))
        result = build_stats(words)
        assert "2 min" in result


class TestGetWeekBoundaries:
    def test_returns_two_dates(self) -> None:
        start, end = get_week_boundaries()
        assert isinstance(start, str)
        assert isinstance(end, str)
        assert start <= end

    def test_sunday_start(self) -> None:
        start, end = get_week_boundaries(start_on_sunday=True)
        assert isinstance(start, str)
        assert isinstance(end, str)

    def test_week_boundaries_monday_start_deterministic(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(services.datetime, "date", FixedDate)

        assert get_week_boundaries(start_on_sunday=False) == (
            "2026-05-25",
            "2026-05-31",
        )

    def test_week_boundaries_sunday_start_deterministic(self, monkeypatch) -> None:
        class FixedDate(datetime.date):
            @classmethod
            def today(cls) -> datetime.date:
                return cls(2026, 5, 27)

        monkeypatch.setattr(services.datetime, "date", FixedDate)

        assert get_week_boundaries(start_on_sunday=True) == (
            "2026-05-24",
            "2026-05-30",
        )


class TestSaveNoteContent:
    def test_async_plain_save_failure_does_not_call_on_done(self) -> None:
        future: Future = Future()
        future.set_exception(OSError("disk full"))
        notes_manager = MagicMock()
        notes_manager.save_note_async.return_value = future
        on_done = MagicMock()

        save_note_content(
            note_name="Note",
            content="body",
            is_encrypted=False,
            derive_encryption_key=MagicMock(),
            notes_manager=notes_manager,
            session_password_bytes=None,
            on_done=on_done,
        )

        on_done.assert_not_called()

    def test_async_encrypted_save_failure_does_not_call_on_done(self) -> None:
        future: Future = Future()
        future.set_exception(OSError("disk full"))
        notes_manager = MagicMock()
        notes_manager.save_encrypted_async.return_value = future
        on_done = MagicMock()

        save_note_content(
            note_name="Secret",
            content="body",
            is_encrypted=True,
            derive_encryption_key=lambda _name: (b"0" * 32, b"1" * 16, b"old"),
            notes_manager=notes_manager,
            session_password_bytes=bytearray(b"password"),
            on_done=on_done,
        )

        on_done.assert_not_called()

    def test_encrypted_save_preserves_existing_salt(self) -> None:
        saved = {}
        notes_manager = MagicMock()
        notes_manager.save_encrypted.side_effect = lambda name, ct: saved.update(
            {name: ct}
        )

        save_note_content(
            note_name="Secret",
            content="new body",
            is_encrypted=True,
            derive_encryption_key=lambda _name: (b"0" * 32, b"1" * 16, b"old"),
            notes_manager=notes_manager,
            session_password_bytes=bytearray(b"password"),
        )

        assert saved["Secret"][:16] == b"1" * 16


class TestEncryptNoteOnDisk:
    def test_encrypt_note_removes_plaintext_and_marks_config(self, tmp_path) -> None:
        from core.encryption import decrypt
        from core.storage import NotesManager

        nm = NotesManager(tmp_path)
        cfg = MagicMock()
        nm.save_note("Secret", "plain text")

        content, key_bytes = encrypt_note_on_disk(
            note_name="Secret",
            password="correct horse battery staple",
            notes_manager=nm,
            cfg=cfg,
        )

        assert content == "plain text"
        assert not (tmp_path / "Secret.md").exists()
        assert (tmp_path / "Secret.md.enc").exists()
        cfg.mark_encrypted.assert_called_once_with("Secret")
        assert decrypt(nm.read_encrypted_raw("Secret"), key_bytes) == "plain text"
