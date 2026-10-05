"""Group the same game owned on several platforms into one entry."""
import json
import re
import unicodedata
from collections import defaultdict

from .models import Meta
from .providers import gog
from .store import DATA_DIR, entry_key

ALIASES_PATH = DATA_DIR / "aliases.json"

# Trailing edition markers that don't make a different game ("X: Game of the Year Edition" == "X").
# "Remastered" is deliberately absent: those are usually separate purchases.
_EDITION_SUFFIXES = [
    re.compile(r" (game of the year|goty)( edition)?( deluxe)?$"),
    # Needs the word "edition"/"collection": a bare "Gold" or "Anniversary" is often a different
    # game ("Thief Gold" is the 1998 original, "Tomb Raider: Anniversary" a 2007 remake).
    re.compile(r" (complete|definitive|enhanced|ultimate|gold|deluxe|standard|special|anniversary|extended|premium)( plus)? (edition|collection)$"),
    re.compile(r" complete$"),
    re.compile(r" directors cut$"),
    re.compile(r" edition$"),
]


# Platforms that can offer a second look at an entry's cover (see gog.alt_cover).
_ALT_COVERS = {"gog": gog.alt_cover}


def _clean_title(title: str) -> str:
    return re.sub(r"[™®©]", "", title).strip()


def normalize(title: str, strip_editions: bool = True) -> str:
    t = re.sub(r"[™®©'’‘`]", "", title)  # before NFKD, which would turn ™ into "TM"
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace("&", " and ")
    t = re.sub(r"\(\d{4}\)", " ", t)  # "(2009)" year tags
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    t = re.sub(r"^the ", "", t)
    changed = strip_editions
    while changed:
        changed = False
        for pattern in _EDITION_SUFFIXES:
            stripped = pattern.sub("", t)
            if stripped != t and stripped:
                t, changed = stripped, True
    return t.replace(" ", "")


def strict_key(title: str) -> str:
    """Like normalize() but keeps edition words: identifies one exact title across stores."""
    return normalize(title, strip_editions=False)


def _load_aliases() -> dict[str, str]:
    """data/aliases.json: {"Title as it appears": "Title it should be grouped with"}."""
    if not ALIASES_PATH.exists():
        return {}
    raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    return {normalize(k): normalize(v) for k, v in raw.items()}


def group_games(rows, meta: Meta | None = None) -> list[dict]:
    meta = meta or Meta()
    aliases = _load_aliases()
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        if entry_key(r["platform"], r["platform_id"]) in meta.splits:
            # Pulled out of its auto-group by the user. Licenses split from the same title
            # (e.g. on two stores) still end up together in the new group.
            buckets["=" + strict_key(r["title"])].append(r)
        else:
            key = normalize(r["title"])
            buckets[aliases.get(key, key)].append(r)

    groups = []
    for key, entries in buckets.items():
        entries.sort(key=lambda e: e["platform"])
        playtimes = [e["playtime_minutes"] for e in entries if e["playtime_minutes"]]
        played = [e["last_played"] for e in entries if e["last_played"]]
        started, finished = meta.dates.get(key, (None, None))
        auto_minutes = sum(playtimes) if playtimes else None
        typed_minutes = meta.playtimes.get(key)
        auto_developer = next((e["developer"] for e in entries if e["developer"]), "")
        covers = [e["cover_url"] for e in entries if e["cover_url"]]
        if key in meta.covers:  # the user's own cover goes first; automatic ones stay as fallback
            covers.insert(0, meta.covers[key])
        alternatives = [
            alt
            for e in entries
            if e["cover_url"] and e["platform"] in _ALT_COVERS and (alt := _ALT_COVERS[e["platform"]](e["cover_url"]))
        ]
        groups.append(
            {
                "key": key,
                "hidden": key in meta.hidden,
                "tags": meta.tags.get(key, []),
                "statuses": meta.status.get(key, []),
                "rating": meta.ratings.get(key),
                "note": meta.notes.get(key, ""),
                "started": started,
                "finished": finished,
                "custom_cover": key in meta.covers,
                "title": min((_clean_title(e["title"]) for e in entries), key=lambda s: (len(s), s)),
                "platforms": sorted({e["platform"] for e in entries}),
                "playtime_minutes": typed_minutes if typed_minutes is not None else auto_minutes,
                "playtime_auto": auto_minutes,
                "playtime_custom": typed_minutes is not None,
                "developer": meta.developers.get(key) or auto_developer,
                "developer_auto": auto_developer,
                "developer_custom": key in meta.developers,
                "last_played": max(played) if played else None,
                "covers": covers,
                "alt_covers": [u for u in dict.fromkeys(alternatives) if u not in covers],
                "entries": [
                    {
                        "platform": e["platform"],
                        "title": e["title"],
                        "platform_id": e["platform_id"],
                        "split": entry_key(e["platform"], e["platform_id"]) in meta.splits,
                        "manual": e["source"] == "manual",
                        "playtime_minutes": e["playtime_minutes"],
                        "url": e["url"],
                    }
                    for e in entries
                ],
            }
        )
    groups.sort(key=lambda g: g["title"].lower())
    return groups
