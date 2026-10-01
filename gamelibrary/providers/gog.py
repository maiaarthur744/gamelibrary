import json
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..models import Game
from ..store import DATA_DIR

# Public client credentials used by GOG Galaxy itself.
CLIENT_ID = "46899977096215655"
CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"
TOKEN_URL = "https://auth.gog.com/token"
LIBRARY_URL = "https://embed.gog.com/account/getFilteredProducts"
TOKEN_PATH = DATA_DIR / "gog_token.json"


def login_url() -> str:
    query = urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "layout": "client2",
        }
    )
    return f"https://auth.gog.com/auth?{query}"


def _extract_code(value: str) -> str:
    """Accept either the raw code or the full redirect URL."""
    value = value.strip()
    if value.startswith("http"):
        codes = parse_qs(urlparse(value).query).get("code")
        if not codes:
            raise ValueError("No 'code' parameter found in that URL")
        return codes[0]
    return value


def _save_token(data: dict) -> dict:
    token = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": int(time.time()) + int(data.get("expires_in", 3600)) - 60,
    }
    DATA_DIR.mkdir(exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(token))
    TOKEN_PATH.chmod(0o600)
    return token


def _token_request(**params: str) -> dict:
    resp = httpx.get(
        TOKEN_URL,
        params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **params},
        timeout=30,
    )
    resp.raise_for_status()
    return _save_token(resp.json())


def login(code_or_url: str) -> None:
    _token_request(
        grant_type="authorization_code",
        code=_extract_code(code_or_url),
        redirect_uri=REDIRECT_URI,
    )


def _access_token() -> str:
    if not TOKEN_PATH.exists():
        raise RuntimeError("Not logged in to GOG. Run: gamelibrary gog-login")
    token = json.loads(TOKEN_PATH.read_text())
    if token["expires_at"] <= time.time():
        token = _token_request(grant_type="refresh_token", refresh_token=token["refresh_token"])
    return token["access_token"]


def fetch_games() -> list[Game]:
    headers = {"Authorization": f"Bearer {_access_token()}"}
    games: list[Game] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        resp = httpx.get(
            LIBRARY_URL,
            params={"mediaType": 1, "page": page},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        total_pages = data.get("totalPages", 1)
        for p in data.get("products", []):
            games.append(
                Game(
                    platform="gog",
                    platform_id=str(p["id"]),
                    title=p["title"],
                    url=f"https://www.gog.com{p['url']}" if p.get("url") else None,
                    cover_url=f"https:{p['image']}_glx_vertical_cover.webp" if p.get("image") else None,
                )
            )
        page += 1
    return games
