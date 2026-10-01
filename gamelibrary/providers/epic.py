import json
import subprocess
import sys
from pathlib import Path

from ..models import Game

# Legendary is installed in the same environment as this package.
LEGENDARY = str(Path(sys.executable).parent / "legendary")


def login() -> None:
    """Interactive: opens the Epic login page and asks for the authorization code."""
    subprocess.run([LEGENDARY, "auth"], check=True)


def fetch_games() -> list[Game]:
    proc = subprocess.run(
        [LEGENDARY, "list", "--json", "--platform", "Windows", "--include-noasset"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "legendary failed (not logged in? run: gamelibrary epic-login)\n" + proc.stderr.strip()
        )
    return [
        Game(
            platform="epic",
            platform_id=g["app_name"],
            title=g["app_title"],
            cover_url=_cover(g),
        )
        for g in json.loads(proc.stdout)
    ]


def _cover(game: dict) -> str | None:
    for image in (game.get("metadata") or {}).get("keyImages", []):
        if image["type"] == "DieselGameBoxTall":
            # Originals are ~500 KB; the CDN can resize on the fly.
            return f"{image['url']}?h=400&resize=1&w=300"
    return None
