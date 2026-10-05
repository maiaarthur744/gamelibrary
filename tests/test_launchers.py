"""The double-click launchers (GameLibrary.bat / GameLibrary.command), the Steam setup command, and
Windows-safe text handling. The .bat cannot be run on non-Windows machines, so it gets static checks.

Run from the project root:  python -m unittest discover tests -v
"""
import ast
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("GAMELIBRARY_DATA_DIR", tempfile.mkdtemp(prefix="gamelibrary-test-"))

from gamelibrary import cli

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "GameLibrary.bat"
SH = ROOT / "GameLibrary.command"


class BatchFile(unittest.TestCase):
    def setUp(self):
        self.raw = BAT.read_bytes()
        self.lines = self.raw.decode("ascii").split("\r\n")  # also proves it is pure ASCII

    def test_uses_windows_line_endings_only(self):
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""), "a bare LF breaks goto/call labels in cmd.exe")

    def test_every_goto_and_call_points_to_an_existing_label(self):
        labels = {m.group(1).lower() for line in self.lines if (m := re.match(r"^:(\w+)\s*$", line))}
        used = {m.group(1).lower() for line in self.lines for m in re.finditer(r"\b(?:goto|call)\s+:(\w+)", line, re.I)}
        self.assertEqual(sorted(used - labels), [], "jumps to labels that do not exist")
        self.assertEqual(sorted(labels - used - {"menu"}), [], "labels nobody jumps to")

    def test_parentheses_are_balanced_outside_text(self):
        depth = 0
        for number, line in enumerate(self.lines, 1):
            if re.match(r"^\s*(echo|rem)\b", line, re.I):
                # inside echo text, parentheses must be escaped as ^( ^) so they can't close a block
                self.assertIsNone(re.search(r"(?<!\^)[()]", line.split(None, 1)[1] if " " in line.strip() else ""),
                                  f"unescaped parenthesis in an echo on line {number}")
                continue
            line = re.sub(r'"[^"]*"', '""', line)
            depth += line.count("(") - line.count(")")
            self.assertGreaterEqual(depth, 0, f"extra ) on line {number}")
        self.assertEqual(depth, 0, "a ( block is never closed")

    def test_nothing_chains_commands_after_an_if_on_the_same_line(self):
        for number, line in enumerate(self.lines, 1):
            if re.match(r"^\s*if\b", line, re.I):
                self.assertNotRegex(re.sub(r'"[^"]*"|>nul 2>&1', "", line), r"&", f"line {number}: use a ( ) block instead")

    def test_checks_for_python_3_11_and_never_uses_the_windows_store_alias_blindly(self):
        text = self.raw.decode("ascii")
        self.assertIn("sys.version_info >= (3, 11)", text)
        self.assertIn('"py -3"', text)
        self.assertIn("python.org/downloads", text)


class Launchers(unittest.TestCase):
    def test_command_script_is_executable_and_valid_bash(self):
        self.assertTrue(os.access(SH, os.X_OK))
        self.assertTrue(SH.read_text().startswith("#!/usr/bin/env bash"))
        self.assertNotIn(b"\r", SH.read_bytes(), "CRLF breaks bash scripts")
        subprocess.run(["bash", "-n", str(SH)], check=True)

    def test_every_command_the_menus_run_is_a_real_subcommand(self):
        bat = BAT.read_text()
        sh = SH.read_text()
        used = set(re.findall(r'call :comando "([\w -]+)"', bat)) | {a.strip() for a in re.findall(r"\bcomando ([\w -]+?)(?:;|$)", sh, re.M)}
        used |= {"serve"}  # opened directly by both scripts
        self.assertTrue({"sync", "sync steam", "sync gog", "sync epic", "sync manual", "steam-setup", "gog-login", "epic-login"} <= used, used)
        for command in sorted(used):
            done = subprocess.run([sys.executable, "-m", "gamelibrary.cli", *command.split(), "--help"], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, f"`gamelibrary {command}` is not a valid command: {done.stderr}")

    def test_both_menus_offer_the_same_actions(self):
        bat_actions = re.findall(r'call :comando "([\w -]+)"', BAT.read_text())
        sh_actions = [a.strip() for a in re.findall(r"\bcomando ([\w -]+?)(?:;|$)", SH.read_text(), re.M)]
        self.assertEqual(sorted(set(bat_actions)), sorted(set(sh_actions)))


class SteamSetup(unittest.TestCase):
    KEY, STEAM_ID = "0123456789ABCDEF0123456789abcdef", "76561198000000000"

    def run_setup(self, *answers, env_text=None):
        path = Path(tempfile.mkdtemp()) / ".env"
        if env_text is not None:
            path.write_text(env_text, encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(cli, "ENV_PATH", path), mock.patch("builtins.input", side_effect=list(answers)), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.cmd_steam_setup(None)
        return code, path, err.getvalue()

    def test_saves_both_values_where_the_sync_reads_them(self):
        code, path, _ = self.run_setup(f"  {self.KEY}  ", f" {self.STEAM_ID} ")
        self.assertEqual(code, 0)
        self.assertEqual(path.read_text(encoding="utf-8").splitlines(), [f"STEAM_API_KEY={self.KEY}", f"STEAM_ID={self.STEAM_ID}"])
        from dotenv import dotenv_values
        self.assertEqual(dict(dotenv_values(path)), {"STEAM_API_KEY": self.KEY, "STEAM_ID": self.STEAM_ID})

    def test_keeps_other_lines_and_replaces_old_values(self):
        code, path, _ = self.run_setup(self.KEY, self.STEAM_ID, env_text="OTHER=keep\nSTEAM_ID=111\n")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("OTHER=keep", text)
        self.assertEqual(text.count("STEAM_ID="), 1)
        self.assertIn(f"STEAM_ID={self.STEAM_ID}", text)

    def test_rejects_bad_input_and_saves_nothing(self):
        for key, steam_id, message in (("short", self.STEAM_ID, "32"), (self.KEY, "12345", "17"), ("Z" * 32, self.STEAM_ID, "32")):
            code, path, err = self.run_setup(key, steam_id)
            self.assertEqual(code, 1)
            self.assertIn(message, err)
            self.assertIn("Nada foi salvo", err)
            self.assertEqual(path.read_text(encoding="utf-8") if path.exists() else "", "", "must not write a half-configured file")


class WindowsSafeText(unittest.TestCase):
    """Windows opens text files as cp1252 and decodes pipes as cp1252 unless told otherwise."""

    def calls(self):
        for path in (ROOT / "gamelibrary").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    yield path.relative_to(ROOT), node

    def test_every_text_file_read_and_write_names_its_encoding(self):
        bad = [f"{p}:{n.lineno}" for p, n in self.calls()
               if isinstance(n.func, ast.Attribute) and n.func.attr in ("read_text", "write_text")
               and "encoding" not in {k.arg for k in n.keywords}]
        self.assertEqual(bad, [])

    def test_text_mode_subprocesses_name_their_encoding(self):
        bad = []
        for p, n in self.calls():
            if isinstance(n.func, ast.Attribute) and n.func.attr in ("Popen", "run"):
                kw = {k.arg for k in n.keywords}
                if ("text" in kw or "universal_newlines" in kw) and "encoding" not in kw:
                    bad.append(f"{p}:{n.lineno}")
        self.assertEqual(bad, [])

    def test_titles_outside_cp1252_survive_the_manual_file(self):
        from gamelibrary.providers import manual
        path = Path(tempfile.mkdtemp()) / "manual.json"
        with mock.patch.object(manual, "MANUAL_PATH", path):
            manual._save([{"platform": "itch", "title": "原神 ゲーム 🎮 Pokémon™"}])
            self.assertEqual(manual.load_entries()[0]["title"], "原神 ゲーム 🎮 Pokémon™")
        self.assertIn("原神".encode("utf-8"), path.read_bytes(), "the file itself must be UTF-8")


if __name__ == "__main__":
    unittest.main()
