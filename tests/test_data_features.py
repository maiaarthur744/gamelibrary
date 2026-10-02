"""Ratings in half stars, editable developer and hours, and the Steam developer lookup. Temporary databases only.

Run from the project root:  python -m unittest discover tests -v
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("GAMELIBRARY_DATA_DIR", tempfile.mkdtemp(prefix="gamelibrary-test-"))

from gamelibrary import net, server, store  # noqa: E402
from gamelibrary.grouping import group_games  # noqa: E402
from gamelibrary.models import Game  # noqa: E402
from gamelibrary.providers import epic, steam  # noqa: E402


class TempDatabase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gamelibrary-db-")
        patcher = mock.patch.object(store, "DB_PATH", Path(self.dir) / "library.db")
        patcher.start()
        self.addCleanup(patcher.stop)


class RatingMigration(TempDatabase):
    def test_old_whole_star_ratings_are_doubled_once(self):
        raw = sqlite3.connect(store.DB_PATH)
        raw.execute("CREATE TABLE ratings (key TEXT PRIMARY KEY, rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5))")
        raw.executemany("INSERT INTO ratings VALUES (?, ?)", [("a", 4), ("b", 5), ("c", 1)])
        raw.commit()
        raw.close()

        conn = store.connect()
        self.assertEqual(store.all_ratings(conn), {"a": 8, "b": 10, "c": 2})
        conn = store.connect()  # opening again must not double them again
        self.assertEqual(store.all_ratings(conn), {"a": 8, "b": 10, "c": 2})
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("ratings_old", tables)
        store.set_rating(conn, "d", 9)  # the new table accepts half-star values above 5

    def test_a_fresh_database_starts_on_the_new_scale(self):
        conn = store.connect()
        store.set_rating(conn, "x", 10)
        self.assertEqual(store.connect().execute("SELECT rating FROM ratings").fetchone()[0], 10)


class RatingValidation(TempDatabase):
    def test_accepts_1_to_10_only(self):
        conn = store.connect()
        for ok in (1, 7, 10):
            store.set_rating(conn, "k", ok)
        store.set_rating(conn, "k", None)
        self.assertEqual(store.all_ratings(conn), {})
        for bad in (0, 11, -1, 4.5, True, "5", [5]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                store.set_rating(conn, "k", bad)


class EditableFacts(TempDatabase):
    def groups(self, conn):
        rows = conn.execute("SELECT * FROM games").fetchall()
        return {g["title"]: g for g in group_games(rows, store.load_meta(conn))}

    def test_developer_automatic_then_typed_then_back_to_automatic(self):
        conn = store.connect()
        store.replace_platform(conn, "epic", [Game("epic", "1", "Dying Light", developer="Techland S.A.")])
        store.replace_platform(conn, "gog", [Game("gog", "2", "Gothic")])  # GOG has no developer data
        g = self.groups(conn)
        self.assertEqual((g["Dying Light"]["developer"], g["Dying Light"]["developer_custom"]), ("Techland S.A.", False))
        self.assertEqual(g["Gothic"]["developer"], "")

        store.set_developer(conn, "gothic", "  Piranha   Bytes ")
        store.set_developer(conn, "dyinglight", "Techland")
        g = self.groups(conn)
        self.assertEqual(g["Gothic"]["developer"], "Piranha Bytes")
        self.assertTrue(g["Gothic"]["developer_custom"])
        self.assertEqual((g["Dying Light"]["developer"], g["Dying Light"]["developer_auto"]), ("Techland", "Techland S.A."))

        store.set_developer(conn, "dyinglight", "")  # emptying the field restores the automatic one
        self.assertEqual(self.groups(conn)["Dying Light"]["developer"], "Techland S.A.")

    def test_typed_hours_replace_the_automatic_total(self):
        conn = store.connect()
        store.replace_platform(conn, "steam", [Game("steam", "1", "Hades", playtime_minutes=600)])
        store.replace_platform(conn, "gog", [Game("gog", "2", "Hades", playtime_minutes=120)])
        store.replace_platform(conn, "battlenet", [Game("battlenet", "3", "Diablo IV")])
        g = self.groups(conn)
        self.assertEqual((g["Hades"]["playtime_minutes"], g["Hades"]["playtime_auto"], g["Hades"]["playtime_custom"]), (720, 720, False))
        self.assertIsNone(g["Diablo IV"]["playtime_minutes"])

        store.set_playtime(conn, "hades", 20.5)
        store.set_playtime(conn, "diabloiv", 0)  # zero hours is a real answer, not "unset"
        g = self.groups(conn)
        self.assertEqual((g["Hades"]["playtime_minutes"], g["Hades"]["playtime_auto"], g["Hades"]["playtime_custom"]), (1230, 720, True))
        self.assertEqual((g["Diablo IV"]["playtime_minutes"], g["Diablo IV"]["playtime_custom"]), (0, True))

        store.set_playtime(conn, "hades", None)
        self.assertEqual(self.groups(conn)["Hades"]["playtime_minutes"], 720)

    def test_hours_validation(self):
        conn = store.connect()
        for bad in (-1, 100001, float("nan"), "abc", True, [1]):
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                store.set_playtime(conn, "k", bad)
        store.set_playtime(conn, "k", "")
        self.assertEqual(store.all_playtimes(conn), {})


class Endpoints(TempDatabase):
    def setUp(self):
        super().setUp()
        store.connect()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_validation_messages_reach_the_page(self):
        self.assertEqual(self.post("/api/rating", {"key": "g", "rating": 10})[0], 200)
        self.assertEqual(self.post("/api/rating", {"key": "g", "rating": 7})[0], 200)
        self.assertEqual(self.post("/api/rating", {"key": "g", "rating": 11})[0], 400)
        self.assertEqual(self.post("/api/rating", {"key": "g", "rating": 3.5})[0], 400)
        self.assertEqual(self.post("/api/playtime", {"key": "g", "hours": 12.5})[0], 200)
        status, text = self.post("/api/playtime", {"key": "g", "hours": -3})
        self.assertEqual((status, text), (400, "as horas jogadas precisam estar entre 0 e 100000"))
        self.assertEqual(self.post("/api/playtime", {"key": "g", "hours": "abc"})[0], 400)
        self.assertEqual(self.post("/api/playtime", {"key": "g", "hours": None})[0], 200)
        self.assertEqual(self.post("/api/developer", {"key": "g", "developer": "Supergiant"})[0], 200)
        self.assertEqual(self.post("/api/developer", {"key": "g", "developer": 5})[0], 400)


def steam_store_response(ids, known_to_store):
    items = []
    for appid in ids:
        if appid in known_to_store:
            items.append({
                "appid": appid,
                "assets": {"asset_url_format": f"steam/apps/{appid}/${{FILENAME}}", "library_capsule": "h/library_600x900.jpg"},
                "basic_info": {"developers": known_to_store[appid]},
            })
        else:
            items.append({"appid": 0})  # what the store returns for an app it doesn't know
    return {"response": {"store_items": items}}


class SteamDeveloperLookup(TempDatabase):
    OWNED = {"response": {"games": [{"appid": 10, "name": "Two Devs"}, {"appid": 20, "name": "One Dev"}, {"appid": 30, "name": "Delisted"}]}}
    STORE = {10: [{"name": "Studio A"}, {"name": "Studio B"}], 20: [{"name": "Studio C"}]}

    def sync(self, fail_store=False):
        calls = []

        def get(url, **kwargs):
            calls.append(url)
            if "IStoreBrowseService" in url:
                if fail_store:
                    raise steam.httpx.ConnectError("down")
                ids = [i["appid"] for i in json.loads(kwargs["params"]["input_json"])["ids"]]
                body = steam_store_response(ids, self.STORE)
            else:
                body = self.OWNED
            return mock.Mock(raise_for_status=lambda: None, json=lambda: body)

        with mock.patch.dict(os.environ, {"STEAM_API_KEY": "k", "STEAM_ID": "1"}), \
                mock.patch("gamelibrary.net.get", get), net.session(), net.provider("steam"):
            games = steam.fetch_games()
        store.replace_platform(store.connect(), "steam", games)
        return {g.title: g for g in games}, calls

    def test_developers_come_from_the_store_call_and_are_not_asked_again(self):
        games, calls = self.sync()
        self.assertEqual(len(calls), 2)  # the library + one store batch for all three games
        self.assertEqual(games["Two Devs"].developer, "Studio A, Studio B")
        self.assertEqual(games["One Dev"].developer, "Studio C")
        self.assertEqual(games["Delisted"].developer, "", "the store doesn't know it: stored as empty, not as unknown")

        games, calls = self.sync()
        self.assertEqual(len(calls), 1, "everything was already asked about, including the delisted game")
        self.assertEqual(games["Two Devs"].developer, "Studio A, Studio B")

    def test_a_failed_store_call_is_retried_next_time(self):
        games, calls = self.sync(fail_store=True)
        self.assertIsNone(games["One Dev"].developer, "unknown, not empty")
        self.assertEqual(len(calls), 2)
        games, calls = self.sync()
        self.assertEqual(games["One Dev"].developer, "Studio C")


class EpicDeveloper(unittest.TestCase):
    def test_developer_is_read_from_legendary_metadata_without_extra_requests(self):
        listing = [
            {"app_name": "a", "app_title": "Dying Light", "metadata": {"developer": "Techland S.A.", "keyImages": []}},
            {"app_name": "b", "app_title": "No Metadata"},
        ]
        with mock.patch.object(epic, "_run", return_value=(0, json.dumps(listing), [], False)):
            games = {g.title: g for g in epic.fetch_games()}
        self.assertEqual(games["Dying Light"].developer, "Techland S.A.")
        self.assertEqual(games["No Metadata"].developer, "")


if __name__ == "__main__":
    unittest.main()
