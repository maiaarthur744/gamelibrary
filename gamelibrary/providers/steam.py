import json
import os

import httpx

from ..models import Game

CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"
STORE_ITEMS = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1"
ASSETS = "https://shared.akamai.steamstatic.com/store_item_assets"
URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"


def _store_covers(appids: list[int]) -> dict[int, str]:
    """Portrait covers from the store API.

    Newer apps keep their art under hashed paths, so the plain CDN URL 404s for them.
    """
    covers: dict[int, str] = {}
    for i in range(0, len(appids), 50):
        payload = {
            "ids": [{"appid": a} for a in appids[i : i + 50]],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_assets": True},
        }
        try:
            resp = httpx.get(STORE_ITEMS, params={"input_json": json.dumps(payload)}, timeout=30)
            resp.raise_for_status()
            items = resp.json()["response"]["store_items"]
        except (httpx.HTTPError, KeyError, ValueError):
            continue  # this batch falls back to the plain CDN URL
        for item in items:
            assets = item.get("assets") or {}
            template, capsule = assets.get("asset_url_format"), assets.get("library_capsule")
            if template and capsule and item.get("appid"):
                covers[item["appid"]] = f"{ASSETS}/" + template.replace("${FILENAME}", capsule)
    return covers


def fetch_games() -> list[Game]:
    key = os.environ.get("STEAM_API_KEY")
    steam_id = os.environ.get("STEAM_ID")
    if not key or not steam_id:
        raise RuntimeError("Set STEAM_API_KEY and STEAM_ID in .env")

    resp = httpx.get(
        URL,
        params={
            "key": key,
            "steamid": steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    games = resp.json().get("response", {}).get("games")
    if games is None:
        raise RuntimeError(
            "Steam returned no games. Is your profile's 'Game details' set to public?"
        )

    store_covers = _store_covers([g["appid"] for g in games])
    return [
        Game(
            platform="steam",
            platform_id=str(g["appid"]),
            title=g.get("name") or f"App {g['appid']}",
            playtime_minutes=g.get("playtime_forever"),
            last_played=g.get("rtime_last_played") or None,
            url=f"https://store.steampowered.com/app/{g['appid']}",
            cover_url=store_covers.get(g["appid"]) or f"{CDN}/{g['appid']}/library_600x900.jpg",
        )
        for g in games
    ]
