import json

from ..models import Game
from ..store import DATA_DIR

MANUAL_PATH = DATA_DIR / "manual.json"


def fetch_games() -> list[Game]:
    """Games added by hand (Battle.net, Amazon Games, ...) from data/manual.json."""
    if not MANUAL_PATH.exists():
        return []
    entries = json.loads(MANUAL_PATH.read_text())
    return [
        Game(
            platform=e["platform"],
            platform_id=str(e.get("platform_id") or e["title"]),
            title=e["title"],
            playtime_minutes=e.get("playtime_minutes"),
            url=e.get("url"),
            cover_url=e.get("cover_url"),
        )
        for e in entries
    ]
