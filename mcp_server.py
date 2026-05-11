"""MCP/HTTP bridge — exposes Tokyo Notes to AI assistants via JSON-RPC."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from core.storage import NotesManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class NotesAPI:
    """Wraps NotesManager with config-file tracking so the bridge picks up
    folder changes made inside the GTK app without needing a restart."""

    def __init__(self) -> None:
        self._config_path: Path = (
            Path.home() / ".config" / "tokyo-notes" / "tokyo-notes.json"
        )
        self._config_mtime: float = 0.0
        self.notes_folder: str = "notes"
        self.notes_manager: NotesManager | None = None
        self._refresh_manager()

    # ------------------------------------------------------------------ #
    # Tool catalogue
    # ------------------------------------------------------------------ #

    def get_catalog(self) -> list[dict[str, Any]]:
        tools = [
            (
                "list_notes",
                "List all notes.",
                {"type": "object", "properties": {}},
            ),
            (
                "read_note",
                "Read a note by title.",
                {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
            ),
            (
                "search_notes",
                "Search notes by keyword.",
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            ),
            (
                "create_note",
                "Create a new note with title and content.",
                {
                    "type": "object",
                    "properties": {
                        "title":   {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["title", "content"],
                },
            ),
            (
                "toggle_checkbox",
                "Toggle a checkbox in a note.",
                {
                    "type": "object",
                    "properties": {
                        "title":       {"type": "string"},
                        "line_number": {"type": "integer"},
                        "checked":     {"type": "boolean"},
                    },
                    "required": ["title", "line_number", "checked"],
                },
            ),
        ]
        return [
            {
                "name": n,
                "description": d,
                "inputSchema": s,
                "parameters": s,
                "type": "function",
                "function": {"name": n, "description": d, "parameters": s},
            }
            for n, d, s in tools
        ]

    # ------------------------------------------------------------------ #
    # Request handling
    # ------------------------------------------------------------------ #

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Universal handler supporting MCP, OpenAI, and Llama request formats."""
        self._refresh_manager()
        method = request.get("method", "")
        params = request.get("params", {})

        # Discovery / handshake
        if method in ("initialize", "listTools", "tools/list", "list_tools"):
            if method == "initialize":
                version = params.get("protocolVersion", "2024-11-05")
                return {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "tokyo-notes-bridge", "version": "1.0.0"},
                }
            return {"tools": self.get_catalog()}

        # Parse tool name and arguments from various envelope formats.
        name = params.get("name") or params.get("tool") or method
        args: dict[str, Any] = params.get("arguments") or params.get("args") or {}

        if "tool_calls" in params:
            call = params["tool_calls"][0]["function"]
            name = call["name"]
            args = (
                json.loads(call["arguments"])
                if isinstance(call["arguments"], str)
                else call["arguments"]
            )

        if not name or name in ("notifications/initialized", "initialized"):
            return None

        logger.info("Tool: %s", name)
        try:
            res = self._dispatch(name, args)
            logger.info("OK (%d bytes)", len(res))
            return {"content": [{"type": "text", "text": res}], "result": res}
        except Exception as e:
            logger.error("Failed: %s", e)
            return {"error": {"code": -1, "message": str(e)}}

    def _dispatch(self, name: str, args: dict[str, Any]) -> str:
        """Route a validated tool call to the appropriate NotesManager method."""
        if not self.notes_manager:
            raise RuntimeError("Notes manager not initialised")

        if name == "list_notes":
            notes = self.notes_manager.get_notes()
            return "Notes:\n" + "\n".join(f"- {n}" for n in notes)

        if name == "read_note":
            return self.notes_manager.read_note(args.get("title", "")) or "Note not found."

        if name == "search_notes":
            results = self.notes_manager.get_notes(search_text=args.get("query", ""))
            return "Matches:\n" + "\n".join(f"- {n}" for n in results)

        if name == "create_note":
            stem = self.notes_manager.create_note(
                name=args.get("title", "Untitled"),
                content=args.get("content", ""),
            )
            return f"Created '{stem}'"

        if name == "toggle_checkbox":
            ok = self.notes_manager.update_checkbox(
                args.get("title", ""),
                args.get("line_number", 0),
                args.get("checked", False),
            )
            return "Success" if ok else "Failed — checkbox not found"

        raise ValueError(f"Unknown tool: {name}")

    # ------------------------------------------------------------------ #
    # Config watcher
    # ------------------------------------------------------------------ #

    def _refresh_manager(self) -> None:
        """Reload NotesManager if the config file has changed on disk."""
        try:
            mtime = self._config_path.stat().st_mtime
            if mtime == self._config_mtime:
                return
            self._config_mtime = mtime
            config = json.loads(self._config_path.read_text(encoding="utf-8"))
            new_folder = config.get("notes_folder", "notes")
            if new_folder != self.notes_folder or self.notes_manager is None:
                self.notes_folder = new_folder
                self.notes_manager = NotesManager(notes_dir=new_folder)
        except (OSError, json.JSONDecodeError):
            if self.notes_manager is None:
                self.notes_manager = NotesManager(notes_dir="notes")


# ------------------------------------------------------------------ #
# HTTP handler
# ------------------------------------------------------------------ #

class OmniHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that speaks JSON-RPC, SSE, and OpenAI tool formats."""

    api: NotesAPI | None = None

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default access log
        logger.debug(fmt, *args)

    def _send_headers(
        self, code: int = 200, ctype: str = "application/json", clen: int | None = None
    ) -> None:
        self.send_response(code)
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Type", ctype)
        if clen is not None:
            self.send_header("Content-Length", str(clen))
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._send_headers(204)

    def do_GET(self) -> None:
        logger.info("GET %s", self.path)
        if self.path == "/sse":
            self._send_headers(200, "text/event-stream")
            msg = (
                f"event: endpoint\n"
                f"data: http://127.0.0.1:{self.server.server_port}/sse\n\n"
            )
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()
            try:
                while True:
                    time.sleep(15)
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                logger.debug("SSE client disconnected: %s", e)
        else:
            if not self.api:
                self._send_headers(500)
                return
            body = json.dumps({"tools": self.api.get_catalog()}, indent=2).encode("utf-8")
            self._send_headers(200, clen=len(body))
            self.wfile.write(body)

    def do_POST(self) -> None:
        logger.info("POST %s", self.path)
        try:
            clen = int(self.headers.get("Content-Length", 0))
            if clen > 1 * 1024 * 1024:
                self._send_headers(413)
                return
            raw = self.rfile.read(clen)
            logger.debug("REQ: %s", raw.decode("utf-8")[:200])
            req = json.loads(raw)
            if not self.api:
                raise RuntimeError("API not initialised")
            result = self.api.handle_request(req)
            if result is None:   # notification, no response needed
                self._send_headers(204)
                return
            out = json.dumps(
                {"jsonrpc": "2.0", "id": req.get("id"), "result": result}
            ).encode("utf-8")
            self._send_headers(200, clen=len(out))
            self.wfile.write(out)
        except Exception as e:
            logger.error("Request failed: %s", e)
            err = str(e).encode("utf-8")
            self._send_headers(500, clen=len(err))
            self.wfile.write(err)


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

def run_mcp_server(port: int = 8999) -> None:
    api = NotesAPI()
    OmniHandler.api = api
    server = ThreadingHTTPServer(("127.0.0.1", port), OmniHandler)
    logger.info("Tokyo Notes AI Bridge ready on http://127.0.0.1:%d/sse", port)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tokyo Notes MCP bridge")
    parser.add_argument("--port", type=int, default=8999)
    run_mcp_server(parser.parse_args().port)
