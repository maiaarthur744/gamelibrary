import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .. import net
from ..models import Game

# Legendary runs through a thin wrapper that logs, spaces out and caps its requests (see legendary_logged.py).
LEGENDARY = [sys.executable, "-m", "gamelibrary.legendary_logged"]
# --third-party also lists games that can't be installed through Epic (claimed on Ubisoft, EA...). Legendary
# silently ignores options it doesn't know, so the spelling matters: tests/test_epic_first_sync.py checks it.
LIST_ARGS = ["list", "--json", "--platform", "Windows", "--third-party"]


def _env() -> dict[str, str]:
    # Legendary prints UTF-8. On Windows the default for pipes is cp1252, which garbles titles like "Batman™".
    return {**os.environ, "GAMELIBRARY_EPIC_BUDGET": str(net.remaining()), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _cached_metadata() -> int:
    """How many games Legendary already has cached: a rough sign of whether this is the first sync. -1 = unknown."""
    base = os.environ.get("LEGENDARY_CONFIG_PATH") or (
        os.path.join(os.environ["XDG_CONFIG_HOME"], "legendary")
        if os.environ.get("XDG_CONFIG_HOME")
        else os.path.expanduser("~/.config/legendary")
    )
    try:
        return sum(1 for _ in Path(base, "metadata").glob("*.json"))
    except OSError:
        return -1


def first_sync_notice() -> str | None:
    cached = _cached_metadata()
    if 0 <= cached < 20:
        return ("[net] epic   primeira sincronização: o Legendary busca os dados de cada jogo (cerca de 2 chamadas "
                "por jogo, espaçadas). Pode levar alguns minutos; deixe terminar, é só desta vez.")
    return None


def login() -> None:
    """Interactive: opens the Epic login page and asks for the authorization code."""
    net.check_allowed("legendary auth")
    subprocess.run([*LEGENDARY, "auth"], check=True, env=_env())


def _run(args: list[str]) -> tuple[int, str, list[str], bool]:
    """Run Legendary, showing its request log live. Returns (exit code, stdout, other stderr lines, hit the cap)."""
    net.check_allowed("legendary " + " ".join(args))
    proc = subprocess.Popen(
        [*LEGENDARY, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=_env(),
    )
    out: list[str] = []
    reader = threading.Thread(target=lambda: out.append(proc.stdout.read()))
    reader.start()
    other, blocked = [], False
    for line in proc.stderr:
        line = line.rstrip()
        if line.startswith("[net] "):
            print(line, file=sys.stderr, flush=True)
            if "BLOQUEADA" in line:
                blocked = True
            else:
                net.note_external()
        else:
            other.append(line)
    code = proc.wait()
    reader.join()
    return code, "".join(out), other, blocked


def fetch_games() -> list[Game]:
    if notice := first_sync_notice():
        print(notice, file=sys.stderr, flush=True)
    code, stdout, other, blocked = _run(LIST_ARGS)
    if blocked:
        raise net.BudgetExceeded(
            "epic: o teto de segurança de chamadas deste sync foi atingido. O progresso foi salvo; "
            "rode `gamelibrary sync epic` de novo para continuar."
        )
    if code != 0:
        raise RuntimeError("legendary failed (not logged in? run: gamelibrary epic-login)\n" + "\n".join(other[-10:]))
    return [
        Game(
            platform="epic",
            platform_id=g["app_name"],
            title=g["app_title"],
            cover_url=_cover(g),
            developer=(g.get("metadata") or {}).get("developer") or "",
        )
        for g in json.loads(stdout)
    ]


def _cover(game: dict) -> str | None:
    for image in (game.get("metadata") or {}).get("keyImages", []):
        if image["type"] == "DieselGameBoxTall":
            # Originals are ~500 KB; the CDN can resize on the fly.
            return f"{image['url']}?h=400&resize=1&w=300"
    return None
