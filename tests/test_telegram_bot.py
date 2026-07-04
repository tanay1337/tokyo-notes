"""Tests for core/telegram_bot.py — Telegram bot polling and message handling."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from core.telegram_bot import TelegramBot


@pytest.fixture
def mock_nm():
    """Return a mock NotesManager."""
    nm = MagicMock()
    nm.read_plain.return_value = ""
    return nm


@pytest.fixture
def notes_dir(tmp_path):
    return tmp_path


@pytest.fixture
def bot(mock_nm, notes_dir):
    return TelegramBot(
        token="test:token",
        notes_manager=mock_nm,
        notes_dir=notes_dir,
        owner_id=12345,
        on_inbox_updated=MagicMock(),
    )


@pytest.fixture
def bot_no_owner(mock_nm, notes_dir):
    return TelegramBot(
        token="test:token",
        notes_manager=mock_nm,
        notes_dir=notes_dir,
        on_inbox_updated=MagicMock(),
    )


# ── _api_call ──


class TestApiCall:
    def test_success(self, bot):
        resp_data = {"ok": True, "result": {"username": "test_bot"}}
        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = (
                json.dumps(resp_data).encode()
            )
            result = bot._api_call("getMe")
        assert result == resp_data
        mock_urlopen.assert_called_once()
        url = mock_urlopen.call_args[0][0].full_url
        assert "getMe" in url
        assert "test:token" in url

    def test_http_error_returns_json(self, bot):
        body = json.dumps(
            {"ok": False, "error_code": 401, "description": "Unauthorized"}
        )
        fake_fp = io.BytesIO(body.encode())
        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="http://example.com",
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=fake_fp,
            )
            result = bot._api_call("getMe")
        assert result == {"ok": False, "error_code": 401, "description": "Unauthorized"}

    def test_network_error_returns_none(self, bot):
        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("Connection refused")
            result = bot._api_call("getMe")
        assert result is None

    def test_passes_params_as_query_string(self, bot):
        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"ok":true,"result":[]}'
            )
            bot._api_call("getUpdates", {"offset": 5, "timeout": 10})
        url = mock_urlopen.call_args[0][0].full_url
        assert "offset=5" in url
        assert "timeout=10" in url


# ── _handle_message ──


class TestHandleMessage:
    def test_text_message(self, bot, mock_nm, notes_dir):
        msg = {
            "message_id": 1,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "text": "Hello, world!",
            "date": 1700000000,
        }
        with patch.object(bot, "_append_to_inbox") as mock_append:
            bot._handle_message(msg)
        mock_append.assert_called_once()
        line = mock_append.call_args[0][0]
        assert line.endswith("Hello, world!")

    def test_photo_without_caption(self, bot, mock_nm, notes_dir):
        msg = {
            "message_id": 2,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "photo": [
                {"file_id": "small_photo", "width": 100, "height": 100},
                {"file_id": "large_photo", "width": 800, "height": 600},
            ],
            "date": 1700000000,
        }
        dest = notes_dir / ".images" / "telegram_large_photo.jpg"
        with patch.object(bot, "_download_file", return_value=True) as mock_dl:
            with patch.object(bot, "_append_to_inbox") as mock_append:
                bot._handle_message(msg)
        mock_dl.assert_called_once_with("large_photo", dest)
        line = mock_append.call_args[0][0]
        assert "![](.images/telegram_large_photo.jpg)" in line

    def test_photo_with_caption(self, bot, notes_dir):
        msg = {
            "message_id": 3,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "photo": [{"file_id": "pid", "width": 100, "height": 100}],
            "caption": "A nice view",
            "date": 1700000000,
        }
        with patch.object(bot, "_download_file", return_value=True):
            with patch.object(bot, "_append_to_inbox") as mock_append:
                bot._handle_message(msg)
        line = mock_append.call_args[0][0]
        assert "A nice view" in line
        assert "![](.images/telegram_pid.jpg)" in line

    def test_photo_download_failure(self, bot, notes_dir):
        msg = {
            "message_id": 4,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "photo": [{"file_id": "fid", "width": 100, "height": 100}],
            "date": 1700000000,
        }
        with patch.object(bot, "_download_file", return_value=False):
            with patch.object(bot, "_append_to_inbox") as mock_append:
                bot._handle_message(msg)
        line = mock_append.call_args[0][0]
        # Without a caption, the fallback text is used
        assert "(photo download failed)" in line

    def test_pdf_document(self, bot, notes_dir):
        msg = {
            "message_id": 5,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "document": {
                "file_id": "pdf_id",
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
                "file_size": 1024,
            },
            "caption": "Annual report",
            "date": 1700000000,
        }
        with patch.object(bot, "_download_file", return_value=True):
            with patch.object(bot, "_append_to_inbox") as mock_append:
                bot._handle_message(msg)
        mock_append.assert_called_once()
        line = mock_append.call_args[0][0]
        assert "Annual report" in line
        assert "![](.documents/telegram_pdf_id.pdf)" in line

    def test_pdf_download_failure(self, bot, notes_dir):
        msg = {
            "message_id": 6,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "document": {
                "file_id": "bad_pdf",
                "file_name": "doc.pdf",
                "mime_type": "application/pdf",
            },
            "caption": "",
            "date": 1700000000,
        }
        with patch.object(bot, "_download_file", return_value=False):
            with patch.object(bot, "_append_to_inbox") as mock_append:
                bot._handle_message(msg)
        line = mock_append.call_args[0][0]
        assert "(PDF download failed)" in line

    def test_non_pdf_document_is_ignored(self, bot):
        msg = {
            "message_id": 7,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "document": {
                "file_id": "zip_id",
                "file_name": "archive.zip",
                "mime_type": "application/zip",
            },
            "date": 1700000000,
        }
        with patch.object(bot, "_append_to_inbox") as mock_append:
            bot._handle_message(msg)
        mock_append.assert_not_called()

    def test_sticker_is_ignored(self, bot):
        msg = {
            "message_id": 8,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "sticker": {"file_id": "sticker_id", "emoji": "😊"},
            "date": 1700000000,
        }
        with patch.object(bot, "_append_to_inbox") as mock_append:
            bot._handle_message(msg)
        mock_append.assert_not_called()

    def test_voice_is_ignored(self, bot):
        msg = {
            "message_id": 9,
            "from": {"id": 12345},
            "chat": {"id": 123},
            "voice": {"file_id": "voice_id", "duration": 5},
            "date": 1700000000,
        }
        with patch.object(bot, "_append_to_inbox") as mock_append:
            bot._handle_message(msg)
        mock_append.assert_not_called()

    def test_rejects_message_when_no_owner_id(self, bot_no_owner, mock_nm, notes_dir):
        msg = {
            "message_id": 10,
            "from": {"id": 99999},
            "chat": {"id": 123},
            "text": "Hello from unknown user!",
            "date": 1700000000,
        }
        with patch.object(bot_no_owner, "_append_to_inbox") as mock_append:
            bot_no_owner._handle_message(msg)
        mock_append.assert_not_called()

    def test_rejects_non_owner_message(self, bot, mock_nm, notes_dir):
        msg = {
            "message_id": 11,
            "from": {"id": 99999},
            "chat": {"id": 123},
            "text": "Hello from non-owner!",
            "date": 1700000000,
        }
        with patch.object(bot, "_append_to_inbox") as mock_append:
            bot._handle_message(msg)
        mock_append.assert_not_called()


# ── _append_to_inbox ──


class TestAppendToInbox:
    def test_appends_to_existing_note(self, bot, mock_nm):
        mock_nm.read_plain.return_value = "- [10:00] First note"
        bot._append_to_inbox("- [11:00] Second note")
        saved = mock_nm.save_note.call_args[0]
        assert saved[0] == "Inbox"
        assert "First note" in saved[1]
        assert "Second note" in saved[1]
        bot._on_inbox_updated.assert_called_once()

    def test_creates_note_if_empty(self, bot, mock_nm):
        mock_nm.read_plain.return_value = ""
        bot._append_to_inbox("- [12:00] Only note")
        saved = mock_nm.save_note.call_args[0]
        assert saved[0] == "Inbox"
        assert "Only note" in saved[1]
        bot._on_inbox_updated.assert_called_once()

    def test_adds_newline_if_missing(self, bot, mock_nm):
        mock_nm.read_plain.return_value = "- [10:00] First (no trailing newline)"
        bot._append_to_inbox("- [11:00] Second")
        saved = mock_nm.save_note.call_args[0][1]
        assert saved.endswith("- [11:00] Second\n")


# ── _download_file ──


class TestDownloadFile:
    def test_downloads_and_writes_bytes(self, bot, notes_dir):
        file_id = "file123"
        dest = notes_dir / "test.pdf"
        get_file_resp = {
            "ok": True,
            "result": {"file_id": file_id, "file_path": "docs/file123.pdf"},
        }
        call_count = [0]

        def urlopen_side_effect(*_a, **_kw):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                return io.BytesIO(json.dumps(get_file_resp).encode())
            else:
                return io.BytesIO(b"fake_pdf_content")

        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urlopen_side_effect
            result = bot._download_file(file_id, dest)

        assert result is True
        assert dest.read_bytes() == b"fake_pdf_content"

    def test_get_file_failure_returns_false(self, bot, notes_dir):
        with patch.object(urllib.request, "urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("Network error")
            result = bot._download_file("fid", notes_dir / "x.pdf")
        assert result is False


# ── test_connection ──


class TestTestConnection:
    def test_success_returns_none(self, bot):
        with patch.object(
            bot,
            "_api_call",
            return_value={"ok": True, "result": {"username": "my_bot"}},
        ):
            error = bot.test_connection()
        assert error is None

    def test_api_error_returns_message(self, bot):
        with patch.object(
            bot,
            "_api_call",
            return_value={"ok": False, "description": "Unauthorized"},
        ):
            error = bot.test_connection()
        assert error is not None
        assert "Unauthorized" in error

    def test_network_error_returns_message(self, bot):
        with patch.object(bot, "_api_call", return_value=None):
            error = bot.test_connection()
        assert error is not None
        assert "Network error" in error


# ── start / stop ──


class TestLifecycle:
    def test_start_stops_polling_thread(self, bot):
        bot.start()
        assert bot.is_running()
        assert bot._thread is not None
        assert bot._thread.daemon is True
        bot.stop()
        assert not bot.is_running()

    def test_start_with_empty_token_does_nothing(self, bot):
        bot.token = ""
        bot.start()
        # Should not start since token is empty
        assert not bot.is_running()

    def test_double_start_is_noop(self, bot):
        bot.start()
        thread_id = id(bot._thread)
        bot.start()  # second start should be no-op
        assert id(bot._thread) == thread_id
        bot.stop()

    def test_stop_without_start_is_safe(self, bot):
        bot.stop()  # should not raise
