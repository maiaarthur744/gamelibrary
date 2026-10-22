from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Game:
    platform: str
    platform_id: str
    title: str
    playtime_minutes: int | None = None
    last_played: int | None = None  # unix timestamp
    url: str | None = None
    cover_url: str | None = None  # portrait cover when the platform has one
    developer: str | None = None  # None = never looked up; "" = looked up, nothing found
    release_year: int | None = None  # None = never looked up; 0 = looked up, nothing usable found


def clean_year(value) -> int:
    """A plausible release year, or 0. Store data is sometimes missing or a placeholder."""
    try:
        year = int(value)
    except (TypeError, ValueError):
        return 0
    return year if 1950 <= year <= date.today().year + 5 else 0


@dataclass
class Meta:
    """Everything the user attaches to games (keyed by group key unless noted)."""

    hidden: set[str] = field(default_factory=set)
    tags: dict[str, list[str]] = field(default_factory=dict)
    status: dict[str, list[str]] = field(default_factory=dict)
    ratings: dict[str, int] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    covers: dict[str, str] = field(default_factory=dict)  # custom cover per group
    dates: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)  # (started, finished)
    developers: dict[str, str] = field(default_factory=dict)  # typed by the user, wins over the automatic one
    release_years: dict[str, int] = field(default_factory=dict)  # likewise
    playtimes: dict[str, int] = field(default_factory=dict)  # minutes typed by the user, wins over the automatic sum
    splits: set[str] = field(default_factory=set)  # entry keys
