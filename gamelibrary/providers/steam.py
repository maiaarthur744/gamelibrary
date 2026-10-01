import os

import httpx

from ..models import Game

CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"
URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"


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

    return [
        Game(
            platform="steam",
            platform_id=str(g["appid"]),
            title=g.get("name") or f"App {g['appid']}",
            playtime_minutes=g.get("playtime_forever"),
            last_played=g.get("rtime_last_played") or None,
            url=f"https://store.steampowered.com/app/{g['appid']}",
            cover_url=f"{CDN}/{g['appid']}/library_600x900.jpg",
        )
        for g in games
    ]
