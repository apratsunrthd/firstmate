#!/usr/bin/env python3
"""Local web chat GUI for First Mate.

Serves a single-page chat UI and proxies each message to a headless
`claude -p` invocation run from the repo root, so the session loads this
repo's AGENTS.md and behaves as First Mate. Conversation continuity across
messages is kept with `claude --resume`; the session id is cached in
state/gui-session.json (gitignored) between requests.

Also serves /api/fleet, a read-only snapshot of state/*.meta so the GUI can
show how much work is currently fanned out (task count, project, harness/
model, latest status line) alongside the chat.

Run: python3 gui/server.py   (see gui/README.md)
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_FILE = REPO_ROOT / "state" / "gui-session.json"
STATE_DIR = REPO_ROOT / "state"
HOST = "127.0.0.1"
PORT = int(os.environ.get("FM_GUI_PORT", "8756"))

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def load_session_id():
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text()).get("session_id")
    except (json.JSONDecodeError, OSError):
        return None


def save_session_id(session_id):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"session_id": session_id}))


def clear_session():
    SESSION_FILE.unlink(missing_ok=True)


def _tool_hint(block):
    """Short human-readable summary of a tool_use block's input, e.g. the
    command for Bash or the path for Read/Edit/Write."""
    inp = block.get("input") or {}
    for key in ("command", "file_path", "path", "pattern", "url", "query"):
        val = inp.get(key)
        if val:
            val = str(val)
            if len(val) > 60:
                val = val[:57] + "..."
            return f": {val}"
    return ""


def stream_claude(message, session_id):
    """Run claude headlessly with streaming JSON output, yielding progress
    dicts ({"status": "..."}) as tool calls happen so the caller can show
    live activity instead of a silent block. Always ends by yielding exactly
    one terminal dict shaped {"final": True, "reply": ..., "session_id": ...}
    or {"final": True, "error": "..."}.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        yield {
            "final": True,
            "error": (
                "claude CLI not found on PATH. Install Claude Code and make "
                "sure `claude` is runnable before using this GUI."
            ),
        }
        return

    cmd = [
        claude_bin,
        "-p",
        message,
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd += ["--resume", session_id]

    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines = []

    def drain_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    seen_hook = False
    last_tool = None

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        obj_type = obj.get("type")
        if obj_type == "system":
            if not seen_hook and obj.get("subtype") == "hook_started":
                seen_hook = True
                yield {"status": "Running startup checks..."}
        elif obj_type == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    last_tool = block.get("name") or "tool"
                    yield {"status": f"Running {last_tool}{_tool_hint(block)}"}
                elif block.get("type") == "text" and (block.get("text") or "").strip():
                    yield {"status": "Writing reply..."}
        elif obj_type == "user":
            for block in (obj.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result" and last_tool:
                    yield {"status": f"Finished {last_tool}"}
        elif obj_type == "result":
            new_session_id = obj.get("session_id") or session_id
            if obj.get("is_error"):
                yield {"final": True, "error": obj.get("result") or "claude reported an error"}
            else:
                yield {"final": True, "reply": obj.get("result") or "", "session_id": new_session_id}
            proc.wait()
            stderr_thread.join(timeout=2)
            return

    proc.wait()
    stderr_thread.join(timeout=2)
    if proc.returncode != 0:
        yield {"final": True, "error": "".join(stderr_lines).strip() or f"claude exited {proc.returncode}"}
    else:
        yield {"final": True, "error": "claude produced no result (stream ended without a result event)"}


def read_meta(path):
    """Parse a state/<id>.meta key=value file. Last value wins per key,
    matching fm_meta_get's semantics in bin/fm-backend.sh."""
    data = {}
    try:
        for line in path.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key] = value
    except OSError:
        pass
    return data


def latest_status_line(task_id):
    status_file = STATE_DIR / f"{task_id}.status"
    if not status_file.is_file():
        return None
    try:
        lines = [ln for ln in status_file.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    return lines[-1] if lines else None


def fleet_snapshot():
    tasks = []
    if STATE_DIR.is_dir():
        for meta_path in sorted(STATE_DIR.glob("*.meta")):
            task_id = meta_path.stem
            meta = read_meta(meta_path)
            tasks.append(
                {
                    "id": task_id,
                    "project": meta.get("project") or meta.get("home") or "-",
                    "kind": meta.get("kind") or "ship",
                    "harness": meta.get("harness") or "-",
                    "model": meta.get("model") or "-",
                    "mode": meta.get("mode") or "-",
                    "backend": meta.get("backend") or "tmux",
                    "latest": latest_status_line(task_id),
                }
            )
    return {"tasks": tasks, "count": len(tasks)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # required for chunked responses below

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _start_ndjson_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _write_chunk(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self.wfile.write(("%x\r\n" % len(data)).encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _end_ndjson_stream(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _serve_static(self, rel_path):
        target = (STATIC_DIR / rel_path).resolve()
        if STATIC_DIR not in target.parents and target != STATIC_DIR:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_static("index.html")
        elif self.path.startswith("/static/"):
            self._serve_static(self.path[len("/static/"):])
        elif self.path == "/api/fleet":
            self._send_json(fleet_snapshot())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/chat":
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            message = (body.get("message") or "").strip()
            if not message:
                self._send_json({"error": "empty message"}, 400)
                return
            session_id = load_session_id()
            self._start_ndjson_stream()
            try:
                for event in stream_claude(message, session_id):
                    self._write_chunk(event)
                    if event.get("final") and event.get("session_id"):
                        save_session_id(event["session_id"])
            except (BrokenPipeError, ConnectionResetError):
                pass  # client navigated away mid-stream
            finally:
                self._end_ndjson_stream()
        elif self.path == "/api/reset":
            clear_session()
            self._send_json({"ok": True})
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"First Mate GUI running at http://{HOST}:{PORT} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
