from . import epic, gog, manual, steam

PROVIDERS = {
    "steam": steam.fetch_games,
    "gog": gog.fetch_games,
    "epic": epic.fetch_games,
    "manual": manual.fetch_games,
}
