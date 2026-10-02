from dataclasses import dataclass, field


@dataclass(frozen=True)
class Game:
    platform: str
    platform_id: str
    title: str
    playtime_minutes: int | None = None
    last_played: int | None = None  # unix timestamp
    url: str | None = None
    cover_url: str | None = None  # portrait cover when the platform has one


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
    splits: set[str] = field(default_factory=set)  # entry keys
    manual: set[str] = field(default_factory=set)  # entry keys of licenses added by hand
