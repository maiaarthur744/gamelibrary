"""Hand-added games next to a store's synced games: neither sync may erase the other. Temporary databases only.

Run from the project root:  python -m unittest discover tests -v
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("GAMELIBRARY_DATA_DIR", tempfile.mkdtemp(prefix="gamelibrary-test-"))


from gamelibrary import store
from gamelibrary.grouping import group_games
from gamelibrary.models import Game
from gamelibrary.providers import manual


class Workspace(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gamelibrary-ws-"))
        for target, path in ((store, "DB_PATH"), (manual, "MANUAL_PATH")):
            patcher = mock.patch.object(target, path, self.dir / ("library.db" if path == "DB_PATH" else "manual.json"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.conn = store.connect()

    def rows(self, platform=None, source=None):
        sql, params = "SELECT platform, platform_id, title, source FROM games WHERE 1=1", []
        for column, value in (("platform", platform), ("source", source)):
            if value:
                sql += f" AND {column} = ?"
                params.append(value)
        return sorted(tuple(r) for r in self.conn.execute(sql + " ORDER BY platform, platform_id", params))

    def api(self, platform, *titles):
        store.replace_platform(self.conn, platform, [Game(platform, f"{platform}-{i}", t) for i, t in enumerate(titles)])


class SourceMigration(unittest.TestCase):
    def test_existing_rows_are_classified_and_nothing_else_is_touched(self):
        db = Path(tempfile.mkdtemp()) / "library.db"
        raw = sqlite3.connect(db)
        # the shape of the database before the source column existed, with some user organization in it
        raw.execute("CREATE TABLE games (platform TEXT NOT NULL, platform_id TEXT NOT NULL, title TEXT NOT NULL, "
                    "playtime_minutes INTEGER, last_played INTEGER, url TEXT, cover_url TEXT, developer TEXT, "
                    "synced_at INTEGER NOT NULL, PRIMARY KEY (platform, platform_id))")
        raw.executemany("INSERT INTO games (platform, platform_id, title, synced_at) VALUES (?, ?, ?, 0)",
                        [("steam", "1", "A"), ("gog", "2", "B"), ("epic", "3", "C"),
                         ("battlenet", "4", "D"), ("amazon", "5", "E"), ("ps3", "6", "F")])
        raw.execute("CREATE TABLE game_status (key TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY (key, status))")
        raw.execute("INSERT INTO game_status VALUES ('a', 'played')")
        raw.execute("CREATE TABLE tags (key TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (key, tag))")
        raw.execute("INSERT INTO tags VALUES ('a', 'favorito')")
        raw.commit()
        raw.close()

        with mock.patch.object(store, "DB_PATH", db):
            conn = store.connect()
            sources = dict(conn.execute("SELECT platform, source FROM games").fetchall())
            self.assertEqual(sources, {"steam": "api", "gog": "api", "epic": "api",
                                       "battlenet": "manual", "amazon": "manual", "ps3": "manual"})
            self.assertEqual(conn.execute("SELECT status FROM game_status").fetchall()[0][0], "played")
            self.assertEqual(conn.execute("SELECT tag FROM tags").fetchall()[0][0], "favorito")
            self.assertEqual(conn.execute("SELECT count(*) FROM games").fetchone()[0], 6)

            conn.execute("UPDATE games SET source = 'manual' WHERE platform = 'steam'")  # a Steam game added by hand
            conn.commit()
            self.assertEqual(store.connect().execute("SELECT source FROM games WHERE platform = 'steam'").fetchone()[0],
                             "manual", "opening the database again must not reclassify anything")

    def test_a_fresh_database_has_the_column(self):
        with mock.patch.object(store, "DB_PATH", Path(tempfile.mkdtemp()) / "x.db"):
            cols = {r["name"] for r in store.connect().execute("PRAGMA table_info(games)")}
        self.assertIn("source", cols)


class IndependentSyncs(Workspace):
    def test_steam_gog_and_epic_accept_manual_games(self):
        for platform in ("Steam", "GOG", "Epic"):
            manual.add_entry(platform, f"Mine on {platform}")
        manual.sync(self.conn)
        self.assertEqual({(p, s) for p, _, _, s in self.rows()}, {("steam", "manual"), ("gog", "manual"), ("epic", "manual")})

    def test_a_store_sync_leaves_hand_added_games_alone(self):
        self.api("steam", "Portal", "Portal 2")
        manual.add_entry("steam", "Hidden Gem")
        manual.sync(self.conn)
        self.api("steam", "Portal", "Portal 2", "Half-Life")  # what the next `sync steam` does
        self.assertEqual([r[2] for r in self.rows("steam", "manual")], ["Hidden Gem"])
        self.assertEqual({r[2] for r in self.rows("steam", "api")}, {"Portal", "Portal 2", "Half-Life"})

    def test_the_manual_sync_leaves_the_store_games_alone(self):
        self.api("steam", "Portal", "Portal 2")
        manual.add_entry("steam", "Hidden Gem")  # this used to risk wiping every Steam game
        manual.sync(self.conn)
        manual.sync(self.conn)
        self.assertEqual(len(self.rows("steam", "api")), 2)
        self.assertEqual(len(self.rows("steam", "manual")), 1)

    def test_removing_a_manual_game_deletes_only_that_row(self):
        self.api("steam", "Portal")
        entry = manual.add_entry("steam", "Hidden Gem")
        other = manual.add_entry("battlenet", "Diablo IV")
        manual.sync(self.conn)
        manual.remove_entry("steam", entry["platform_id"])
        manual.sync(self.conn)
        self.assertEqual([r[2] for r in self.rows("steam")], ["Portal"])
        self.assertEqual([r[2] for r in self.rows("battlenet")], ["Diablo IV"])
        manual.remove_entry("battlenet", other["platform_id"])
        manual.sync(self.conn)
        self.assertEqual(self.rows("battlenet"), [], "a platform whose last manual game was removed is emptied")
        self.assertEqual(len(self.rows("steam")), 1)

    def test_removing_does_not_touch_organization_attached_to_the_game(self):
        entry = manual.add_entry("battlenet", "Diablo IV")
        manual.sync(self.conn)
        store.set_status(self.conn, "diabloiv", ["played"])
        store.set_rating(self.conn, "diabloiv", 9)
        store.set_note(self.conn, "diabloiv", "great")
        manual.remove_entry("battlenet", entry["platform_id"])
        manual.sync(self.conn)
        self.assertEqual((store.all_status(self.conn), store.all_ratings(self.conn), store.all_notes(self.conn)),
                         ({"diabloiv": ["played"]}, {"diabloiv": 9}, {"diabloiv": "great"}))
        manual.add_entry("battlenet", "Diablo IV")  # adding it again brings the organization back
        manual.sync(self.conn)
        group = group_games(self.conn.execute("SELECT * FROM games").fetchall(), store.load_meta(self.conn))[0]
        self.assertEqual((group["statuses"], group["rating"], group["note"]), (["played"], 9, "great"))

    def test_a_duplicate_shows_up_as_two_licenses_and_only_the_manual_one_is_removable(self):
        self.api("steam", "Portal 2")
        manual.add_entry("steam", "Portal 2")
        manual.sync(self.conn)
        groups = group_games(self.conn.execute("SELECT * FROM games").fetchall(), store.load_meta(self.conn))
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(e["manual"] for e in groups[0]["entries"]), [False, True])

    def test_a_hand_typed_id_that_equals_a_real_one_does_not_break_syncs(self):
        self.api("steam", "Portal")  # id "steam-0"
        manual.MANUAL_PATH.write_text('[{"platform": "steam", "platform_id": "steam-0", "title": "Clash"}]')
        manual.sync(self.conn)  # must not raise
        self.api("steam", "Portal")  # nor must this
        self.assertEqual(len(self.rows("steam", "api")), 1)

    def test_manual_json_with_a_store_platform_is_accepted_when_hand_edited(self):
        manual.MANUAL_PATH.write_text('[{"platform": "gog", "title": "Old Game"}]')
        self.assertEqual(manual.sync(self.conn), 1)
        self.assertEqual(self.rows("gog", "manual")[0][2], "Old Game")


if __name__ == "__main__":
    unittest.main()
