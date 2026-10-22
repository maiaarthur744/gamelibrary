import json
import os
from datetime import datetime, timezone

import httpx

from .. import net, store
from ..models import Game, clean_year

CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"
STORE_ITEMS = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1"
ASSETS = "https://shared.akamai.steamstatic.com/store_item_assets"
URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"


Info = tuple[str | None, str | None, int | None]  # (cover, developer, release year)


def _known_info() -> dict[int, Info]:
    """What earlier syncs learned from the store: appid -> (cover, developer, release year).

    A game counts as already asked about once its release year is stored, even as 0 (the store had none).
    Rows asked about before years existed are re-asked once. What they already know is kept if that fails.
    """
    rows = store.connect().execute(
        "SELECT platform_id, cover_url, developer, release_year FROM games"
        " WHERE platform = 'steam' AND source = 'api'"  # not the ones added by hand
    )
    return {int(r["platform_id"]): (r["cover_url"], r["developer"], r["release_year"]) for r in rows if r["platform_id"].isdigit()}


def _release_year(release: dict | None) -> int:
    """The earliest plausible year among the original and the Steam release dates (unix timestamps), else 0."""
    years = []
    for field in ("original_release_date", "steam_release_date"):
        stamp = (release or {}).get(field)
        if isinstance(stamp, (int, float)) and stamp > 0:
            try:
                years.append(clean_year(datetime.fromtimestamp(stamp, timezone.utc).year))
            except (OverflowError, OSError, ValueError):
                pass
    return min((y for y in years if y), default=0)


def _store_info(appids: list[int]) -> dict[int, Info]:
    """(portrait cover, developer, release year) per game from the store API, 50 games per request.

    Newer apps keep their art under hashed paths, so the plain CDN URL 404s for them. A game the store
    doesn't know comes back as (None, "", 0), so it isn't asked about again on every sync.
    """
    info: dict[int, Info] = {}
    for i in range(0, len(appids), 50):
        batch = appids[i : i + 50]
        payload = {
            "ids": [{"appid": a} for a in batch],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_assets": True, "include_basic_info": True, "include_release": True},
        }
        try:
            resp = net.get(STORE_ITEMS, params={"input_json": json.dumps(payload)})
            resp.raise_for_status()
            items = resp.json()["response"]["store_items"]
        except (httpx.HTTPError, KeyError, ValueError):
            continue  # this batch stays unasked, so the next sync tries again
        found: dict[int, Info] = {}
        for item in items:
            if not item.get("appid"):  # unknown apps come back as an empty item with appid 0
                continue
            assets = item.get("assets") or {}
            template, capsule = assets.get("asset_url_format"), assets.get("library_capsule")
            cover = f"{ASSETS}/" + template.replace("${FILENAME}", capsule) if template and capsule else None
            developers = [d["name"] for d in (item.get("basic_info") or {}).get("developers", []) if d.get("name")]
            found[item["appid"]] = (cover, ", ".join(developers), _release_year(item.get("release")))
        for appid in batch:
            info[appid] = found.get(appid, (None, "", 0))
    return info


def fetch_games(refresh_covers: bool = False) -> list[Game]:
    key = os.environ.get("STEAM_API_KEY")
    steam_id = os.environ.get("STEAM_ID")
    if not key or not steam_id:
        raise RuntimeError("Set STEAM_API_KEY and STEAM_ID in .env")

    resp = net.get(
        URL,
        params={
            "key": key,
            "steamid": steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        },
    )
    resp.raise_for_status()
    games = resp.json().get("response", {}).get("games")
    if games is None:
        raise RuntimeError(
            "Steam returned no games. Is your profile's 'Game details' set to public?"
        )

    # Only games the store was never asked about are looked up: usually none, so a sync is one request.
    known = {} if refresh_covers else _known_info()
    asked = {appid for appid, (_, _, year) in known.items() if year is not None}
    info = known | _store_info([g["appid"] for g in games if g["appid"] not in asked])

    result = []
    for g in games:
        appid = g["appid"]
        cover, developer, year = info.get(appid, (None, None, None))  # None: the store lookup failed this time
        result.append(
            Game(
                platform="steam",
                platform_id=str(appid),
                title=g.get("name") or f"App {appid}",
                playtime_minutes=g.get("playtime_forever"),
                last_played=g.get("rtime_last_played") or None,
                url=f"https://store.steampowered.com/app/{appid}",
                cover_url=cover or f"{CDN}/{appid}/library_600x900.jpg",
                developer=developer,
                release_year=year,
            )
        )
    return result
