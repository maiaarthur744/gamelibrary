"""The first Epic sync, end to end: the REAL Legendary against a fake Epic backend (tests/fake_epic.py).

No network is used. This exists because a cap of 100 requests per run made a first sync need 6+ runs, and
because Legendary silently ignores command-line options it does not know.

Run from the project root:  python -m unittest discover tests -v
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("GAMELIBRARY_DATA_DIR", tempfile.mkdtemp(prefix="gamelibrary-test-"))

from gamelibrary import net  # noqa: E402
from gamelibrary.providers import epic  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_epic.py"
GAMES, LIBRARY = 214, 30  # installable games, and third-party items without assets


def run_fake(config: str, budget: int) -> tuple[int, int, bool, list | None]:
    """One `legendary list` run. Returns (exit code, requests sent, hit the cap, games listed)."""
    env = {**os.environ, "LEGENDARY_CONFIG_PATH": config, "GAMELIBRARY_EPIC_BUDGET": str(budget),
           "GAMELIBRARY_EPIC_INTERVAL": "0", "FAKE_EPIC_GAMES": str(GAMES), "FAKE_EPIC_LIBRARY": str(LIBRARY),
           "PYTHONUTF8": "1"}
    done = subprocess.run([sys.executable, str(FAKE)], capture_output=True, text=True, env=env, timeout=120)
    lines = done.stderr.splitlines()
    sent = sum(1 for line in lines if line.startswith("[net] epic") and "BLOQUEADA" not in line)
    games = json.loads(done.stdout) if done.returncode == 0 else None
    return done.returncode, sent, any("BLOQUEADA" in line for line in lines), games


class Flags(unittest.TestCase):
    def test_every_option_we_pass_to_legendary_exists(self):
        """Legendary ignores unknown options without a word: --include-noasset was silently doing nothing."""
        done = subprocess.run([sys.executable, "-m", "gamelibrary.legendary_logged", "list", "--help"],
                              capture_output=True, text=True, timeout=60)
        for arg in (a for a in epic.LIST_ARGS if a.startswith("--")):
            self.assertIn(arg, done.stdout, f"Legendary has no {arg} option, so it would be ignored")


class FirstSync(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.mkdtemp(prefix="legendary-cfg-")

    def test_a_cold_first_sync_finishes_in_one_run_with_the_real_ceiling(self):
        code, sent, hit_cap, games = run_fake(self.config, net.BUDGETS["epic"])
        self.assertEqual((code, hit_cap), (0, False))
        self.assertEqual(len(games), GAMES + LIBRARY, "installable games AND third-party items")
        self.assertGreaterEqual(sent, 2 * GAMES, "about two requests per game on a cold cache")
        self.assertLess(sent, 2 * GAMES + 100)

    def test_the_next_sync_is_tiny(self):
        run_fake(self.config, net.BUDGETS["epic"])
        code, sent, _, games = run_fake(self.config, net.BUDGETS["epic"])
        self.assertEqual(code, 0)
        self.assertLess(sent, 15)
        self.assertEqual(len(games), GAMES + LIBRARY)

    def test_if_the_ceiling_is_ever_hit_running_again_continues_where_it_stopped(self):
        """Progress is kept on disk, so a small cap only costs extra runs (this is how 100 behaved)."""
        runs = 0
        while runs < 20:
            runs += 1
            code, sent, hit_cap, games = run_fake(self.config, 100)
            if code == 0:
                break
            self.assertTrue(hit_cap, "a failed run must be the cap, not something else")
            self.assertLessEqual(sent, 100, "and it must stop before sending more than the cap")
        self.assertEqual(code, 0, f"never finished in {runs} runs")
        self.assertEqual(len(games), GAMES + LIBRARY)
        self.assertGreater(runs, 4, "with a cap of 100 a cold sync takes several runs: that is why the ceiling is high")


class ThroughTheApp(unittest.TestCase):
    """epic.fetch_games(): the app's own plumbing (log forwarding, counting, parsing) around the same fake."""

    def fetch(self, budget: int):
        config = tempfile.mkdtemp(prefix="legendary-cfg-")
        env = {"LEGENDARY_CONFIG_PATH": config, "GAMELIBRARY_EPIC_INTERVAL": "0", "FAKE_EPIC_GAMES": str(GAMES),
               "FAKE_EPIC_LIBRARY": str(LIBRARY)}
        err = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(epic, "LEGENDARY", [sys.executable, str(FAKE)]), \
                mock.patch.dict(net.BUDGETS, {"epic": budget}), contextlib.redirect_stderr(err), \
                net.session(), net.provider("epic"):
            try:
                games = epic.fetch_games()
            except net.BudgetExceeded as e:
                return None, str(e), err.getvalue(), net._counts.get("epic", 0)
            return games, None, err.getvalue(), net._counts.get("epic", 0)

    def test_a_cold_sync_returns_every_game_and_counts_every_request(self):
        games, error, log, counted = self.fetch(net.BUDGETS["epic"])
        self.assertIsNone(error)
        self.assertEqual(len(games), GAMES + LIBRARY)
        self.assertEqual({g.platform for g in games}, {"epic"})
        self.assertEqual(sum(1 for g in games if g.title.startswith("Third Party")), LIBRARY)
        self.assertTrue(all(g.developer == "Fake Studio" for g in games))
        self.assertIn("primeira sincronização", log, "a first sync announces itself")
        shown = sum(1 for line in log.splitlines() if line.startswith("[net] epic") and "->" in line)
        self.assertEqual(counted, shown, "every request shown in the terminal is counted in the summary")

    def test_hitting_the_ceiling_gives_a_clear_message(self):
        games, error, log, _ = self.fetch(50)
        self.assertIsNone(games)
        self.assertIn("teto de segurança", error)
        self.assertIn("rode `gamelibrary sync epic` de novo", error)
        self.assertIn("BLOQUEADA", log)


if __name__ == "__main__":
    unittest.main()
