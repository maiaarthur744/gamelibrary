import os
import sqlite3
import time
from pathlib import Path

from .models import Game

# GAMELIBRARY_DATA_DIR lets tests run against a copy instead of the real library.
DATA_DIR = Path(os.environ.get("GAMELIBRARY_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DB_PATH = DATA_DIR / "library.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    platform         TEXT NOT NULL,
    platform_id      TEXT NOT NULL,
    title            TEXT NOT NULL,
    playtime_minutes INTEGER,
    last_played      INTEGER,
    url              TEXT,
    cover_url        TEXT,
    synced_at        INTEGER NOT NULL,
    PRIMARY KEY (platform, platform_id)
);
CREATE TABLE IF NOT EXISTS hidden (
    key   TEXT PRIMARY KEY,  -- grouping key, so it survives re-syncs
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    key TEXT NOT NULL,  -- grouping key, so tags survive re-syncs
    tag TEXT NOT NULL,
    PRIMARY KEY (key, tag)
);
CREATE TABLE IF NOT EXISTS game_status (
    key    TEXT NOT NULL,  -- grouping key; a game can have several statuses
    status TEXT NOT NULL,
    PRIMARY KEY (key, status)
);
CREATE TABLE IF NOT EXISTS ratings (
    key    TEXT PRIMARY KEY,  -- grouping key
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5)
);
CREATE TABLE IF NOT EXISTS notes (
    key  TEXT PRIMARY KEY,  -- grouping key
    note TEXT NOT NULL
);
DROP TABLE IF EXISTS splits;  -- first version keyed by title, which can't tell identical titles apart
CREATE TABLE IF NOT EXISTS entry_splits (
    entry_key TEXT PRIMARY KEY  -- "platform:platform_id" of a license pulled out of its auto-group
);
CREATE INDEX IF NOT EXISTS idx_games_title ON games (title COLLATE NOCASE);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    with conn:  # first version allowed a single status per game: keep what was already set
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'status'").fetchone():
            conn.execute("INSERT OR IGNORE INTO game_status (key, status) SELECT key, status FROM status")
            conn.execute("DROP TABLE status")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(games)")}
    if "cover_url" not in columns:  # database created before covers existed
        conn.execute("ALTER TABLE games ADD COLUMN cover_url TEXT")
    return conn


def replace_platform(conn: sqlite3.Connection, platform: str, games: list[Game]) -> None:
    """Make the stored games for `platform` match `games` exactly."""
    now = int(time.time())
    with conn:
        conn.execute("DELETE FROM games WHERE platform = ?", (platform,))
        conn.executemany(
            "INSERT INTO games (platform, platform_id, title, playtime_minutes, last_played,"
            " url, cover_url, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (g.platform, g.platform_id, g.title, g.playtime_minutes, g.last_played, g.url,
                 g.cover_url, now)
                for g in games
            ],
        )


def hidden_keys(conn: sqlite3.Connection) -> set[str]:
    return {r["key"] for r in conn.execute("SELECT key FROM hidden")}


def set_hidden(conn: sqlite3.Connection, key: str, title: str, hidden: bool) -> None:
    with conn:
        if hidden:
            conn.execute("INSERT OR REPLACE INTO hidden (key, title) VALUES (?, ?)", (key, title))
        else:
            conn.execute("DELETE FROM hidden WHERE key = ?", (key,))


STATUSES = ("playing", "played", "backlog", "replay", "dropped", "multiplayer", "endless")


def all_status(conn: sqlite3.Connection) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for r in conn.execute("SELECT key, status FROM game_status"):
        found.setdefault(r["key"], set()).add(r["status"])
    return {key: [s for s in STATUSES if s in have] for key, have in found.items()}


def set_status(conn: sqlite3.Connection, key: str, statuses: list[str]) -> None:
    if not isinstance(statuses, list):
        raise TypeError("statuses must be a list")
    unknown = [s for s in statuses if s not in STATUSES]
    if unknown:
        raise ValueError(f"unknown status {unknown[0]!r}")
    with conn:
        conn.execute("DELETE FROM game_status WHERE key = ?", (key,))
        conn.executemany(
            "INSERT OR IGNORE INTO game_status (key, status) VALUES (?, ?)", [(key, s) for s in statuses]
        )


def all_ratings(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["key"]: r["rating"] for r in conn.execute("SELECT key, rating FROM ratings")}


def set_rating(conn: sqlite3.Connection, key: str, rating: int | None) -> None:
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
        raise ValueError("rating must be an integer from 1 to 5")
    with conn:
        if rating is None:
            conn.execute("DELETE FROM ratings WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO ratings (key, rating) VALUES (?, ?)", (key, rating))


def all_notes(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["note"] for r in conn.execute("SELECT key, note FROM notes")}


def set_note(conn: sqlite3.Connection, key: str, note: str) -> None:
    if not isinstance(note, str):
        raise TypeError("note must be a string")
    note = note.strip()[:20000]
    with conn:
        if note:
            conn.execute("INSERT OR REPLACE INTO notes (key, note) VALUES (?, ?)", (key, note))
        else:
            conn.execute("DELETE FROM notes WHERE key = ?", (key,))


def entry_key(platform: str, platform_id: str) -> str:
    return f"{platform}:{platform_id}"


def split_keys(conn: sqlite3.Connection) -> set[str]:
    return {r["entry_key"] for r in conn.execute("SELECT entry_key FROM entry_splits")}


def set_split(conn: sqlite3.Connection, key: str, split: bool) -> None:
    with conn:
        if split:
            conn.execute("INSERT OR IGNORE INTO entry_splits (entry_key) VALUES (?)", (key,))
        else:
            conn.execute("DELETE FROM entry_splits WHERE entry_key = ?", (key,))


def all_tags(conn: sqlite3.Connection) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    for r in conn.execute("SELECT key, tag FROM tags ORDER BY tag COLLATE NOCASE"):
        tags.setdefault(r["key"], []).append(r["tag"])
    return tags


def set_tags(conn: sqlite3.Connection, key: str, tags: list[str]) -> None:
    with conn:
        conn.execute("DELETE FROM tags WHERE key = ?", (key,))
        conn.executemany("INSERT INTO tags (key, tag) VALUES (?, ?)", [(key, t) for t in tags])


def query(conn: sqlite3.Connection, platform: str | None, search: str | None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM games WHERE 1=1"
    params: list[str] = []
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    if search:
        sql += " AND title LIKE ?"
        params.append(f"%{search}%")
    sql += " ORDER BY title COLLATE NOCASE"
    return conn.execute(sql, params).fetchall()
