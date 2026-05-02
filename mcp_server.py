import sys
import json
import argparse
import uuid
import time
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from core.storage import NotesManager

def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}")
    sys.stdout.flush()

class NotesAPI:
    def __init__(self):
        config_path = Path.home() / ".config" / "tokyo-notes" / "tokyo-notes.json"
        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        self.notes_folder = config.get('notes_folder', "notes")
        self.notes_manager = NotesManager(notes_dir=self.notes_folder)

    def get_catalog(self):
        tools = [
            ("list_notes", "List all notes.", {"type": "object", "properties": {}}),
            ("read_note", "Read a note.", {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}),
            ("search_notes", "Search notes.", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
            ("create_note", "Create a note.", {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}}, "required": ["title", "content"]}),
            ("toggle_checkbox", "Toggle checkbox.", {"type": "object", "properties": {"title": {"type": "string"}, "line_number": {"type": "integer"}, "checked": {"type": "boolean"}}, "required": ["title", "line_number", "checked"]})
        ]
        return [{"name": n, "description": d, "inputSchema": s, "parameters": s, "type": "function", "function": {"name": n, "description": d, "parameters": s}} for n, d, s in tools]

    def handle_request(self, request):
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

        log(f" [API] Target: {name}")
        try:
            if name == "list_notes": res = "Notes:\n" + "\n".join([f"- {n}" for n in self.notes_manager.get_notes()])
            elif name == "read_note": res = self.notes_manager.read_note(args.get("title")) or "None."
            elif name == "search_notes": res = "Matches:\n" + "\n".join([f"- {n}" for n in self.notes_manager.get_notes(search_text=args.get("query"))])
            elif name == "create_note":
                t = self.notes_manager.create_note(args.get("title"))
                self.notes_manager.save_note(t, args.get("content"))
                res = f"Created {t}"
            elif name == "toggle_checkbox":
                res = "Success" if self.notes_manager.update_checkbox(args.get("title"), args.get("line_number"), args.get("checked")) else "Failed"
            else: return {"error": {"code": -1, "message": f"Unknown tool: {name}"}}
            
            log(f" [RES] Success ({len(res)} bytes)")
            return {"content": [{"type": "text", "text": res}], "result": res}
        except Exception as e:
            log(f" [!] Error: {e}")
            return {"error": {"code": -1, "message": str(e)}}

    def _refresh_manager(self):
        config_path = Path.home() / ".config" / "tokyo-notes" / "tokyo-notes.json"
        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        new_folder = config.get('notes_folder', "notes")
        if new_folder != self.notes_folder:
            self.notes_folder = new_folder
            self.notes_manager = NotesManager(notes_dir=self.notes_folder)

class OmniHandler(BaseHTTPRequestHandler):
    api = None
    
    def _send_headers(self, code=200, ctype='application/json', clen=None):
        self.send_response(code)
        origin = self.headers.get('Origin', '*')
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Vary', 'Origin')
        self.send_header('Content-Type', ctype)
        if clen: self.send_header('Content-Length', str(clen))
        self.end_headers()

    def do_OPTIONS(self):
        self._send_headers(204)

    def do_GET(self):
        log(f" >>> [GET] {self.path}")
        if self.path == "/sse":
            self._send_headers(200, 'text/event-stream')
            msg = f"event: endpoint\ndata: http://127.0.0.1:{self.server.server_port}/sse\n\n"
            self.wfile.write(msg.encode()); self.wfile.flush()
            try:
                while True:
                    time.sleep(15); self.wfile.write(b": ping\n\n"); self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
        else:
            body = json.dumps({"tools": self.api.get_catalog()}, indent=2).encode()
            self._send_headers(200, clen=len(body))
            self.wfile.write(body)

    def do_POST(self):
        log(f" >>> [POST] {self.path}")
        try:
            clen = int(self.headers.get('Content-Length', 0))
            if clen > 1 * 1024 * 1024:
                self._send_headers(413)
                return
            raw = self.rfile.read(clen)
            log(f" [REQ] RAW: {raw.decode()[:200]}...")
            req = json.loads(raw)
            res = self.api.handle_request(req)
            if res is None: # Notification
                self._send_headers(204)
                return
            out = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": res}).encode()
            self._send_headers(200, clen=len(out))
            self.wfile.write(out)
            log(f" [RES] OK.")
        except Exception as e:
            log(f" [!] Failed: {e}")
            err = str(e).encode()
            self._send_headers(500, clen=len(err))
            self.wfile.write(err)

def run_mcp_server(port=8999):
    api = NotesAPI()
    OmniHandler.api = api
    server = ThreadingHTTPServer(('127.0.0.1', port), OmniHandler)
    log(f"Tokyo Notes AI Bridge Ready on http://127.0.0.1:{port}/sse")
    server.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8999)
    args = parser.parse_args()
    run_mcp_server(args.port)
