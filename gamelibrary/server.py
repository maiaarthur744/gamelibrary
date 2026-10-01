import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import store
from .grouping import group_games

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _clean_tags(raw: list) -> list[str]:
    """Trim, collapse whitespace and drop case-insensitive duplicates."""
    seen: dict[str, str] = {}
    for tag in raw:
        tag = " ".join(str(tag).split())[:40]
        if tag:
            seen.setdefault(tag.lower(), tag)
    return list(seen.values())[:30]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", (STATIC_DIR / "index.html").read_bytes())
        elif self.path == "/api/games":
            conn = store.connect()
            rows = conn.execute("SELECT * FROM games").fetchall()
            groups = group_games(
                rows, store.hidden_keys(conn), store.all_tags(conn), store.all_status(conn),
                store.split_keys(conn),
            )
            body = json.dumps(groups).encode()
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:
        # Requiring a JSON content type forces a CORS preflight, so other websites
        # open in your browser can't hide games through this localhost server.
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._send(400, "text/plain", b"bad request")
            return
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            conn = store.connect()
            if self.path == "/api/tags":
                store.set_tags(conn, data["key"], _clean_tags(data.get("tags", [])))
            elif self.path in ("/api/hide", "/api/unhide"):
                store.set_hidden(conn, data["key"], data.get("title", ""), self.path == "/api/hide")
            elif self.path == "/api/status":
                store.set_status(conn, data["key"], data.get("status"))
            elif self.path in ("/api/split", "/api/unsplit"):
                key = store.entry_key(data["platform"], data["platform_id"])
                store.set_split(conn, key, self.path == "/api/split")
            else:
                self._send(404, "text/plain", b"not found")
                return
        except (KeyError, ValueError, TypeError):
            self._send(400, "text/plain", b"bad request")
            return
        self._send(200, "application/json", b"{}")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def serve(port: int, open_browser: bool) -> None:
    # Bound to localhost only: this exposes your whole library.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Serving on {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
