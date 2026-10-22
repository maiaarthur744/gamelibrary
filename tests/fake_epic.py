"""A fake Epic Games backend for the REAL Legendary, so the first-sync flow can be tested without a network.

It answers the handful of requests `legendary list --json --include-noasset` makes, from inside the process
(by replacing requests' Session.request), then runs Legendary through gamelibrary's logging wrapper exactly as
`gamelibrary sync epic` does. Configure with environment variables:

    LEGENDARY_CONFIG_PATH  where Legendary keeps its cache (a temp folder: it is what makes a run cold or warm)
    FAKE_EPIC_GAMES        games with installable assets (default 214)
    FAKE_EPIC_LIBRARY      third-party library items without assets (default 30)
    GAMELIBRARY_EPIC_BUDGET / GAMELIBRARY_EPIC_INTERVAL   as in the real wrapper
"""
import json
import os
import sys
from urllib.parse import urlsplit

import requests

GAMES = int(os.environ.get("FAKE_EPIC_GAMES", "214"))
LIBRARY = int(os.environ.get("FAKE_EPIC_LIBRARY", "30"))


def _catalog_item(title: str) -> dict:
    return {"title": title, "developer": "Fake Studio", "categories": [{"path": "games"}, {"path": "applications"}],
            "keyImages": [{"type": "DieselGameBoxTall", "url": "https://cdn.example/cover.jpg"}]}


ASSETS = [{"appName": f"App{i:04d}", "assetId": f"asset{i}", "buildVersion": "1.0", "catalogItemId": f"cat{i}",
           "labelName": "Live-Windows", "namespace": f"ns{i}", "metadata": {}} for i in range(GAMES)]
CATALOG = {f"cat{i}": _catalog_item(f"Game {i}") for i in range(GAMES)}
LIBRARY_ITEMS = [{"namespace": f"lns{i}", "appName": f"Lib{i:03d}", "catalogItemId": f"lcat{i}", "sandboxType": "PUBLIC"}
                 for i in range(LIBRARY)]
CATALOG.update({f"lcat{i}": _catalog_item(f"Third Party {i}") for i in range(LIBRARY)})


def respond(body, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode()
    r.headers["Content-Type"] = "application/json"
    r.encoding = "utf-8"
    return r


def fake_request(self, method, url, params=None, json=None, **kwargs):  # replaces requests.Session.request
    path = urlsplit(str(url)).path
    params = params or {}
    if path == "/account/api/oauth/verify":
        return respond({"token": "t", "account_id": "acc", "expires_in": 7000, "expires_at": "2099-01-01T00:00:00.000Z",
                        "displayName": "tester", "app": "launcher"})
    if path == "/v1/version.json":  # Legendary's own update check
        return respond({"release_info": {"version": "0.0.1"}})
    if path == "/launcher/api/public/assets/Windows":
        return respond(ASSETS)
    if path.startswith("/entitlement/api/account/"):
        return respond([])
    if path.endswith("/bulk/items"):
        item_id = params["id"]
        return respond({item_id: CATALOG[item_id]})
    if path == "/graphql":
        return respond({"data": {"Achievement": {"productAchievementsRecordBySandbox": {"sandboxId": "x", "totalAchievements": 0}}}})
    if path == "/library/api/public/items":
        half = len(LIBRARY_ITEMS) // 2
        if params.get("cursor"):
            return respond({"records": LIBRARY_ITEMS[half:], "responseMetadata": {}})
        return respond({"records": LIBRARY_ITEMS[:half], "responseMetadata": {"nextCursor": "page2"}})
    return respond({"errorMessage": f"fake epic: no route for {path}"}, 404)


def main() -> None:
    config = os.environ["LEGENDARY_CONFIG_PATH"]
    os.makedirs(config, exist_ok=True)
    user = os.path.join(config, "user.json")
    if not os.path.exists(user):
        with open(user, "w", encoding="utf-8") as f:
            json.dump({"access_token": "t", "refresh_token": "r", "expires_at": "2099-01-01T00:00:00.000Z",
                       "refresh_expires_at": "2099-01-01T00:00:00.000Z", "account_id": "acc", "displayName": "tester"}, f)
    requests.sessions.Session.request = fake_request  # innermost: the wrapper below logs and counts on top of it
    from gamelibrary import legendary_logged
    legendary_logged.install(int(os.environ.get("GAMELIBRARY_EPIC_BUDGET", "2000")))
    from legendary.cli import main as legendary_main

    from gamelibrary.providers import epic

    sys.argv = ["legendary", *epic.LIST_ARGS]  # the very arguments the app passes
    legendary_main()


if __name__ == "__main__":
    main()
