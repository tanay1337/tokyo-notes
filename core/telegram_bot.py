"""Telegram bot daemon — polls for messages and captures them into Inbox.md.

Uses only the Telegram Bot REST API via urllib (stdlib).  Runs as a daemon
thread inside the GUI application.  Text messages, photos, and PDF documents
are appended to the Inbox note; everything else is silently ignored.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Thread
from typing import Any, Callable

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot"
_FILE_BASE = "https://api.telegram.org/file/bot"
_POLL_INTERVAL = 5.0
_POLL_TIMEOUT = 10


class TelegramBot:
    """Polls Telegram for new messages and appends them to a target note."""

    def __init__(
        self,
        token: str,
        notes_manager: Any,
        notes_dir: Path,
        target_note: str = "Inbox",
        separator: bool = False,
        prefix: str = "",
        owner_id: int | None = None,
        on_inbox_updated: Callable[[], Any] | None = None,
    ) -> None:
        self.token = token
        self.nm = notes_manager
        self.notes_dir = notes_dir
        self.target_note = target_note
        self.separator = separator
        self.prefix = prefix
        self.owner_id = owner_id
        self._on_inbox_updated = on_inbox_updated
        self._offset: int = 0
        self._running: bool = False
        self._thread: Thread | None = None

    # ── public API ──

    def start(self) -> None:
        if self._running:
            logger.debug("Telegram bot already running")
            return
        if not self.token:
            logger.debug("No Telegram token configured — bot not started")
            return
        self._running = True
        self._thread = Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Telegram bot started (polling every %.1fs)", _POLL_INTERVAL)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        logger.info("Telegram bot stopped")

    def is_running(self) -> bool:
        return self._running

    def test_connection(self) -> str | None:
        """Call getMe to validate the token.  Returns the bot username on success,
        or an error message on failure."""
        result = self._api_call("getMe")
        if result is None:
            return "Network error — could not reach Telegram API"
        if not result.get("ok"):
            desc = result.get("description", "Unknown error")
            return f"API error: {desc}"
        username = result.get("result", {}).get("username", "?")
        logger.info("Telegram connection OK — bot @%s", username)
        return None  # success

    # ── polling ──

    def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    msg = update.get("message")
                    if msg is not None:
                        self._handle_message(msg)
                    self._offset = update["update_id"] + 1
            except Exception:
                logger.exception("Telegram poll error")
            for _ in range(int(_POLL_INTERVAL * 10)):
                if not self._running:
                    break
                time.sleep(0.1)

    def _get_updates(self) -> list[dict[str, Any]]:
        params = {"offset": self._offset, "timeout": _POLL_TIMEOUT}
        result = self._api_call("getUpdates", params)
        if result is None:
            return []
        if not result.get("ok"):
            desc = result.get("description", "Unknown error")
            # 409 — conflict with another bot instance, 401 — invalid token
            if result.get("error_code") in (401, 409):
                logger.error(
                    "Telegram API error %s: %s — stopping polling",
                    result.get("error_code"),
                    desc,
                )
                self._running = False
            else:
                logger.warning("Telegram getUpdates error: %s", desc)
            return []
        return result.get("result", [])

    # ── message handling ──

    def _handle_message(self, msg: dict[str, Any]) -> None:
        chat_id = msg.get("chat", {}).get("id")
        if chat_id is None:
            return

        if not self.owner_id:
            logger.debug("No owner ID configured — rejecting message")
            return
        sender = msg.get("from", {}).get("id")
        if sender != self.owner_id:
            logger.debug("Ignoring message from non-owner user %s", sender)
            return

        photo = msg.get("photo")
        document = msg.get("document")
        text = msg.get("text", "")

        if photo:
            file_id = photo[-1]["file_id"]
            caption = msg.get("caption", "") or ""
            ext = ".jpg"
            dest = self.notes_dir / ".images" / f"telegram_{file_id}{ext}"
            if self._download_file(file_id, dest):
                caption_part = f"{caption} " if caption else ""
                line = f"{caption_part}![](.images/telegram_{file_id}{ext})"
                self._append_to_inbox(line)
                return
            else:
                line = caption or "(photo download failed)"
                self._append_to_inbox(line)
                return

        if document and document.get("mime_type") == "application/pdf":
            file_id = document["file_id"]
            caption = msg.get("caption", "") or ""
            dest = self.notes_dir / ".documents" / f"telegram_{file_id}.pdf"
            if self._download_file(file_id, dest):
                caption_part = f"{caption} " if caption else ""
                line = f"{caption_part}![](.documents/telegram_{file_id}.pdf)"
                self._append_to_inbox(line)
                return
            else:
                line = caption or "(PDF download failed)"
                self._append_to_inbox(line)
                return

        if text:
            line = text
            self._append_to_inbox(line)
            return

        # Silently ignore stickers, GIFs, voice, video, commands

    def _append_to_inbox(self, line: str) -> None:
        """Append a line to the target note, creating it if it doesn't exist."""
        note = self.target_note
        try:
            content = self.nm.read_plain(note)
            if content and not content.endswith("\n"):
                content += "\n"
            if content and self.separator:
                content += "\n"
            content += self.prefix + line + "\n"
            self.nm.save_note(note, content)
            logger.debug("Appended to %s: %s", note, line)
            if self._on_inbox_updated is not None:
                self._on_inbox_updated()
        except Exception:
            logger.exception("Failed to append to %s", note)

    # ── file download ──

    def _download_file(self, file_id: str, dest: Path) -> bool:
        """Download a file from Telegram to *dest*.  Returns True on success."""
        try:
            result = self._api_call("getFile", {"file_id": file_id})
            if result is None or not result.get("ok"):
                logger.warning("getFile failed for %s", file_id)
                return False
            file_path = result.get("result", {}).get("file_path")
            if not file_path:
                logger.warning("No file_path in getFile response for %s", file_id)
                return False
            url = f"{_FILE_BASE}{self.token}/{file_path}"
            req = urllib.request.Request(url, headers={"User-Agent": "TokyoNotes/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            logger.debug("Downloaded telegram file %s -> %s", file_id, dest)
            return True
        except Exception:
            logger.exception("Failed to download telegram file %s", file_id)
            return False

    # ── helpers ──

    def _send_message(self, chat_id: int, text: str) -> None:
        """Send a reply message to a chat."""
        self._api_call("sendMessage", {"chat_id": chat_id, "text": text})

    def _api_call(self, method: str, params: dict | None = None) -> dict | None:
        """Make a call to the Telegram Bot API.

        Returns the parsed JSON dict on success, or None on network error.
        """
        url = f"{_API_BASE}{self.token}/{method}"
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
            url = f"{url}?{qs}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TokyoNotes/1.0"})
            with urllib.request.urlopen(req, timeout=_POLL_TIMEOUT + 5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"ok": False, "error_code": e.code, "description": body}
        except Exception:
            logger.debug("Telegram API call failed: %s %s", method, params)
            return None
