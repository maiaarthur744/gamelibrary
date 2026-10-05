import os
import sqlite3
import time
from datetime import date
from pathlib import Path

from .models import Game, Meta

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
    developer        TEXT,
    source           TEXT NOT NULL DEFAULT 'api',  -- 'api' (a store's sync) or 'manual' (added by hand)
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
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 10)  -- half stars: 7 = 3.5 stars
);
CREATE TABLE IF NOT EXISTS developers (
    key       TEXT PRIMARY KEY,  -- grouping key
    developer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playtimes (
    key     TEXT PRIMARY KEY,  -- grouping key
    minutes INTEGER NOT NULL CHECK (minutes >= 0)
);
CREATE TABLE IF NOT EXISTS notes (
    key  TEXT PRIMARY KEY,  -- grouping key
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS covers (
    key TEXT PRIMARY KEY,  -- grouping key
    url TEXT NOT NULL      -- http(s) URL or /covers/<file> for an uploaded image
);
CREATE TABLE IF NOT EXISTS dates (
    key      TEXT PRIMARY KEY,  -- grouping key
    started  TEXT,              -- ISO dates (YYYY-MM-DD)
    finished TEXT
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
    if "developer" not in columns:
        conn.execute("ALTER TABLE games ADD COLUMN developer TEXT")
    if "source" not in columns:  # before this, nothing recorded where a row came from
        with conn:
            conn.execute("BEGIN")
            conn.execute("ALTER TABLE games ADD COLUMN source TEXT NOT NULL DEFAULT 'api'")
            # Back then only these three stores synced from an API; everything else was typed in by hand.
            conn.execute("UPDATE games SET source = 'manual' WHERE platform NOT IN ('steam', 'gog', 'epic')")
    _migrate_ratings_to_half_stars(conn)
    return conn


def _migrate_ratings_to_half_stars(conn: sqlite3.Connection) -> None:
    """Ratings used to be 1-5 whole stars; they are now 1-10 (half stars). Same grades, doubled."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ratings'").fetchone()
    if not row or "BETWEEN 1 AND 5" not in row["sql"]:
        return
    with conn:
        conn.execute("BEGIN")  # DDL does not open a transaction by itself, and this must be all-or-nothing
        conn.execute("ALTER TABLE ratings RENAME TO ratings_old")
        conn.execute("CREATE TABLE ratings (key TEXT PRIMARY KEY, rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 10))")
        conn.execute("INSERT INTO ratings (key, rating) SELECT key, rating * 2 FROM ratings_old")
        conn.execute("DROP TABLE ratings_old")


def replace_platform(conn: sqlite3.Connection, platform: str, games: list[Game], source: str = "api") -> None:
    """Make the games of `platform` that came from `source` match `games` exactly.

    Rows from the other source are never touched, so a store's sync can't erase games you added by hand
    to that same store, and the other way around.
    """
    now = int(time.time())
    with conn:
        conn.execute("DELETE FROM games WHERE platform = ? AND source = ?", (platform, source))
        conn.executemany(
            # OR IGNORE: a hand-typed id that equals a real one must not break the whole sync
            "INSERT OR IGNORE INTO games (platform, platform_id, title, playtime_minutes, last_played,"
            " url, cover_url, developer, source, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (g.platform, g.platform_id, g.title, g.playtime_minutes, g.last_played, g.url,
                 g.cover_url, g.developer, source, now)
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


STATUSES = ("playing", "played", "backlog", "replay", "dropped", "multiplayer", "endless", "unplayed")


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
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 10):
        raise ValueError("rating must be a whole number from 1 to 10 (half stars)")
    with conn:
        if rating is None:
            conn.execute("DELETE FROM ratings WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO ratings (key, rating) VALUES (?, ?)", (key, rating))


def all_developers(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["developer"] for r in conn.execute("SELECT key, developer FROM developers")}


def set_developer(conn: sqlite3.Connection, key: str, developer: str) -> None:
    if not isinstance(developer, str):
        raise TypeError("developer must be a string")
    developer = " ".join(developer.split())[:200]
    with conn:
        if developer:
            conn.execute("INSERT OR REPLACE INTO developers (key, developer) VALUES (?, ?)", (key, developer))
        else:  # emptying the field goes back to the automatic value
            conn.execute("DELETE FROM developers WHERE key = ?", (key,))


def all_playtimes(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["key"]: r["minutes"] for r in conn.execute("SELECT key, minutes FROM playtimes")}


def set_playtime(conn: sqlite3.Connection, key: str, hours: float | None) -> None:
    """The hours typed by the user replace the automatic total. None or "" removes them."""
    if hours is None or hours == "":
        minutes = None
    else:
        if isinstance(hours, bool) or not isinstance(hours, (int, float)):
            raise TypeError("hours must be a number")
        if not 0 <= hours <= 100000:  # also rejects NaN
            raise ValueError("as horas jogadas precisam estar entre 0 e 100000")
        minutes = round(hours * 60)
    with conn:
        if minutes is None:
            conn.execute("DELETE FROM playtimes WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO playtimes (key, minutes) VALUES (?, ?)", (key, minutes))


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


def all_covers(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["url"] for r in conn.execute("SELECT key, url FROM covers")}


def set_cover(conn: sqlite3.Connection, key: str, url: str | None) -> None:
    if url is not None:
        if not isinstance(url, str) or len(url) > 2000 or not url.startswith(("http://", "https://", "/covers/")):
            raise ValueError("a capa precisa ser um link http(s) ou uma imagem enviada")
    with conn:
        if url is None:
            conn.execute("DELETE FROM covers WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO covers (key, url) VALUES (?, ?)", (key, url))


def all_dates(conn: sqlite3.Connection) -> dict[str, tuple[str | None, str | None]]:
    return {r["key"]: (r["started"], r["finished"]) for r in conn.execute("SELECT key, started, finished FROM dates")}


def set_dates(conn: sqlite3.Connection, key: str, started: str | None, finished: str | None) -> None:
    def parse(value):
        if not value:
            return None
        if not isinstance(value, str):
            raise TypeError("date must be a string")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("data inválida") from None

    s, f = parse(started), parse(finished)
    if s and f and f < s:
        raise ValueError("a data de fim não pode ser anterior à de início")
    with conn:
        if not s and not f:
            conn.execute("DELETE FROM dates WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO dates (key, started, finished) VALUES (?, ?, ?)",
                (key, s.isoformat() if s else None, f.isoformat() if f else None),
            )


def load_meta(conn: sqlite3.Connection) -> Meta:
    return Meta(
        hidden=hidden_keys(conn),
        tags=all_tags(conn),
        status=all_status(conn),
        ratings=all_ratings(conn),
        developers=all_developers(conn),
        playtimes=all_playtimes(conn),
        notes=all_notes(conn),
        covers=all_covers(conn),
        dates=all_dates(conn),
        splits=split_keys(conn),
    )


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
