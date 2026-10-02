import json
import sqlite3
import uuid

from .. import store
from ..models import Game
from ..store import DATA_DIR

MANUAL_PATH = DATA_DIR / "manual.json"
# Spellings people naturally type, mapped to the names the interface already knows.
ALIASES = {"battle.net": "battlenet", "battle net": "battlenet", "amazon games": "amazon", "amazon prime gaming": "amazon"}
# Stores that sync from their own API: a manual entry here would be wiped by (or wipe) the real sync.
RESERVED = {"steam", "gog", "epic"}


def load_entries() -> list[dict]:
    if not MANUAL_PATH.exists():
        return []
    return json.loads(MANUAL_PATH.read_text())


def _save(entries: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = MANUAL_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(MANUAL_PATH)


def entry_id(entry: dict) -> str:
    return str(entry.get("platform_id") or entry["title"])


def entry_keys() -> set[str]:
    return {store.entry_key(e["platform"], entry_id(e)) for e in load_entries()}


def _http_url(value: str | None, what: str) -> str | None:
    value = (value or "").strip()
    if value and not value.startswith(("http://", "https://", "/covers/")):
        raise ValueError(f"{what} precisa começar com http:// ou https://")
    return value or None


def add_entry(platform: str, title: str, cover_url=None, playtime_hours=None, url=None) -> dict:
    platform = " ".join(str(platform).lower().split())[:30]
    platform = ALIASES.get(platform, platform)
    title = " ".join(str(title).split())[:200]
    if not platform or not title:
        raise ValueError("informe a plataforma e o título")
    if platform in RESERVED:
        raise ValueError(f"{platform} é sincronizada automaticamente; use o sync em vez de adicionar à mão")
    entries = load_entries()
    if any(e["platform"].lower() == platform and e["title"].lower() == title.lower() for e in entries):
        raise ValueError("esse jogo já foi adicionado nessa plataforma")
    entry = {"platform": platform, "platform_id": uuid.uuid4().hex[:12], "title": title}
    if cover := _http_url(cover_url, "a capa"):
        entry["cover_url"] = cover
    if url := _http_url(url, "o link"):
        entry["url"] = url
    if playtime_hours not in (None, ""):
        hours = float(playtime_hours)
        if hours < 0:
            raise ValueError("as horas jogadas não podem ser negativas")
        entry["playtime_minutes"] = round(hours * 60)
    _save(entries + [entry])
    return entry


def remove_entry(platform: str, platform_id: str) -> None:
    entries = load_entries()
    kept = [e for e in entries if not (e["platform"] == platform and entry_id(e) == platform_id)]
    if len(kept) == len(entries):
        raise ValueError("jogo manual não encontrado")
    _save(kept)


def fetch_games() -> list[Game]:
    """Games added by hand (Battle.net, Amazon Games, ...) from data/manual.json."""
    games = []
    for e in load_entries():
        if e["platform"].lower() in RESERVED:
            raise ValueError(
                f"manual.json: '{e['platform']}' é sincronizada automaticamente; "
                "use outro nome de plataforma para jogos manuais"
            )
        games.append(
            Game(
                platform=e["platform"],
                platform_id=entry_id(e),
                title=e["title"],
                playtime_minutes=e.get("playtime_minutes"),
                url=e.get("url"),
                cover_url=e.get("cover_url"),
                developer=e.get("developer") or "",
            )
        )
    return games


def sync(conn: sqlite3.Connection) -> int:
    """Make the database match manual.json, including platforms whose games were all removed."""
    games = fetch_games()
    known = {r["platform"] for r in conn.execute("SELECT DISTINCT platform FROM games")}
    # Every platform that isn't synced from an API is manual, so it is rewritten (or emptied) here.
    for platform in ({g.platform for g in games} | known) - RESERVED:
        store.replace_platform(conn, platform, [g for g in games if g.platform == platform])
    return len(games)
