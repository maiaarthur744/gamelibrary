import sqlite3
import time
from pathlib import Path

from .models import Game

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
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
CREATE TABLE IF NOT EXISTS status (
    key    TEXT PRIMARY KEY,  -- grouping key
    status TEXT NOT NULL
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


STATUSES = ("playing", "played", "backlog", "replay", "dropped")


def all_status(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["status"] for r in conn.execute("SELECT key, status FROM status")}


def set_status(conn: sqlite3.Connection, key: str, status: str | None) -> None:
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    with conn:
        if status is None:
            conn.execute("DELETE FROM status WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO status (key, status) VALUES (?, ?)", (key, status))


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
