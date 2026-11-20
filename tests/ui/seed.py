"""Seeds a data folder (GAMELIBRARY_DATA_DIR) for the UI tests.

    seed.py            the games the stores would return, nothing else (a computer that only ran `sync`)
    seed.py --organized   the same games plus statuses, tags, ratings, a manual game, aliases...
"""
import json
import sys

from gamelibrary import grouping, store
from gamelibrary.models import Game
from gamelibrary.providers import manual

conn = store.connect()
store.replace_platform(conn, "steam", [Game("steam", "1", "Portal 2", developer="Valve", release_year=2011),
                                       Game("steam", "2", "Hades"), Game("steam", "3", "Hollow Knight")])
store.replace_platform(conn, "gog", [Game("gog", "9", "Fallout", release_year=1997), Game("gog", "8", "Gothic")])
store.replace_platform(conn, "epic", [Game("epic", "e1", "Alan Wake", developer="Remedy")])

if "--organized" in sys.argv:
    store.set_status(conn, "portal2", ["played", "multiplayer"])
    store.set_tags(conn, "portal2", ["puzzle", "coop"])
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
    manual.save_entries([{"platform": "battlenet", "title": "Diablo IV", "cover_url": "https://example.com/d4.jpg"}])
    manual.sync(conn)
    store.set_status(conn, "diabloiv", ["playing"])
    grouping.ALIASES_PATH.write_text(json.dumps([{"Old A": "Old B"}]), encoding="utf-8")
