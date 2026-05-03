"""MCP/HTTP server for note access."""
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

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def log(msg: str) -> None:
    """Shim for backwards compatibility."""
    logger.info(msg)

class NotesAPI:
    def __init__(self) -> None:
        self._config_path: Path = Path.home() / ".config" / "tokyo-notes" / "tokyo-notes.json"
        self._config_mtime: float = 0.0
        self.notes_folder: str = "notes"
        self.notes_manager: NotesManager | None = None
        self._refresh_manager()

    def get_catalog(self) -> list[dict[str, Any]]:
        """Returns the list of available tools."""
        tools = [
            ("list_notes", "List all notes.", {"type": "object", "properties": {}}),
            ("read_note", "Read a note.", {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}),
            ("search_notes", "Search notes.", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
            ("create_note", "Create a note.", {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}}, "required": ["title", "content"]}),
            ("toggle_checkbox", "Toggle checkbox.", {"type": "object", "properties": {"title": {"type": "string"}, "line_number": {"type": "integer"}, "checked": {"type": "boolean"}}, "required": ["title", "line_number", "checked"]})
        ]
        return [
            {
                "name": n, "description": d, "inputSchema": s, "parameters": s, 
                "type": "function", "function": {"name": n, "description": d, "parameters": s}
            } 
            for n, d, s in tools
        ]

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Universal handler for MCP, OpenAI, and Llama formats."""
        self._refresh_manager()
        m = request.get("method", "")
        p = request.get("params", {})
        
        # 1. Discovery
        if m in ["initialize", "listTools", "tools/list", "list_tools"]:
            if m == "initialize":
                v = p.get("protocolVersion", "2024-11-05")
                return {"protocolVersion": v, "capabilities": {"tools": {}}, "serverInfo": {"name": "bridge", "version": "1.0.0"}}
            return {"tools": self.get_catalog()}
        
        # 2. Extract Tool Name and Args (Omni-Parser)
        name = p.get("name") or p.get("tool") or m
        args = p.get("arguments") or p.get("args") or {}
        
        # Special check for OpenAI-style 'tool_calls' inside params
        if "tool_calls" in p:
            call = p["tool_calls"][0]["function"]
            name = call["name"]
            args = json.loads(call["arguments"]) if isinstance(call["arguments"], str) else call["arguments"]

        if not name or name in ["notifications/initialized", "initialized"]:
            return None

        logger.info(f"Target: {name}")
        try:
            if not self.notes_manager:
                raise RuntimeError("Notes manager not initialized")

            if name == "list_notes":
                note_list = self.notes_manager.get_notes()
                res = "Notes:\n" + "\n".join([f"- {n}" for n in note_list])
            elif name == "read_note":
                res = self.notes_manager.read_note(args.get("title", "")) or "None."
            elif name == "search_notes":
                search_res = self.notes_manager.get_notes(search_text=args.get("query", ""))
                res = "Matches:\n" + "\n".join([f"- {n}" for n in search_res])
            elif name == "create_note":
                t = self.notes_manager.create_note(args.get("title", "Untitled"))
                self.notes_manager.save_note(t, args.get("content", ""))
                res = f"Created {t}"
            elif name == "toggle_checkbox":
                success = self.notes_manager.update_checkbox(args.get("title", ""), args.get("line_number", 0), args.get("checked", False))
                res = "Success" if success else "Failed"
            else:
                return {"error": {"code": -1, "message": f"Unknown tool: {name}"}}
            
            logger.info("Success (%d bytes)", len(res))
            return {"content": [{"type": "text", "text": res}], "result": res}
        except Exception as e:
            logger.error("Failed: %s", e)
            return {"error": {"code": -1, "message": str(e)}}

    def _refresh_manager(self) -> None:
        try:
            mtime = self._config_path.stat().st_mtime
            if mtime == self._config_mtime:
                return  # Config unchanged
            self._config_mtime = mtime
            config = json.loads(self._config_path.read_text(encoding="utf-8"))
            new_folder = config.get('notes_folder', 'notes')
            if new_folder != self.notes_folder or self.notes_manager is None:
                self.notes_folder = new_folder
                self.notes_manager = NotesManager(notes_dir=new_folder)
        except (OSError, json.JSONDecodeError):
            if self.notes_manager is None:
                self.notes_manager = NotesManager(notes_dir="notes")

class OmniHandler(BaseHTTPRequestHandler):
    api: NotesAPI | None = None
    
    def _send_headers(self, code: int = 200, ctype: str = 'application/json', clen: int | None = None) -> None:
        self.send_response(code)
        origin = self.headers.get('Origin', '*')
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Vary', 'Origin')
        self.send_header('Content-Type', ctype)
        if clen:
            self.send_header('Content-Length', str(clen))
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._send_headers(204)

    def do_GET(self) -> None:
        logger.info("GET %s", self.path)
        if self.path == "/sse":
            self._send_headers(200, 'text/event-stream')
            msg = f"event: endpoint\ndata: http://127.0.0.1:{self.server.server_port}/sse\n\n"
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
            clen = int(self.headers.get('Content-Length', 0))
            if clen > 1 * 1024 * 1024:
                self._send_headers(413)
                return
            raw = self.rfile.read(clen)
            logger.info("REQ RAW: %s...", raw.decode("utf-8")[:200])
            req = json.loads(raw)
            if not self.api:
                raise RuntimeError("API not initialized")
            res = self.api.handle_request(req)
            if res is None: # Notification
                self._send_headers(204)
                return
            out = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": res}).encode("utf-8")
            self._send_headers(200, clen=len(out))
            self.wfile.write(out)
            logger.info("RES OK.")
        except Exception as e:
            logger.error("Failed: %s", e)
            err = str(e).encode("utf-8")
            self._send_headers(500, clen=len(err))
            self.wfile.write(err)

def run_mcp_server(port: int = 8999) -> None:
    api = NotesAPI()
    OmniHandler.api = api
    server = ThreadingHTTPServer(('127.0.0.1', port), OmniHandler)
    logger.info("Tokyo Notes AI Bridge Ready on http://127.0.0.1:%d/sse", port)
    server.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8999)
    args = parser.parse_args()
    run_mcp_server(args.port)
