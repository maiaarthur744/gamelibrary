"""Guarantees about outside requests. No real network is used: everything talks to a local server.

Run from the project root:  python -m unittest discover tests -v
"""
import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

# Must be set before gamelibrary is imported: tests never touch the real library.
os.environ["GAMELIBRARY_DATA_DIR"] = tempfile.mkdtemp(prefix="gamelibrary-test-")
os.environ.pop("GAMELIBRARY_MAX_REQUESTS", None)

import argparse  # noqa: E402

from gamelibrary import cli, net, server, store  # noqa: E402
from gamelibrary.models import Game  # noqa: E402
from gamelibrary.providers import PROVIDERS, manual, steam  # noqa: E402

PACKAGE = Path(__file__).resolve().parent.parent / "gamelibrary"


class Echo(BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):
        Echo.hits.append(self.path)
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start(handler) -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def captured_log(fn) -> str:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        fn()
    return err.getvalue()


class NetModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.url = start(Echo)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        Echo.hits.clear()

    def test_blocked_outside_a_session(self):
        with self.assertRaises(net.NetworkNotAllowed):
            net.get(self.url + "/x")
        self.assertEqual(Echo.hits, [])

    def test_blocked_inside_a_session_without_a_provider(self):
        with net.session(), self.assertRaises(net.NetworkNotAllowed):
            net.get(self.url + "/x")
        self.assertEqual(Echo.hits, [])

    def test_blocked_again_after_the_session_ends(self):
        with net.session(), net.provider("steam"):
            net.get(self.url + "/x")
        with self.assertRaises(net.NetworkNotAllowed):
            net.get(self.url + "/x")
        self.assertEqual(len(Echo.hits), 1)

    def test_logs_host_and_path_but_never_the_query(self):
        def run():
            with net.session(), net.provider("steam"):
                net.get(self.url + "/api/thing", params={"key": "SUPERSECRET"})

        log = captured_log(run)
        self.assertIn("[net] steam  GET 127.0.0.1:", log)
        self.assertIn("/api/thing -> 200", log)
        self.assertIn("[1/20]", log)
        self.assertNotIn("SUPERSECRET", log)
        self.assertEqual(Echo.hits, ["/api/thing?key=SUPERSECRET"])  # it was sent, just not printed

    def test_budget_stops_before_sending(self):
        def run():
            with net.session(), net.provider("gog"):
                with mock.patch.dict(net.BUDGETS, {"gog": 3}):
                    net._budget = 3
                    for _ in range(3):
                        net.get(self.url + "/x")
                    with self.assertRaises(net.BudgetExceeded):
                        net.get(self.url + "/x")

        log = captured_log(run)
        self.assertEqual(len(Echo.hits), 3, "the 4th request must not reach the server")
        self.assertIn("BLOQUEADA", log)

    def test_manual_provider_has_no_budget_at_all(self):
        with net.session(), net.provider("manual"), self.assertRaises(net.BudgetExceeded):
            net.get(self.url + "/x")
        self.assertEqual(Echo.hits, [])

    def test_requests_are_spaced_out(self):
        with net.session(), net.provider("steam"):
            started = time.monotonic()
            for _ in range(3):
                net.get(self.url + "/x")
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 2 * net.MIN_INTERVAL - 0.05)

    def test_budget_can_be_overridden_by_env_but_not_for_manual(self):
        with mock.patch.dict(os.environ, {"GAMELIBRARY_MAX_REQUESTS": "7"}):
            self.assertEqual(net.budget_for("steam"), 7)
            self.assertEqual(net.budget_for("manual"), 0)
        with mock.patch.dict(os.environ, {"GAMELIBRARY_MAX_REQUESTS": "lots"}), self.assertRaises(ValueError):
            net.budget_for("steam")

    def test_summary_counts_per_provider(self):
        def run():
            with net.session():
                with net.provider("steam"):
                    net.get(self.url + "/a")
                    net.get(self.url + "/b")
                with net.provider("gog"):
                    net.get(self.url + "/c")
                net.print_summary()

        log = captured_log(run)
        self.assertIn("Resumo: steam 2, gog 1. Total: 3 chamadas externas.", log)


class LegendaryWrapper(unittest.TestCase):
    def test_requests_are_logged_capped_and_secrets_stay_out_of_the_log(self):
        httpd, url = start(Echo)
        Echo.hits.clear()
        code = (
            "from gamelibrary import legendary_logged as l\n"
            "l.install(2)\n"
            "import requests\n"
            "s = requests.Session()\n"
            f"for i in range(3):\n"
            f"    try: s.get('{url}/x?token=SECRET')\n"
            f"    except l.BudgetExceeded: print('blocked', i)\n"
        )
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        httpd.shutdown()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(Echo.hits), 2, "only the first two requests may reach the server")
        self.assertIn("blocked 2", done.stdout)
        self.assertIn("[net] epic   GET 127.0.0.1:", done.stderr)
        self.assertIn("[1/2]", done.stderr)
        self.assertIn("BLOQUEADA", done.stderr)
        self.assertNotIn("SECRET", done.stderr)


class SteamRequestVolume(unittest.TestCase):
    """A sync should be one request unless there are games whose cover is not known yet."""

    def fake_get(self, calls):
        owned = {"response": {"games": [{"appid": 10, "name": "Old A"}, {"appid": 20, "name": "Old B"}]}}

        def get(url, **kwargs):
            calls.append(url)
            body = owned
            if "IStoreBrowseService" in url:
                ids = [i["appid"] for i in json.loads(kwargs["params"]["input_json"])["ids"]]
                body = {"response": {"store_items": [
                    {"appid": a, "assets": {"asset_url_format": "steam/apps/%d/${FILENAME}" % a,
                                            "library_capsule": "hash/library_600x900.jpg"}} for a in ids]}}
            return mock.Mock(raise_for_status=lambda: None, json=lambda: body)

        return get

    def run_fetch(self, **kwargs):
        calls = []
        with mock.patch.dict(os.environ, {"STEAM_API_KEY": "k", "STEAM_ID": "1"}), \
                mock.patch("gamelibrary.net.get", self.fake_get(calls)), \
                net.session(), net.provider("steam"):
            return steam.fetch_games(**kwargs), calls

    def test_first_sync_asks_the_store_once_per_50_games_then_never_again(self):
        games, calls = self.run_fetch()
        self.assertEqual(len(calls), 2, "1 for the library + 1 store batch for 2 unknown covers")
        store.replace_platform(store.connect(), "steam", games)

        games, calls = self.run_fetch()
        self.assertEqual(len(calls), 1, "covers are already known: just the library request")
        self.assertTrue(all("store_item_assets" in g.cover_url for g in games))

    def test_refresh_flag_asks_again(self):
        _, calls = self.run_fetch(refresh_covers=True)
        self.assertEqual(len(calls), 2)


class SyncCommand(unittest.TestCase):
    def test_plain_sync_wraps_every_provider_and_blocks_surprise_requests(self):
        seen = {}

        def fake(name):
            def fetch(**kwargs):
                seen[name] = net._active and net._provider == name
                return [Game(platform=name, platform_id="1", title=name)]
            return fetch

        fakes = {k: fake(k) for k in PROVIDERS if k != "manual"}
        with mock.patch.dict(PROVIDERS, fakes), mock.patch("httpx.get", side_effect=AssertionError("network")):
            captured_log(lambda: cli.cmd_sync(argparse.Namespace(provider=None, refresh_covers=False)))
        self.assertEqual(seen, {"steam": True, "gog": True, "epic": True})
        self.assertFalse(net._active, "the session must be closed when sync ends")

    def test_manual_sync_never_touches_the_network(self):
        with mock.patch("httpx.get", side_effect=AssertionError("network")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process")):
            captured_log(lambda: cli.cmd_sync(argparse.Namespace(provider="manual", refresh_covers=False)))


class ServerStaysOffline(unittest.TestCase):
    def test_no_endpoint_reaches_the_outside(self):
        key = "somegame"
        store.replace_platform(store.connect(), "battlenet",
                               [Game(platform="battlenet", platform_id="1", title="Some Game")])
        httpd, base = start(server.Handler)

        def call(path, body=None):
            req = urllib.request.Request(
                base + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json"} if body is not None else {},
            )
            try:
                return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e:
                return e.code

        png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
        posts = [
            ("/api/tags", {"key": key, "tags": ["a"]}), ("/api/hide", {"key": key, "title": "x"}),
            ("/api/unhide", {"key": key}), ("/api/status", {"key": key, "statuses": ["played"]}),
            ("/api/rating", {"key": key, "rating": 4}), ("/api/note", {"key": key, "note": "hi"}),
            ("/api/dates", {"key": key, "started": "2026-01-01"}),
            ("/api/cover", {"key": key, "url": "https://example.com/c.jpg"}),
            ("/api/cover/upload", {"key": key, "data_url": png}),
            ("/api/manual/add", {"platform": "itch", "title": "Celeste"}),
            ("/api/split", {"platform": "battlenet", "platform_id": "1"}),
        ]
        boom = AssertionError("outside request from the server")
        with mock.patch("httpx.Client.send", side_effect=boom), mock.patch("httpx.get", side_effect=boom), \
                mock.patch("requests.sessions.Session.request", side_effect=boom), \
                mock.patch("subprocess.Popen", side_effect=boom), mock.patch("subprocess.run", side_effect=boom):
            self.assertEqual(call("/"), 200)
            self.assertEqual(call("/api/games"), 200)
            for path, body in posts:
                self.assertEqual(call(path, body), 200, path)
            games = json.loads(urllib.request.urlopen(base + "/api/games").read())
            celeste = next(g for g in games if g["title"] == "Celeste")
            pid = celeste["entries"][0]["platform_id"]
            self.assertEqual(call("/api/manual/remove", {"platform": "itch", "platform_id": pid}), 200)
        httpd.shutdown()


class StaticPolicy(unittest.TestCase):
    """Only these files may call the network or start processes; everything else has to use net.get."""

    ALLOWED = {"net.py": {"httpx"}, "providers/epic.py": {"subprocess"}}
    WATCHED = {"httpx", "requests", "subprocess", "socket", "urllib"}

    def test_only_the_expected_files_make_outside_calls(self):
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            rel = path.relative_to(PACKAGE).as_posix()
            if rel == "legendary_logged.py":  # patches requests inside the Legendary process; sends nothing itself
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    root = node.func
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in self.WATCHED and root.id not in self.ALLOWED.get(rel, set()):
                        offenders.append(f"{rel}:{node.lineno} calls {root.id}.{node.func.attr}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
