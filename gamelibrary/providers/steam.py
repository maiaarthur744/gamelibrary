import json
import os

import httpx

from .. import net, store
from ..models import Game

CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"
STORE_ITEMS = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1"
ASSETS = "https://shared.akamai.steamstatic.com/store_item_assets"
URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"


def _known_info() -> dict[int, tuple[str | None, str]]:
    """What earlier syncs learned from the store: appid -> (cover, developer).

    A game counts as already asked about once its developer is stored, even as "" (the store had none).
    """
    rows = store.connect().execute(
        "SELECT platform_id, cover_url, developer FROM games WHERE platform = 'steam' AND developer IS NOT NULL"
    )
    return {int(r["platform_id"]): (r["cover_url"], r["developer"]) for r in rows}


def _store_info(appids: list[int]) -> dict[int, tuple[str | None, str]]:
    """(portrait cover, developer) per game from the store API, 50 games per request.

    Newer apps keep their art under hashed paths, so the plain CDN URL 404s for them. A game the store
    doesn't know comes back as (None, ""), so it isn't asked about again on every sync.
    """
    info: dict[int, tuple[str | None, str]] = {}
    for i in range(0, len(appids), 50):
        batch = appids[i : i + 50]
        payload = {
            "ids": [{"appid": a} for a in batch],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_assets": True, "include_basic_info": True},
        }
        try:
            resp = net.get(STORE_ITEMS, params={"input_json": json.dumps(payload)})
            resp.raise_for_status()
            items = resp.json()["response"]["store_items"]
        except (httpx.HTTPError, KeyError, ValueError):
            continue  # this batch stays unasked, so the next sync tries again
        found: dict[int, tuple[str | None, str]] = {}
        for item in items:
            if not item.get("appid"):  # unknown apps come back as an empty item with appid 0
                continue
            assets = item.get("assets") or {}
            template, capsule = assets.get("asset_url_format"), assets.get("library_capsule")
            cover = f"{ASSETS}/" + template.replace("${FILENAME}", capsule) if template and capsule else None
            developers = [d["name"] for d in (item.get("basic_info") or {}).get("developers", []) if d.get("name")]
            found[item["appid"]] = (cover, ", ".join(developers))
        for appid in batch:
            info[appid] = found.get(appid, (None, ""))
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
    info = known | _store_info([g["appid"] for g in games if g["appid"] not in known])

    result = []
    for g in games:
        appid = g["appid"]
        cover, developer = info.get(appid, (None, None))  # developer None: the store lookup failed this time
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
            )
        )
    return result
