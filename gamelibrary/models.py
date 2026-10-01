from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    platform: str
    platform_id: str
    title: str
    playtime_minutes: int | None = None
    last_played: int | None = None  # unix timestamp
    url: str | None = None
    cover_url: str | None = None  # portrait cover when the platform has one
