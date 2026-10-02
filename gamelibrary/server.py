import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import covers, store
from .grouping import group_games
from .providers import manual

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
            meta = store.load_meta(conn)
            meta.manual = manual.entry_keys()
            self._send(200, "application/json", json.dumps(group_games(rows, meta)).encode())
        elif self.path.startswith("/covers/"):
            found = covers.read(self.path.removeprefix("/covers/"))
            if found:
                self._send(200, found[1], found[0], cache="public, max-age=31536000, immutable")
            else:
                self._send(404, "text/plain", b"not found")
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:
        # Requiring a JSON content type forces a CORS preflight, so other websites
        # open in your browser can't hide games through this localhost server.
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._send(400, "text/plain", b"bad request")
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > 10 * 1024 * 1024:
            self._send(413, "text/plain", b"too large")
            return
        try:
            data = json.loads(self.rfile.read(length))
            conn = store.connect()
            if self.path == "/api/tags":
                store.set_tags(conn, data["key"], _clean_tags(data.get("tags", [])))
            elif self.path in ("/api/hide", "/api/unhide"):
                store.set_hidden(conn, data["key"], data.get("title", ""), self.path == "/api/hide")
            elif self.path == "/api/status":
                store.set_status(conn, data["key"], data.get("statuses", []))
            elif self.path == "/api/rating":
                store.set_rating(conn, data["key"], data.get("rating"))
            elif self.path == "/api/note":
                store.set_note(conn, data["key"], data.get("note", ""))
            elif self.path == "/api/cover":
                store.set_cover(conn, data["key"], data.get("url"))
            elif self.path == "/api/cover/upload":
                store.set_cover(conn, data["key"], covers.save_data_url(data["data_url"]))
            elif self.path == "/api/dates":
                store.set_dates(conn, data["key"], data.get("started"), data.get("finished"))
            elif self.path == "/api/manual/add":
                manual.add_entry(
                    data["platform"], data["title"], data.get("cover_url"),
                    data.get("playtime_hours"), data.get("url"),
                )
                manual.sync(conn)
            elif self.path == "/api/manual/remove":
                manual.remove_entry(data["platform"], data["platform_id"])
                manual.sync(conn)
            elif self.path in ("/api/split", "/api/unsplit"):
                key = store.entry_key(data["platform"], data["platform_id"])
                store.set_split(conn, key, self.path == "/api/split")
            else:
                self._send(404, "text/plain", b"not found")
                return
        except ValueError as e:  # messages are written for the user and shown in the page
            self._send(400, "text/plain; charset=utf-8", str(e).encode())
            return
        except (KeyError, TypeError):
            self._send(400, "text/plain", b"bad request")
            return
        self._send(200, "application/json", b"{}")

    def _send(self, status: int, content_type: str, body: bytes, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def serve(port: int, open_browser: bool) -> None:
    # Bound to localhost only: this exposes your whole library.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Serving on {url} (Ctrl+C to stop)")
    print("Este servidor não faz chamadas externas (só o `sync` e os logins fazem).")
    print("O navegador carrega as capas direto dos CDNs das lojas; isso não envolve a sua conta.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
