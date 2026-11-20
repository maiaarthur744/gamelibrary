"""Export and import of the organization (tests/../gamelibrary/transfer.py). Temporary databases and files only.

Run from the project root:  python -m unittest discover tests -v
"""
import copy
import hashlib
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

from gamelibrary import grouping, server, store, transfer  # noqa: E402
from gamelibrary.grouping import group_games  # noqa: E402
from gamelibrary.models import Game  # noqa: E402
from gamelibrary.providers import manual  # noqa: E402


class World(unittest.TestCase):
    """A private data folder: database, manual.json, aliases.json and backups all live in a temp dir."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gamelibrary-world-"))
        for target, name, value in ((store, "DB_PATH", self.dir / "library.db"), (store, "DATA_DIR", self.dir),
                                    (manual, "MANUAL_PATH", self.dir / "manual.json"),
                                    (grouping, "ALIASES_PATH", self.dir / "aliases.json")):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.conn = store.connect()

    # -- helpers
    def library(self, conn=None):
        """The games the stores would return."""
        conn = conn or self.conn
        store.replace_platform(conn, "steam", [Game("steam", "1", "Portal 2", developer="Valve", release_year=2011),
                                               Game("steam", "2", "Hades"), Game("steam", "3", "Hollow Knight")])
        store.replace_platform(conn, "gog", [Game("gog", "9", "Fallout", release_year=1997), Game("gog", "8", "Gothic")])
        store.replace_platform(conn, "epic", [Game("epic", "e1", "Alan Wake", developer="Remedy")])

    def organize(self, conn=None):
        conn = conn or self.conn
        store.set_status(conn, "portal2", ["played", "multiplayer"])
        store.set_tags(conn, "portal2", ["puzzle", "Coop"])
        store.set_rating(conn, "portal2", 9)
        store.set_note(conn, "portal2", "Best puzzle game")
        store.set_developer(conn, "gothic", "Piranha Bytes")
        store.set_release_year(conn, "gothic", 2001)
        store.set_playtime(conn, "gothic", 42.5)
        store.set_dates(conn, "hades", "2026-01-10", "2026-02-01")
        store.set_cover(conn, "hades", "https://example.com/hades.jpg")
        store.set_hidden(conn, "hollowknight", "Hollow Knight", True)
        store.set_status(conn, "fallout", ["backlog"])
        store.set_status(conn, "alanwake", ["backlog", "unplayed"])
        store.set_tags(conn, "alanwake", ["horror"])

    def meta_snapshot(self, conn=None):
        conn = conn or self.conn
        meta = store.load_meta(conn)
        return {"status": meta.status, "tags": meta.tags, "ratings": meta.ratings, "notes": meta.notes,
                "developers": meta.developers, "years": meta.release_years, "playtimes": meta.playtimes,
                "dates": meta.dates, "covers": meta.covers, "hidden": sorted(meta.hidden), "splits": sorted(meta.splits)}

    def files_snapshot(self):
        return {p.name: hashlib.sha1(p.read_bytes()).hexdigest() for p in (self.dir / "manual.json", self.dir / "aliases.json") if p.exists()}

    def db_digest(self):
        raw = sqlite3.connect(self.dir / "library.db")
        try:
            return {t: hashlib.sha1(repr(sorted(map(tuple, raw.execute(f"SELECT * FROM {t}")))).encode()).hexdigest()
                    for (t,) in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        finally:
            raw.close()

    def export(self, **kwargs):
        return transfer.export_data(self.conn, **kwargs)

    def group_titles(self, data):
        return sorted(g["title"] for g in data["groups"])


class Export(World):
    def setUp(self):
        super().setUp()
        self.library()
        self.organize()

    def test_exports_only_what_you_typed_never_what_the_stores_said(self):
        data = self.export()
        by_title = {g["title"]: g for g in data["groups"]}
        self.assertNotIn("developer", by_title["Portal 2"], "Valve came from Steam: it is automatic, not yours")
        self.assertNotIn("release_year", by_title["Portal 2"])
        self.assertEqual(by_title["Gothic"]["developer"], "Piranha Bytes")
        self.assertEqual(by_title["Gothic"]["release_year"], 2001)
        self.assertEqual(by_title["Gothic"]["playtime_minutes"], 2550)
        portal = by_title["Portal 2"]
        self.assertEqual((portal["statuses"], portal["tags"], portal["rating"], portal["note"]),
                         (["played", "multiplayer"], ["Coop", "puzzle"], 9, "Best puzzle game"))
        self.assertEqual((by_title["Hades"]["started"], by_title["Hades"]["finished"], by_title["Hades"]["cover"]),
                         ("2026-01-10", "2026-02-01", "https://example.com/hades.jpg"))
        self.assertTrue(by_title["Hollow Knight"]["hidden"])

    def test_the_games_the_stores_return_are_not_in_the_file(self):
        data = self.export()
        self.assertEqual(data["manual_games"], [], "nothing was added by hand, and store games are never listed as such")
        self.assertTrue(all(set(g) <= {"key", "title", "licenses", "statuses", "tags", "rating", "note", "developer", "release_year",
                                       "playtime_minutes", "started", "finished", "cover", "hidden"} for g in data["groups"]))

    def test_the_file_is_plain_json_and_never_carries_secrets(self):
        (self.dir / "gog_token.json").write_text('{"access_token": "SECRET-TOKEN"}', encoding="utf-8")
        (self.dir / ".env").write_text("STEAM_API_KEY=SECRET-KEY", encoding="utf-8")
        text = json.dumps(self.export())
        self.assertNotIn("SECRET", text)
        self.assertEqual(json.loads(text)["format"], "gamelibrary-export")

    def test_filter_by_store(self):
        self.assertEqual(self.group_titles(self.export(platforms=["steam"])), ["Hades", "Hollow Knight", "Portal 2"])
        self.assertEqual(self.group_titles(self.export(platforms=["gog", "epic"])), ["Alan Wake", "Fallout", "Gothic"])

    def test_filter_by_status_and_tag(self):
        self.assertEqual(self.group_titles(self.export(statuses=["backlog"])), ["Alan Wake", "Fallout"])
        self.assertEqual(self.group_titles(self.export(statuses=["backlog", "played"])), ["Alan Wake", "Fallout", "Portal 2"])
        self.assertEqual(self.group_titles(self.export(tags=["COOP"])), ["Portal 2"], "tags match ignoring case")

    def test_filters_combine_with_and_between_kinds(self):
        self.assertEqual(self.group_titles(self.export(platforms=["epic"], statuses=["backlog"])), ["Alan Wake"])
        self.assertEqual(self.group_titles(self.export(platforms=["steam"], statuses=["backlog"])), [])

    def test_explicit_keys_are_what_the_screen_filter_uses(self):
        self.assertEqual(self.group_titles(self.export(keys=["gothic", "hades", "does-not-exist"])), ["Gothic", "Hades"])
        self.assertEqual(self.export(keys=[])["groups"], [])

    def test_games_with_nothing_set_are_not_listed(self):
        store.replace_platform(self.conn, "steam", [Game("steam", "7", "Untouched")])
        self.assertNotIn("Untouched", self.group_titles(self.export()))

    def test_organization_of_a_game_you_do_not_have_yet_is_still_exported(self):
        store.set_status(self.conn, "notownedyet", ["backlog"])
        data = self.export()
        self.assertIn("notownedyet", [g["key"] for g in data["groups"]])
        self.assertNotIn("notownedyet", [g["key"] for g in self.export(platforms=["steam"])["groups"]])

    def test_manual_games_and_splits_follow_their_games(self):
        manual.save_entries([{"platform": "battlenet", "platform_id": "b1", "title": "Diablo IV", "cover_url": "https://x/d.jpg"},
                             {"platform": "steam", "platform_id": "m1", "title": "Halo 2", "playtime_minutes": 90}])
        manual.sync(self.conn)
        store.set_status(self.conn, "halo2", ["played"])
        store.set_split(self.conn, store.entry_key("steam", "2"), True)
        all_data = self.export()
        self.assertEqual(sorted(m["title"] for m in all_data["manual_games"]), ["Diablo IV", "Halo 2"])
        self.assertIn("steam:2", all_data["splits"])
        only_steam = self.export(platforms=["steam"])
        self.assertEqual([m["title"] for m in only_steam["manual_games"]], ["Halo 2"], "Diablo IV is not on Steam")
        self.assertEqual(only_steam["manual_games"][0]["playtime_minutes"], 90)

    def test_uploaded_cover_files_are_left_out_and_counted(self):
        store.set_cover(self.conn, "gothic", "/covers/" + "a" * 40 + ".png")
        data = self.export()
        self.assertEqual(data["summary"]["uploaded_covers_skipped"], 1)
        self.assertNotIn("cover", {g["title"]: g for g in data["groups"]}["Gothic"])

    def test_aliases_are_included_by_default_and_can_be_left_out(self):
        (self.dir / "aliases.json").write_text('[{"A": "B"}, {"C": ["D", "E"]}]', encoding="utf-8")
        self.assertEqual(self.export()["aliases"], [["A", "B"], ["C", "D"], ["C", "E"]])
        self.assertEqual(self.export(include_aliases=False)["aliases"], [])

    def test_a_broken_aliases_file_does_not_break_the_export(self):
        (self.dir / "aliases.json").write_text("{not json", encoding="utf-8")
        data = self.export()
        self.assertEqual(data["aliases"], [])
        self.assertTrue(data["summary"]["warnings"])

    def test_exporting_changes_nothing(self):
        before, files = self.db_digest(), self.files_snapshot()
        self.export()
        self.assertEqual((self.db_digest(), self.files_snapshot()), (before, files))


class Import(World):
    """Import into a database that has the same games but none of the organization (a new computer)."""

    def setUp(self):
        super().setUp()
        self.library()
        self.organize()
        self.data = self.export()
        # wipe the organization: this is "the other computer"
        for table in ("game_status", "tags", "notes", "ratings", "hidden", "dates", "covers", "developers", "release_years",
                      "playtimes"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def run_import(self, mode="fill", data=None):
        return transfer.apply_import(self.conn, data or self.data, mode)

    def test_a_round_trip_restores_everything(self):
        expected = {"status": {"portal2": ["played", "multiplayer"], "fallout": ["backlog"], "alanwake": ["backlog", "unplayed"]},
                    "ratings": {"portal2": 9}, "hidden": ["hollowknight"], "developers": {"gothic": "Piranha Bytes"},
                    "years": {"gothic": 2001}, "playtimes": {"gothic": 2550},
                    "dates": {"hades": ("2026-01-10", "2026-02-01")}, "covers": {"hades": "https://example.com/hades.jpg"},
                    "notes": {"portal2": "Best puzzle game"}}
        report = self.run_import()
        snap = self.meta_snapshot()
        for field, value in expected.items():
            self.assertEqual(snap[field], value, field)
        self.assertEqual(snap["tags"], {"portal2": ["Coop", "puzzle"], "alanwake": ["horror"]})
        self.assertEqual(report["groups"]["found_here"], len(self.data["groups"]))
        self.assertEqual(report["fields"]["kept_local"], 0)
        self.assertTrue(Path(report["backup"]).is_dir())

    def test_importing_the_same_file_again_changes_nothing(self):
        self.run_import()
        before = self.db_digest()
        report = self.run_import()
        self.assertEqual(self.db_digest(), before)
        self.assertEqual((report["fields"]["written"], report["fields"]["kept_local"], report["fields"]["overwritten"]), (0, 0, 0))
        self.assertGreater(report["fields"]["unchanged"], 0)
        self.assertEqual(report["groups"]["touched"], 0)

    def test_fill_mode_never_overwrites_what_you_already_have(self):
        store.set_rating(self.conn, "portal2", 4)
        store.set_note(self.conn, "portal2", "my own note")
        store.set_status(self.conn, "portal2", ["dropped"])
        report = self.run_import("fill")
        snap = self.meta_snapshot()
        self.assertEqual((snap["ratings"]["portal2"], snap["notes"]["portal2"], snap["status"]["portal2"]), (4, "my own note", ["dropped"]))
        self.assertEqual(snap["tags"]["portal2"], ["Coop", "puzzle"], "...but the gaps are filled")
        self.assertEqual(report["fields"]["kept_local"], 3)
        fields = {c["field"] for c in report["conflicts"]}
        self.assertEqual(fields, {"rating", "note", "statuses"})
        self.assertTrue(all(c["resolution"] == "kept_local" for c in report["conflicts"]))

    def test_overwrite_mode_replaces_with_the_file_but_never_deletes(self):
        store.set_rating(self.conn, "portal2", 4)
        store.set_status(self.conn, "portal2", ["dropped"])
        store.set_tags(self.conn, "hades", ["roguelike"])  # the file has nothing for Hades' tags
        report = self.run_import("overwrite")
        snap = self.meta_snapshot()
        self.assertEqual((snap["ratings"]["portal2"], snap["status"]["portal2"]), (9, ["played", "multiplayer"]))
        self.assertEqual(snap["tags"]["hades"], ["roguelike"], "something the file does not mention is left alone")
        self.assertEqual(report["fields"]["overwritten"], 2)

    def test_merge_mode_unites_statuses_and_tags(self):
        store.set_status(self.conn, "portal2", ["backlog"])
        store.set_tags(self.conn, "portal2", ["favorite", "coop"])
        store.set_rating(self.conn, "portal2", 4)
        report = self.run_import("merge")
        snap = self.meta_snapshot()
        self.assertEqual(snap["status"]["portal2"], ["played", "backlog", "multiplayer"])
        self.assertEqual(sorted(t.lower() for t in snap["tags"]["portal2"]), ["coop", "favorite", "puzzle"], "'coop' and 'Coop' are one tag")
        self.assertEqual(snap["ratings"]["portal2"], 4, "single values are only filled in, not overwritten")
        self.assertEqual(report["fields"]["merged"], 2)

    def test_hiding_is_only_ever_added(self):
        store.set_hidden(self.conn, "hades", "Hades", True)  # hidden here, not in the file
        for mode in ("fill", "merge", "overwrite"):
            self.run_import(mode)
        self.assertEqual(self.meta_snapshot()["hidden"], ["hades", "hollowknight"])

    def test_a_key_that_changed_is_found_again_through_its_licenses(self):
        data = copy.deepcopy(self.data)
        for g in data["groups"]:
            if g["title"] == "Portal 2":
                g["key"] = "portal2-old-key"  # the other computer used different grouping rules
        report = self.run_import(data=data)
        self.assertEqual(report["groups"]["matched_by_license"], 1)
        self.assertEqual(self.meta_snapshot()["ratings"].get("portal2"), 9)
        self.assertNotIn("portal2-old-key", self.meta_snapshot()["ratings"])

    def test_organization_for_a_game_not_synced_yet_waits_for_it(self):
        store.set_status(self.conn, "newgame", ["backlog"])
        store.set_rating(self.conn, "newgame", 7)
        data = self.export(keys=["newgame"])
        for table in ("game_status", "ratings"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        report = self.run_import(data=data)
        self.assertEqual(report["groups"]["waiting_for_the_game"], 1)
        store.replace_platform(self.conn, "steam", [Game("steam", "77", "New Game")])  # the sync brings it later
        group = {g["key"]: g for g in group_games(self.conn.execute("SELECT * FROM games").fetchall(), store.load_meta(self.conn))}["newgame"]
        self.assertEqual((group["statuses"], group["rating"]), (["backlog"], 7))

    def test_manual_games_are_added_once_and_dedupe_like_adding_by_hand(self):
        manual.save_entries([{"platform": "steam", "platform_id": "m1", "title": "Halo 2"}])
        manual.sync(self.conn)
        data = copy.deepcopy(self.data)
        data["manual_games"] = [
            {"platform": "steam", "platform_id": "m1", "title": "Halo 2"},                                       # same id
            {"platform": "Steam", "platform_id": "other", "title": "halo 2"},                                    # same title
            {"platform": "battlenet", "platform_id": "b1", "title": "Diablo IV", "cover_url": "https://x/c.jpg", "playtime_minutes": 600},
            {"platform": "Battle.net", "title": "Overwatch 2"},                                                  # alias + no id
            {"platform": "steam", "platform_id": "1", "title": "Collides With A Steam App Id"},                  # id of a real game
            {"title": "No platform"}, {"platform": "x", "title": ""}, "not a dict",                              # junk
            {"platform": "itch", "title": "Bad Cover", "cover_url": "javascript:alert(1)"},
        ]
        report = self.run_import(data=data)
        self.assertEqual(report["manual_games"], {"added": 4, "already_there": 2, "invalid": 3})
        entries = {e["title"]: e for e in manual.load_entries()}
        self.assertEqual(sorted(entries), ["Bad Cover", "Collides With A Steam App Id", "Diablo IV", "Halo 2", "Overwatch 2"])
        self.assertEqual(entries["Diablo IV"]["playtime_minutes"], 600)
        self.assertNotIn("cover_url", entries["Bad Cover"], "a javascript: link is dropped")
        self.assertEqual(entries["Overwatch 2"]["platform"], "battlenet")
        self.assertNotEqual(entries["Collides With A Steam App Id"]["platform_id"], "1", "must never collide with a real id")
        rows = {r["title"]: r["source"] for r in self.conn.execute("SELECT title, source FROM games WHERE source = 'manual'")}
        self.assertEqual(set(rows), set(entries), "the database rows match manual.json")

    def test_entries_without_an_id_keep_the_identity_they_had(self):
        """Older manual entries have no platform_id: their identity is the title, on every computer."""
        manual.save_entries([{"platform": "battlenet", "title": "Starcraft 2"}, {"platform": "steam", "platform_id": "m9", "title": "Halo 3"}])
        manual.sync(self.conn)
        store.set_split(self.conn, store.entry_key("battlenet", "Starcraft 2"), True)
        store.set_status(self.conn, "=starcraft2", ["played"])
        data = self.export()
        # the other computer starts empty
        manual.save_entries([])
        manual.sync(self.conn)
        for table in ("entry_splits", "game_status"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        report = transfer.apply_import(self.conn, data, "fill")
        self.assertEqual(report["manual_games"]["added"], 2)
        entries = {e["title"]: e for e in manual.load_entries()}
        self.assertEqual(entries["Starcraft 2"], {"platform": "battlenet", "title": "Starcraft 2"}, "written back exactly as it was")
        self.assertEqual(entries["Halo 3"]["platform_id"], "m9")
        self.assertEqual(self.conn.execute("SELECT platform_id FROM games WHERE title = 'Starcraft 2'").fetchone()[0], "Starcraft 2")
        self.assertIn("battlenet:Starcraft 2", self.meta_snapshot()["splits"], "so splits that point at it still match")
        self.assertEqual(self.meta_snapshot()["status"].get("=starcraft2"), ["played"])
        again = transfer.apply_import(self.conn, data, "fill")
        self.assertEqual(again["manual_games"], {"added": 0, "already_there": 2, "invalid": 0}, "importing twice never duplicates it")

    def test_splits_and_aliases_are_added_without_touching_existing_ones(self):
        (self.dir / "aliases.json").write_text('[{"Old A": "Old B"}]', encoding="utf-8")
        data = copy.deepcopy(self.data)
        data["splits"] = ["steam:2", "bogus", 5]
        data["aliases"] = [["Old A", "Different Target"], ["New A", "New B"], ["Old A", "Old B"], ["Hub", "Child 1"], ["Hub", "Child 2"], "junk", ["only one"]]
        report = self.run_import(data=data)
        self.assertEqual(report["splits"], {"added": 1, "already_there": 0})
        self.assertIn("steam:2", self.meta_snapshot()["splits"])
        self.assertEqual(report["aliases"], {"added": 3, "already_there": 0, "conflicting": 2})
        pairs = [(k, v) for o in json.loads((self.dir / "aliases.json").read_text(encoding="utf-8")) for k, v in o.items()]
        self.assertEqual(pairs, [("Old A", "Old B"), ("New A", "New B"), ("Hub", "Child 1"), ("Hub", "Child 2")])
        self.assertIn("aliases", {c["field"] for c in report["conflicts"]})

    def test_a_broken_local_aliases_file_is_not_overwritten(self):
        (self.dir / "aliases.json").write_text("{not json", encoding="utf-8")
        data = copy.deepcopy(self.data)
        data["aliases"] = [["New A", "New B"]]
        report = self.run_import(data=data)
        self.assertEqual((self.dir / "aliases.json").read_text(encoding="utf-8"), "{not json")
        self.assertTrue(any("aliases.json" in w for w in report["warnings"]))

    def test_aliases_that_arrive_change_how_the_imported_games_group(self):
        store.replace_platform(self.conn, "epic", [Game("epic", "e1", "Alan Wake"), Game("epic", "e2", "Alan Wake Remastered")])
        data = copy.deepcopy(self.data)
        data["aliases"] = [["Alan Wake", "Alan Wake Remastered"]]
        self.run_import(data=data)
        keys = {g["key"] for g in group_games(self.conn.execute("SELECT * FROM games").fetchall(), store.load_meta(self.conn))}
        self.assertNotIn("alanwake", keys)
        self.assertIn("alanwakeremastered", keys)


class Validation(World):
    def apply(self, data, mode="fill"):
        return transfer.apply_import(self.conn, data, mode)

    def base(self, **groups):
        return {"format": "gamelibrary-export", "version": 1, "groups": [dict(key="portal2", title="Portal 2", **groups)]}

    def test_a_file_that_is_not_an_export_is_refused_before_anything_happens(self):
        before = self.db_digest()
        for bad in ({}, [], "text", {"format": "other", "version": 1}, {"format": "gamelibrary-export"},
                    {"format": "gamelibrary-export", "version": 2}, {"format": "gamelibrary-export", "version": "1"},
                    {"format": "gamelibrary-export", "version": 1, "groups": {}}, {"format": "gamelibrary-export", "version": 1, "manual_games": "x"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.apply(bad)
        self.assertEqual(self.db_digest(), before)
        self.assertFalse((self.dir / "backups").exists(), "a refused file must not even create a backup")

    def test_the_version_message_tells_the_user_what_to_do(self):
        with self.assertRaises(ValueError) as cm:
            self.apply({"format": "gamelibrary-export", "version": 2})
        self.assertIn("Atualize o programa", str(cm.exception))

    def test_bad_values_are_skipped_one_by_one_and_reported(self):
        data = self.base(rating=11, note=5, statuses=["played", "bogus"], tags="not a list", developer=["x"], release_year=1900,
                         playtime_minutes=-3, started="yesterday", cover="javascript:alert(1)", hidden="yes")
        data["groups"].append({"key": "ok", "title": "Fine", "rating": 8, "tags": ["good"]})
        data["groups"] += [{"title": "no key"}, "not a dict", {"key": ""}, {"key": "x" * 400}]
        report = self.apply(data)
        snap = self.meta_snapshot()
        self.assertEqual((snap["ratings"], snap["tags"]), ({"ok": 8}, {"ok": ["good"]}), "only the valid group got through")
        self.assertEqual(snap["status"], {})
        self.assertEqual(snap["hidden"], [])
        self.assertEqual(report["fields"]["skipped_invalid"], 9)
        self.assertGreaterEqual(len(report["warnings"]), 9)

    def test_an_uploaded_cover_path_is_rejected_because_the_file_would_not_exist_here(self):
        report = self.apply(self.base(cover="/covers/" + "a" * 40 + ".png"))
        self.assertEqual((self.meta_snapshot()["covers"], report["fields"]["skipped_invalid"]), ({}, 1))

    def test_oversized_values_are_cut_to_the_same_limits_as_the_page(self):
        self.apply(self.base(note="n" * 30000, developer="d" * 500, tags=[f"tag{i}" for i in range(50)] + ["x" * 100]))
        snap = self.meta_snapshot()
        self.assertEqual((len(snap["notes"]["portal2"]), len(snap["developers"]["portal2"])), (20000, 200))
        self.assertEqual(len(snap["tags"]["portal2"]), 30)
        self.assertTrue(all(len(t) <= 40 for t in snap["tags"]["portal2"]))

    def test_dates_that_would_end_before_they_start_are_not_written(self):
        store.set_dates(self.conn, "portal2", "2026-06-01", None)
        report = self.apply(self.base(finished="2026-01-01"))
        self.assertEqual(self.meta_snapshot()["dates"]["portal2"], ("2026-06-01", None))
        self.assertTrue(any("datas" in w for w in report["warnings"]))

    def test_every_check_agrees_with_what_the_page_accepts(self):
        """transfer.check_* must accept and reject exactly what store.set_* does, and normalize the same way."""
        conn = self.conn
        cases = {
            "rating": ([1, 5, 10], [0, 11, 4.5, True, "5", None], transfer.check_rating, store.set_rating, store.all_ratings),
            "year": ([1950, 2015, 2100], [1949, 2101, 2015.5, True, "2015"], transfer.check_year, store.set_release_year, store.all_release_years),
            "note": (["  hi  ", "x" * 25000, "line\nbreak"], [5, None, ["a"]], transfer.check_note, store.set_note, store.all_notes),
            "developer": (["  A   B ", "d" * 300, "Single"], [5, None, ["a"]], transfer.check_developer, store.set_developer, store.all_developers),
        }
        for name, (good, bad, check, setter, getter) in cases.items():
            for value in good:
                normalized = check(value)
                setter(conn, "k", value)
                self.assertEqual(getter(conn).get("k"), normalized or None, f"{name}: {value!r}")
            for value in bad:
                if name in ("rating", "year") and value is None:
                    continue  # None means "clear" on the page and "nothing" in a file
                with self.assertRaises((ValueError, TypeError), msg=f"{name} check accepted {value!r}"):
                    check(value)
                if value is not None:
                    with self.assertRaises((ValueError, TypeError), msg=f"{name} setter accepted {value!r}"):
                        setter(conn, "k", value)
        # statuses, tags, minutes, dates
        for value in (["played", "multiplayer"], ["multiplayer", "played", "played"], []):
            store.set_status(conn, "s", value)
            self.assertEqual(store.all_status(conn).get("s", []), transfer.check_statuses(value))
        for value in (["bogus"], ["played", "bogus"], "played", None):
            with self.assertRaises((ValueError, TypeError)):
                transfer.check_statuses(value)
            with self.assertRaises((ValueError, TypeError)):
                store.set_status(conn, "s", value)
        self.assertEqual(transfer.check_tags([" b ", "A", "a", "", "x" * 60]), ["A", "b", "x" * 40])
        self.assertEqual(transfer.check_minutes(2550), 2550)
        for bad in (-1, 6000001, True, "9", None):
            with self.assertRaises((ValueError, TypeError)):
                transfer.check_minutes(bad)
        self.assertEqual(transfer.check_date("2026-02-03"), "2026-02-03")
        for bad in ("2026-13-01", "yesterday", 20260203, None):
            with self.assertRaises((ValueError, TypeError)):
                transfer.check_date(bad)
        for good in ("http://x/y.jpg", "https://x/y.jpg"):
            self.assertEqual(transfer.check_cover(good), good)
        for bad in ("javascript:alert(1)", "/covers/abc.png", "ftp://x", "x" * 2100, None, 5):
            with self.assertRaises((ValueError, TypeError)):
                transfer.check_cover(bad)


class PreviewAndSafety(World):
    def setUp(self):
        super().setUp()
        self.library()
        self.organize()
        manual.save_entries([{"platform": "steam", "platform_id": "m1", "title": "Halo 2"}])
        manual.sync(self.conn)
        (self.dir / "aliases.json").write_text('[{"A": "B"}]', encoding="utf-8")
        self.data = self.export()
        self.data["manual_games"].append({"platform": "itch", "platform_id": "i1", "title": "Celeste"})
        self.data["aliases"].append(["C", "D"])
        self.data["splits"].append("gog:9")
        for table in ("game_status", "tags", "ratings", "notes"):
            self.conn.execute(f"DELETE FROM {table}")
        store.set_rating(self.conn, "portal2", 3)
        self.conn.commit()

    def test_the_preview_reports_exactly_what_applying_does_and_touches_nothing(self):
        before, files = self.db_digest(), self.files_snapshot()
        previews = {mode: transfer.preview_import(self.conn, self.data, mode) for mode in transfer.MODES}
        self.assertEqual((self.db_digest(), self.files_snapshot()), (before, files), "a preview must write nothing")
        self.assertFalse((self.dir / "backups").exists())
        for mode, preview in previews.items():
            restore = self.dir / f"restore-{mode}"
            restore.mkdir()
            for name in ("library.db", "manual.json", "aliases.json"):
                if (self.dir / name).exists():
                    (restore / name).write_bytes((self.dir / name).read_bytes())
            applied = transfer.apply_import(self.conn, self.data, mode)
            applied.pop("backup")
            self.assertEqual(preview, applied, f"mode {mode}: the preview must match the real result")
            # put things back for the next mode
            self.conn.close()
            for name in ("library.db", "manual.json", "aliases.json"):
                if (restore / name).exists():
                    (self.dir / name).write_bytes((restore / name).read_bytes())
            self.conn = store.connect()

    def test_a_failure_halfway_undoes_everything(self):
        before, files, organization = self.db_digest(), self.files_snapshot(), self.meta_snapshot()
        calls = []
        real = transfer._apply_group

        def explode(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError("disk on fire")
            return real(*args, **kwargs)

        with mock.patch.object(transfer, "_apply_group", explode), self.assertRaises(RuntimeError):
            transfer.apply_import(self.conn, self.data, "overwrite")
        self.assertEqual(self.db_digest(), before, "the database must be exactly as it was")
        self.assertEqual(self.meta_snapshot(), organization, "...also as seen by the connection that ran the import")
        self.assertFalse(self.conn.in_transaction, "no transaction may be left open")
        self.assertEqual(self.files_snapshot(), files, "and so must manual.json and aliases.json")
        self.assertEqual(len(list((self.dir / "backups").iterdir())), 1, "the backup was made before starting")

    def test_a_failure_writing_the_files_also_undoes_the_database(self):
        before = self.db_digest()
        organization = self.meta_snapshot()
        with mock.patch.object(manual, "save_entries", side_effect=OSError("disk full")), self.assertRaises(OSError):
            transfer.apply_import(self.conn, self.data, "fill")
        self.assertEqual(self.db_digest(), before)
        self.assertEqual(self.meta_snapshot(), organization)
        self.assertFalse(self.conn.in_transaction)

    def test_the_backup_has_the_database_and_both_files_as_they_were(self):
        before = {"manual.json": (self.dir / "manual.json").read_bytes(), "aliases.json": (self.dir / "aliases.json").read_bytes()}
        rating_before = store.all_ratings(self.conn)
        report = transfer.apply_import(self.conn, self.data, "overwrite")
        folder = Path(report["backup"])
        self.assertEqual({p.name for p in folder.iterdir()}, {"library.db", "manual.json", "aliases.json"})
        for name, content in before.items():
            self.assertEqual((folder / name).read_bytes(), content)
        backup = sqlite3.connect(folder / "library.db")
        self.assertEqual(dict(backup.execute("SELECT key, rating FROM ratings").fetchall()), rating_before)
        backup.close()
        self.assertNotEqual(store.all_ratings(self.conn), rating_before, "while the live database did change")

    def test_each_import_gets_its_own_backup_folder(self):
        folders = {transfer.apply_import(self.conn, self.data, "fill")["backup"] for _ in range(3)}
        self.assertEqual(len(folders), 3)

    def test_unchanged_files_are_not_rewritten(self):
        transfer.apply_import(self.conn, self.data, "fill")  # adds the manual game and aliases
        before = {p: p.stat().st_mtime_ns for p in (self.dir / "manual.json", self.dir / "aliases.json")}
        transfer.apply_import(self.conn, self.data, "fill")  # nothing new for either file
        self.assertEqual({p: p.stat().st_mtime_ns for p in before}, before)

    def test_two_imports_at_once_are_serialized(self):
        order = []
        real = transfer._run_import

        def slow(*args, **kwargs):
            order.append("start")
            result = real(*args, **kwargs)
            order.append("end")
            return result

        with mock.patch.object(transfer, "_run_import", slow):
            threads = [threading.Thread(target=lambda: transfer.apply_import(store.connect(), self.data, "fill")) for _ in range(3)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual(order, ["start", "end"] * 3, "never interleaved")

    def test_no_network_and_no_processes_are_ever_used(self):
        boom = AssertionError("outside request")
        with mock.patch("httpx.get", side_effect=boom), mock.patch("httpx.Client.send", side_effect=boom), \
                mock.patch("requests.sessions.Session.request", side_effect=boom), mock.patch("subprocess.Popen", side_effect=boom):
            transfer.export_data(self.conn)
            transfer.preview_import(self.conn, self.data, "merge")
            transfer.apply_import(self.conn, self.data, "overwrite")


class Endpoints(World):
    def setUp(self):
        super().setUp()
        self.library()
        self.organize()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def post(self, path, body, content_type="application/json"):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_export_preview_apply_over_http(self):
        status, export = self.post("/api/export", {"platforms": ["steam"]})
        self.assertEqual(status, 200)
        self.assertEqual(sorted(g["title"] for g in export["groups"]), ["Hades", "Hollow Knight", "Portal 2"])
        self.conn.execute("DELETE FROM ratings")
        self.conn.commit()
        status, preview = self.post("/api/import/preview", {"data": export, "mode": "fill"})
        self.assertEqual((status, preview["fields"]["written"] > 0, store.all_ratings(self.conn)), (200, True, {}), "a preview writes nothing")
        status, applied = self.post("/api/import/apply", {"data": export, "mode": "fill"})
        self.assertEqual(status, 200)
        self.assertEqual(store.all_ratings(store.connect()), {"portal2": 9})
        self.assertTrue(Path(applied["backup"]).is_dir())

    def test_the_screen_filter_sends_explicit_keys(self):
        status, export = self.post("/api/export", {"keys": ["gothic"], "include_aliases": False})
        self.assertEqual((status, [g["title"] for g in export["groups"]], export["aliases"]), (200, ["Gothic"], []))

    def test_errors_reach_the_page_as_readable_messages(self):
        self.assertEqual(self.post("/api/import/preview", {"data": {"format": "nope"}, "mode": "fill"}),
                         (400, "Este arquivo não é uma exportação do Game Library."))
        status, text = self.post("/api/import/apply", {"data": {"format": "gamelibrary-export", "version": 1}, "mode": "wild"})
        self.assertEqual((status, text), (400, "modo inválido: 'wild'"))
        self.assertEqual(self.post("/api/import/apply", {"mode": "fill"})[0], 400, "no data at all")
        self.assertEqual(self.post("/api/export", {"platforms": "steam"})[0], 400)
        self.assertEqual(self.post("/api/export", {"keys": [1, 2]})[0], 400)

    def test_only_json_requests_are_accepted_like_every_other_write(self):
        self.assertEqual(self.post("/api/export", {}, content_type="text/plain")[0], 400)


if __name__ == "__main__":
    unittest.main()
