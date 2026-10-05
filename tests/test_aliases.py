"""data/aliases.json: both formats work, and a broken file can't take the page down. Temporary files only.

Run from the project root:  python -m unittest discover tests -v
"""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("GAMELIBRARY_DATA_DIR", tempfile.mkdtemp(prefix="gamelibrary-test-"))

from gamelibrary import grouping, server, store  # noqa: E402
from gamelibrary.grouping import group_games  # noqa: E402
from gamelibrary.models import Game, Meta  # noqa: E402

USERS_FILE = """[
  { "System Shock 2 (1999)": "System Shock 2: 25th Anniversary Remaster" },
  { "The Witcher 3: Wild Hunt": "The Witcher 3: Wild Hunt — Remastered" }
]"""


class AliasBase(unittest.TestCase):
    """Helpers only: the tests live in the subclasses (otherwise they would be inherited and run twice)."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "aliases.json"
        patcher = mock.patch.object(grouping, "ALIASES_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        grouping._warned.clear()

    def groups(self, titles_by_platform):
        rows = [{"platform": p, "platform_id": str(i), "title": t, "playtime_minutes": None, "last_played": None,
                 "cover_url": None, "developer": None, "source": "api", "url": None}
                for i, (p, t) in enumerate(titles_by_platform)]
        return group_games(rows)

    def load(self, text):
        self.path.write_text(text, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            aliases, self.main_titles = grouping._load_aliases()
        return aliases, err.getvalue()


class Aliases(AliasBase):
    GAMES = [("steam", "System Shock 2 (1999)"), ("gog", "System Shock 2: 25th Anniversary Remaster"),
             ("steam", "The Witcher 3: Wild Hunt"), ("epic", "The Witcher 3: Wild Hunt — Remastered")]

    def test_without_aliases_these_stay_apart(self):
        self.assertEqual(len(self.groups(self.GAMES)), 4)

    def test_the_object_format_merges_titles(self):
        self.path.write_text(json.dumps({"System Shock 2 (1999)": "System Shock 2: 25th Anniversary Remaster",
                                         "The Witcher 3: Wild Hunt": "The Witcher 3: Wild Hunt — Remastered"}), encoding="utf-8")
        groups = self.groups(self.GAMES)
        self.assertEqual(sorted(len(g["entries"]) for g in groups), [2, 2])

    def test_a_list_of_objects_works_the_same_way(self):
        """The format that crashed the page."""
        self.path.write_text(USERS_FILE, encoding="utf-8")
        groups = self.groups(self.GAMES)
        self.assertEqual(sorted(len(g["entries"]) for g in groups), [2, 2])
        merged = {frozenset(e["title"] for e in g["entries"]) for g in groups}
        self.assertIn(frozenset({"System Shock 2 (1999)", "System Shock 2: 25th Anniversary Remaster"}), merged)
        self.assertIn(frozenset({"The Witcher 3: Wild Hunt", "The Witcher 3: Wild Hunt — Remastered"}), merged)

    def test_both_formats_load_to_the_same_mapping(self):
        as_object, _ = self.load('{"A": "B", "C": "D"}')
        as_list, _ = self.load('[{"A": "B"}, {"C": "D"}]')
        self.assertEqual(as_object, as_list)

    def test_empty_and_missing_files_are_fine(self):
        self.assertEqual(grouping._load_aliases(), ({}, set()))  # no file
        for text in ("{}", "[]"):
            aliases, err = self.load(text)
            self.assertEqual((aliases, err), ({}, ""))

    def test_a_broken_file_is_ignored_with_one_clear_warning(self):
        for text in ("{not json", '"just a string"', "[1, 2]", '{"A": 5}', '{"A": []}', '{"A": [1]}', '{"A": ["B", 2]}', "42"):
            grouping._warned.clear()
            aliases, err = self.load(text)
            self.assertEqual(aliases, {}, text)
            self.assertIn("[aviso] aliases.json foi ignorado", err, text)
        grouping._warned.clear()
        self.path.write_text("{not json", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(5):  # every page load reads the file: it must not spam the terminal
                grouping._load_aliases()
        self.assertEqual(err.getvalue().count("[aviso]"), 1)

    def test_a_broken_file_does_not_take_the_page_down(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        db = Path(tempfile.mkdtemp()) / "library.db"
        with mock.patch.object(store, "DB_PATH", db):
            store.replace_platform(store.connect(), "steam", [Game("steam", "1", "Portal")])
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.shutdown)
            with contextlib.redirect_stderr(io.StringIO()):
                with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/games") as r:
                    status, body = r.status, json.loads(r.read())
        self.assertEqual(status, 200)
        self.assertEqual([g["title"] for g in body], ["Portal"])


PARENT = "Warhammer 40,000: Dawn of War - Anniversary Edition"
CHILDREN = ["Warhammer 40,000: Dawn of War - Dark Crusade", "Warhammer 40,000: Dawn of War - Winter Assault",
            "Warhammer 40,000: Dawn of War - Soulstorm"]
DOW = [("steam", PARENT), *[("steam", c) for c in CHILDREN]]


class OneMainTitleSeveralChildren(AliasBase):
    """The case that did not work: a base game that should absorb its expansions."""

    def parent_meta(self):
        key = grouping.normalize(PARENT)
        return Meta(status={key: ["backlog", "multiplayer"]}, tags={key: ["rts"]}, ratings={key: 8})

    def check(self):
        rows = [{"platform": p, "platform_id": str(i), "title": t, "playtime_minutes": None, "last_played": None,
                 "cover_url": None, "developer": None, "source": "api", "url": None} for i, (p, t) in enumerate(DOW)]
        groups = group_games(rows, self.parent_meta())
        self.assertEqual(len(groups), 1, [g["title"] for g in groups])
        group = groups[0]
        self.assertEqual(sorted(e["title"] for e in group["entries"]), sorted([PARENT, *CHILDREN]))
        self.assertEqual(group["key"], grouping.normalize(PARENT), "the main title keeps its key")
        self.assertEqual(group["title"], PARENT, "...and names the group, instead of the shortest expansion title")
        self.assertEqual((group["statuses"], group["tags"], group["rating"]), (["backlog", "multiplayer"], ["rts"], 8),
                         "what you set on the main game must stay attached to the merged group")

    def test_the_same_title_repeated_on_the_left_makes_it_the_main_one(self):
        """Exactly what was written in the user's file."""
        self.path.write_text(json.dumps([{PARENT: c} for c in CHILDREN]), encoding="utf-8")
        self.check()

    def test_a_list_on_the_right_means_the_same(self):
        self.path.write_text(json.dumps({PARENT: CHILDREN}), encoding="utf-8")
        self.check()

    def test_both_spellings_build_the_same_mapping(self):
        repeated, _ = self.load(json.dumps([{PARENT: c} for c in CHILDREN]))
        as_list, _ = self.load(json.dumps({PARENT: CHILDREN}))
        self.assertEqual(repeated, as_list)
        self.assertEqual(set(repeated.values()), {grouping.normalize(PARENT)})
        self.assertEqual(set(repeated), {grouping.normalize(c) for c in CHILDREN})

    def test_only_titles_with_several_partners_count_as_main(self):
        self.load(json.dumps([{PARENT: c} for c in CHILDREN] + [{"A": "B"}]))
        self.assertEqual(self.main_titles, {grouping.normalize(PARENT)})

    def test_repeating_the_same_pair_changes_nothing(self):
        aliases, _ = self.load(json.dumps([{"A": "B"}, {"A": "B"}, {"A": "B"}]))
        self.assertEqual(aliases, {"a": "b"})

    def test_a_title_pointing_at_itself_is_ignored(self):
        aliases, _ = self.load(json.dumps({"A": "A", "C": "D"}))
        self.assertEqual(aliases, {"c": "d"})


class SingleAliasesKeepTheirMeaning(AliasBase):
    """The two-title form must behave exactly as it did before: the left title joins the right one's group."""

    def test_the_left_title_joins_the_group_of_the_right_one(self):
        self.path.write_text(json.dumps({"System Shock 2 (1999)": "System Shock 2: 25th Anniversary Remaster"}), encoding="utf-8")
        rows = [{"platform": p, "platform_id": str(i), "title": t, "playtime_minutes": None, "last_played": None,
                 "cover_url": None, "developer": None, "source": "api", "url": None}
                for i, (p, t) in enumerate([("gog", "System Shock 2 (1999)"), ("epic", "System Shock 2: 25th Anniversary Remaster")])]
        (group,) = group_games(rows)
        self.assertEqual(group["key"], grouping.normalize("System Shock 2: 25th Anniversary Remaster"))
        self.assertEqual(group["title"], "System Shock 2 (1999)", "two-title aliases keep the old rule: the shortest title")

    def test_chains_end_up_in_one_group(self):
        aliases, _ = self.load(json.dumps([{"A": "B"}, {"B": "C"}]))
        self.assertEqual([grouping._resolve(k, aliases) for k in ("a", "b", "c", "z")], ["c", "c", "c", "z"])

    def test_a_loop_in_the_file_still_gives_one_stable_group(self):
        aliases, _ = self.load(json.dumps([{"A": "B"}, {"B": "C"}, {"C": "B"}]))
        keys = {grouping._resolve(k, aliases) for k in ("a", "b", "c")}
        self.assertEqual(len(keys), 1, "everything in or leading into the loop must agree")

    def test_a_loop_does_not_hang_or_crash(self):
        aliases, _ = self.load(json.dumps([{"A": "B"}, {"B": "A"}]))
        self.assertEqual(grouping._resolve("a", aliases), grouping._resolve("b", aliases))


if __name__ == "__main__":
    unittest.main()
