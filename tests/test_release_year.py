"""Release year: read from Steam and GOG, kept per game, editable. Temporary databases only.

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
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("GAMELIBRARY_DATA_DIR", tempfile.mkdtemp(prefix="gamelibrary-test-"))

from gamelibrary import net, server, store  # noqa: E402
from gamelibrary.grouping import group_games  # noqa: E402
from gamelibrary.models import Game, clean_year  # noqa: E402
from gamelibrary.providers import gog, steam  # noqa: E402


def stamp(year: int, month: int = 6, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


class TempDatabase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(store, "DB_PATH", Path(tempfile.mkdtemp(prefix="gamelibrary-db-")) / "library.db")
        patcher.start()
        self.addCleanup(patcher.stop)


class CleanYear(unittest.TestCase):
    def test_plausible_years_pass_and_junk_becomes_zero(self):
        self.assertEqual([clean_year(v) for v in (2015, "1998", 1950, 2020.0)], [2015, 1998, 1950, 2020])
        for junk in (0, "0000", 1949, 1900, 2200, None, "", "abc", [2015], -5):
            self.assertEqual(clean_year(junk), 0, repr(junk))


class SteamYear(unittest.TestCase):
    def test_the_earliest_of_original_and_steam_dates_wins(self):
        self.assertEqual(steam._release_year({"original_release_date": stamp(2006), "steam_release_date": stamp(2007)}), 2006)
        self.assertEqual(steam._release_year({"steam_release_date": stamp(2015)}), 2015)
        self.assertEqual(steam._release_year({"original_release_date": 0, "steam_release_date": stamp(1998)}), 1998)

    def test_missing_or_nonsense_dates_give_zero(self):
        for release in (None, {}, {"steam_release_date": 0}, {"steam_release_date": -5}, {"steam_release_date": "soon"},
                        {"steam_release_date": 10**15}, {"mac_release_date": stamp(2010)}):
            self.assertEqual(steam._release_year(release), 0, repr(release))


class GogYear(unittest.TestCase):
    def test_parses_the_date_object_the_listing_returns(self):
        date = {"date": "2019-05-14 00:00:00.000000", "timezone_type": 1, "timezone": "+02:00"}
        self.assertEqual(gog._release_year(date), 2019)
        self.assertEqual(gog._release_year({"date": "1992-01-02 00:00:00.000000"}), 1992)
        self.assertEqual(gog._release_year("2001-03-04"), 2001)

    def test_missing_or_implausible_dates_give_none(self):
        for value in (None, {}, {"date": None}, {"date": ""}, "", {"date": "0000-00-00 00:00:00"}, {"date": "1900-01-01"}, 5, []):
            self.assertIsNone(gog._release_year(value), repr(value))

    def test_the_listing_gives_every_game_its_year_without_extra_requests(self):
        pages = [
            {"totalPages": 2, "products": [
                {"id": 1, "title": "With Date", "url": "/en/game/a", "releaseDate": {"date": "2019-05-14 00:00:00.000000"}},
                {"id": 2, "title": "No Date", "url": "/en/game/b"}]},
            {"totalPages": 2, "products": [{"id": 3, "title": "Old", "url": "/en/game/c", "releaseDate": {"date": "1992-01-02 00:00:00.000000"}}]},
        ]
        calls = []

        def get(url, **kwargs):
            calls.append(kwargs["params"]["page"])
            return mock.Mock(raise_for_status=lambda: None, json=lambda: pages[kwargs["params"]["page"] - 1])

        with mock.patch.object(gog, "_access_token", return_value="t"), mock.patch("gamelibrary.net.get", get):
            games = {g.title: g for g in gog.fetch_games()}
        self.assertEqual(calls, [1, 2], "one request per page, exactly as before")
        self.assertEqual([games[t].release_year for t in ("With Date", "No Date", "Old")], [2019, None, 1992])


class StoredAndGrouped(TempDatabase):
    def groups(self, conn):
        return {g["title"]: g for g in group_games(conn.execute("SELECT * FROM games").fetchall(), store.load_meta(conn))}

    def test_a_game_in_two_stores_shows_the_earliest_year(self):
        conn = store.connect()
        store.replace_platform(conn, "steam", [Game("steam", "1", "Fallout", release_year=2007), Game("steam", "2", "Portal", release_year=2007)])
        store.replace_platform(conn, "gog", [Game("gog", "9", "Fallout", release_year=1997), Game("gog", "8", "Nameless", release_year=None)])
        g = self.groups(conn)
        self.assertEqual((g["Fallout"]["release_year"], g["Fallout"]["release_year_auto"], g["Fallout"]["release_year_custom"]), (1997, 1997, False))
        self.assertEqual(g["Portal"]["release_year"], 2007)
        self.assertIsNone(g["Nameless"]["release_year"])

    def test_zero_means_asked_and_nothing_found_and_is_not_a_year(self):
        conn = store.connect()
        store.replace_platform(conn, "steam", [Game("steam", "1", "Delisted", release_year=0)])
        self.assertIsNone(self.groups(conn)["Delisted"]["release_year"])

    def test_the_year_you_type_wins_and_clearing_goes_back_to_automatic(self):
        conn = store.connect()
        store.replace_platform(conn, "gog", [Game("gog", "1", "Gothic", release_year=2001)])
        store.replace_platform(conn, "epic", [Game("epic", "2", "Only On Epic")])
        store.set_release_year(conn, "gothic", 2000)
        store.set_release_year(conn, "onlyonepic", 2018)  # no automatic year at all: the typed one is all there is
        g = self.groups(conn)
        self.assertEqual((g["Gothic"]["release_year"], g["Gothic"]["release_year_auto"], g["Gothic"]["release_year_custom"]), (2000, 2001, True))
        self.assertEqual((g["Only On Epic"]["release_year"], g["Only On Epic"]["release_year_custom"]), (2018, True))
        store.set_release_year(conn, "gothic", None)
        self.assertEqual(self.groups(conn)["Gothic"]["release_year"], 2001)

    def test_validation(self):
        conn = store.connect()
        for ok in (1950, 2015, 2100):
            store.set_release_year(conn, "k", ok)
        store.set_release_year(conn, "k", "")
        self.assertEqual(store.all_release_years(conn), {})
        for bad in (1949, 2101, 0, -2015):
            with self.assertRaises(ValueError, msg=repr(bad)):
                store.set_release_year(conn, "k", bad)
        for bad in (2015.5, "2015", True, [2015]):
            with self.assertRaises(TypeError, msg=repr(bad)):
                store.set_release_year(conn, "k", bad)

    def test_an_existing_database_gets_the_column_without_losing_anything(self):
        raw = sqlite3.connect(store.DB_PATH)
        raw.execute("CREATE TABLE games (platform TEXT NOT NULL, platform_id TEXT NOT NULL, title TEXT NOT NULL, "
                    "playtime_minutes INTEGER, last_played INTEGER, url TEXT, cover_url TEXT, developer TEXT, "
                    "source TEXT NOT NULL DEFAULT 'api', synced_at INTEGER NOT NULL, PRIMARY KEY (platform, platform_id))")
        raw.execute("INSERT INTO games (platform, platform_id, title, developer, synced_at) VALUES ('steam', '1', 'Old Row', 'Dev', 0)")
        raw.execute("CREATE TABLE game_status (key TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY (key, status))")
        raw.execute("INSERT INTO game_status VALUES ('oldrow', 'played')")
        raw.commit()
        raw.close()
        conn = store.connect()
        row = conn.execute("SELECT title, developer, release_year FROM games").fetchone()
        self.assertEqual(tuple(row), ("Old Row", "Dev", None), "the old row is intact and its year is 'never looked up'")
        self.assertEqual(store.all_status(conn), {"oldrow": ["played"]})
        store.connect()  # opening again is harmless


class SteamLookup(TempDatabase):
    OWNED = {"response": {"games": [{"appid": 10, "name": "Anniversary"}, {"appid": 20, "name": "Modern"}, {"appid": 30, "name": "Delisted"}]}}
    STORE = {10: {"original_release_date": stamp(2006), "steam_release_date": stamp(2007)}, 20: {"steam_release_date": stamp(2015)}}

    def sync(self, fail_store=False, **options):
        calls, payloads = [], []

        def get(url, **kwargs):
            calls.append(url)
            body = self.OWNED
            if "IStoreBrowseService" in url:
                if fail_store:
                    raise steam.httpx.ConnectError("down")
                request = json.loads(kwargs["params"]["input_json"])
                payloads.append(request["data_request"])
                items = [{"appid": a, "assets": {"asset_url_format": f"steam/apps/{a}/${{FILENAME}}", "library_capsule": "h/c.jpg"},
                          "basic_info": {"developers": [{"name": f"Dev {a}"}]}, "release": self.STORE[a]}
                         if a in self.STORE else {"appid": 0} for a in [i["appid"] for i in request["ids"]]]
                body = {"response": {"store_items": items}}
            return mock.Mock(raise_for_status=lambda: None, json=lambda: body)

        with mock.patch.dict(os.environ, {"STEAM_API_KEY": "k", "STEAM_ID": "1"}), mock.patch("gamelibrary.net.get", get), \
                net.session(), net.provider("steam"):
            games = steam.fetch_games(**options)
        store.replace_platform(store.connect(), "steam", games)
        return {g.title: g for g in games}, calls, payloads

    def test_the_year_comes_in_the_same_store_request_and_is_not_asked_again(self):
        games, calls, payloads = self.sync()
        self.assertEqual(len(calls), 2, "the library + ONE store batch: release data costs no extra request")
        self.assertTrue(payloads[0]["include_release"])
        self.assertEqual([games[t].release_year for t in ("Anniversary", "Modern", "Delisted")], [2006, 2015, 0])

        games, calls, _ = self.sync()
        self.assertEqual(len(calls), 1, "everything was already asked about, including the delisted game")
        self.assertEqual([games[t].release_year for t in ("Anniversary", "Modern", "Delisted")], [2006, 2015, 0])

    def test_games_synced_before_years_existed_are_asked_once_and_keep_what_they_had(self):
        conn = store.connect()
        store.replace_platform(conn, "steam", [
            Game("steam", "10", "Anniversary", cover_url="https://old/cover10.jpg", developer="Old Dev"),
            Game("steam", "20", "Modern", cover_url="https://old/cover20.jpg", developer="Old Dev 2")])  # year NULL: the old schema

        games, calls, _ = self.sync(fail_store=True)  # the lookup fails this time
        self.assertEqual(len(calls), 2)
        self.assertEqual((games["Anniversary"].developer, games["Anniversary"].cover_url), ("Old Dev", "https://old/cover10.jpg"),
                         "a failed lookup must not wipe what an earlier sync already learned")
        self.assertIsNone(games["Anniversary"].release_year)

        games, calls, _ = self.sync()  # next time it works
        self.assertEqual(len(calls), 2)
        self.assertEqual((games["Anniversary"].release_year, games["Anniversary"].developer), (2006, "Dev 10"))
        self.assertEqual(len(self.sync()[1]), 1, "and from then on it is a single request again")

    def test_steam_games_added_by_hand_are_left_alone(self):
        conn = store.connect()
        store.replace_platform(conn, "steam", [Game("steam", "09c063038c41", "Halo 2", release_year=2004)], source="manual")
        games, calls, _ = self.sync()
        self.assertEqual(len(calls), 2)
        rows = conn.execute("SELECT title, release_year FROM games WHERE source = 'manual'").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("Halo 2", 2004)])


class Endpoint(TempDatabase):
    def setUp(self):
        super().setUp()
        store.connect()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/release-year", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_saving_clearing_and_rejecting(self):
        self.assertEqual(self.post({"key": "g", "year": 2015})[0], 200)
        self.assertEqual(store.all_release_years(store.connect()), {"g": 2015})
        self.assertEqual(self.post({"key": "g", "year": None})[0], 200)
        self.assertEqual(store.all_release_years(store.connect()), {})
        self.assertEqual(self.post({"key": "g", "year": 1949}), (400, "o ano precisa estar entre 1950 e 2100"))
        self.assertEqual(self.post({"key": "g", "year": 2015.5})[0], 400)
        self.assertEqual(self.post({"key": "g", "year": "2015"})[0], 400)
        self.assertEqual(self.post({"year": 2015})[0], 400)


if __name__ == "__main__":
    unittest.main()
