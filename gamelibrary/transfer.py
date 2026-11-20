"""Export and import of the organization you built: status, tags, notes, ratings, developers, years, hours,
dates, cover links and hidden games, plus the games you added by hand, the splits and the title aliases.

The games the stores return are NOT part of it: each computer gets its own with `sync`. Everything here is
keyed by the group key (derived from the title, the same on any machine), so an import works before or after
a sync. An import is all-or-nothing, runs after an automatic backup, and can be previewed first: the preview
runs the very same code on an in-memory copy of the database.
"""
import copy
import json
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from . import store
from .grouping import _alias_pairs, _build_aliases, group_games, normalize
from .providers import manual

FORMAT = "gamelibrary-export"
VERSION = 1
MODES = ("fill", "merge", "overwrite")
MAX_GROUPS = 50_000
_lock = threading.Lock()  # one import at a time


# --------------------------------------------------------------------------- validation
# These mirror what store.set_* accepts (tests/test_transfer.py checks they agree), but never write.
def check_statuses(value) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("statuses must be a list")
    unknown = [s for s in value if s not in store.STATUSES]
    if unknown:
        raise ValueError(f"unknown status {unknown[0]!r}")
    return [s for s in store.STATUSES if s in value]


def check_tags(value) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("tags must be a list")
    seen: dict[str, str] = {}
    for tag in value:
        tag = " ".join(str(tag).split())[:40]
        if tag:
            seen.setdefault(tag.lower(), tag)
    return sorted(list(seen.values())[:30], key=str.lower)


def check_rating(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
        raise ValueError("rating must be a whole number from 1 to 10")
    return value


def check_note(value) -> str:
    if not isinstance(value, str):
        raise TypeError("note must be a string")
    return value.strip()[:20000]


def check_developer(value) -> str:
    if not isinstance(value, str):
        raise TypeError("developer must be a string")
    return " ".join(value.split())[:200]


def check_year(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1950 <= value <= 2100:
        raise ValueError("year must be a whole number from 1950 to 2100")
    return value


def check_minutes(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100000 * 60:
        raise ValueError("minutes out of range")
    return round(value)


def check_date(value) -> str:
    if not isinstance(value, str):
        raise TypeError("date must be a string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("invalid date") from None


def check_cover(value) -> str:
    # Uploaded images (/covers/...) are not exported, so they are not accepted either.
    if not isinstance(value, str) or len(value) > 2000 or not value.startswith(("http://", "https://")):
        raise ValueError("cover must be an http(s) link")
    return value


def _url_or_none(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else ""
    return value if value.startswith(("http://", "https://")) and len(value) <= 2000 else None


# --------------------------------------------------------------------------- export
def _organization(key: str, meta, title: str) -> dict:
    """What the user typed for one game. Automatic values (from the stores) are not exported."""
    record = {"key": key, "title": title}
    if meta.status.get(key):
        record["statuses"] = list(meta.status[key])
    if meta.tags.get(key):
        record["tags"] = list(meta.tags[key])
    if key in meta.ratings:
        record["rating"] = meta.ratings[key]
    if meta.notes.get(key):
        record["note"] = meta.notes[key]
    if key in meta.developers:
        record["developer"] = meta.developers[key]
    if key in meta.release_years:
        record["release_year"] = meta.release_years[key]
    if key in meta.playtimes:
        record["playtime_minutes"] = meta.playtimes[key]
    started, finished = meta.dates.get(key, (None, None))
    if started:
        record["started"] = started
    if finished:
        record["finished"] = finished
    if key in meta.covers:
        record["cover"] = meta.covers[key]
    if key in meta.hidden:
        record["hidden"] = True
    return record


def export_data(conn: sqlite3.Connection, *, keys=None, platforms=None, statuses=None, tags=None,
                include_aliases: bool = True) -> dict:
    """Build the export. `keys` picks games explicitly; otherwise platforms/statuses/tags filter.

    Within one filter any value matches; across filters all must match.
    """
    keys = set(keys) if keys is not None else None
    rows = conn.execute("SELECT * FROM games").fetchall()
    meta = store.load_meta(conn)
    groups = {g["key"]: g for g in group_games(rows, meta)}
    organized = (set(meta.status) | set(meta.tags) | set(meta.ratings) | set(meta.notes) | set(meta.developers)
                 | set(meta.release_years) | set(meta.playtimes) | set(meta.dates) | set(meta.covers) | meta.hidden)
    candidates = set(groups) | organized

    def matches(key: str) -> bool:
        if keys is not None:
            return key in keys
        group = groups.get(key)
        if platforms and not (group and set(group["platforms"]) & set(platforms)):
            return False
        if statuses and not set(meta.status.get(key, [])) & set(statuses):
            return False
        return not tags or bool({t.lower() for t in meta.tags.get(key, [])} & {t.lower() for t in tags})

    selected = sorted(k for k in candidates if matches(k))
    out_groups, skipped_uploads = [], 0
    manual_entries = {(e["platform"], manual.entry_id(e)): e for e in manual.load_entries()}
    manual_out, splits = [], []
    for key in selected:
        group = groups.get(key)
        record = _organization(key, meta, group["title"] if group else key)
        if str(record.get("cover", "")).startswith("/covers/"):
            skipped_uploads += 1
            del record["cover"]
        if len(record) > 2:  # more than key and title: there is something to carry
            if group:
                record["licenses"] = [{"platform": e["platform"], "platform_id": e["platform_id"], "title": e["title"]}
                                      for e in group["entries"]]
            out_groups.append(record)
        for entry in group["entries"] if group else []:
            if entry["manual"] and (entry["platform"], entry["platform_id"]) in manual_entries:
                manual_out.append(manual_entries[(entry["platform"], entry["platform_id"])])
            if entry["split"]:
                splits.append(store.entry_key(entry["platform"], entry["platform_id"]))

    aliases, alias_problem = [], None
    if include_aliases:
        try:
            aliases = _alias_pairs(json.loads(_aliases_path().read_text(encoding="utf-8"))) if _aliases_path().exists() else []
        except ValueError as e:
            alias_problem = f"aliases.json não foi exportado: {e}"
    export = {
        "format": FORMAT, "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filter": {"keys": len(keys) if keys is not None else None, "platforms": platforms or [],
                   "statuses": statuses or [], "tags": tags or [], "aliases": include_aliases},
        "groups": out_groups,
        "manual_games": manual_out,
        "splits": sorted(set(splits)),
        "aliases": [[k, v] for k, v in aliases],
    }
    export["summary"] = {"groups": len(out_groups), "manual_games": len(manual_out), "splits": len(export["splits"]),
                         "aliases": len(aliases), "uploaded_covers_skipped": skipped_uploads,
                         "warnings": [alias_problem] if alias_problem else []}
    return export


def _aliases_path() -> Path:
    from . import grouping
    return grouping.ALIASES_PATH


# --------------------------------------------------------------------------- import
class Workspace:
    """The two files an import may change. Loaded up front; only written when the import is applied."""

    def __init__(self, manual_entries: list[dict], alias_pairs: list[tuple[str, str]] | None, alias_problem: str | None):
        self.manual = manual_entries
        self.alias_pairs = alias_pairs  # None: aliases.json is unreadable, so it is left alone
        self.alias_problem = alias_problem
        self._original = (copy.deepcopy(manual_entries), list(alias_pairs) if alias_pairs is not None else None)

    @classmethod
    def load(cls) -> "Workspace":
        path = _aliases_path()
        pairs, problem = [], None
        if path.exists():
            try:
                pairs = _alias_pairs(json.loads(path.read_text(encoding="utf-8")))
            except ValueError as e:
                pairs, problem = None, f"o aliases.json daqui tem um problema ({e}); as junções do arquivo foram ignoradas"
        return cls(manual.load_entries(), pairs, problem)

    def write(self) -> None:
        """Only the files that actually changed are rewritten."""
        original_manual, original_aliases = self._original
        if self.manual != original_manual:
            manual.save_entries(self.manual)
        if self.alias_pairs is not None and self.alias_pairs != original_aliases:
            tmp = _aliases_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps([{k: v} for k, v in self.alias_pairs], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(_aliases_path())


def _validate(data) -> dict:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Este arquivo não é uma exportação do Game Library.")
    if data.get("version") != VERSION:
        raise ValueError(f"Versão {data.get('version')!r} do arquivo não é suportada (esperado {VERSION}). "
                         "Atualize o programa neste computador.")
    for field, kind in (("groups", list), ("manual_games", list), ("splits", list), ("aliases", list)):
        if not isinstance(data.get(field, []), kind):
            raise ValueError(f"O arquivo está malformado: '{field}' deveria ser uma lista.")
    if len(data.get("groups", [])) > MAX_GROUPS:
        raise ValueError("O arquivo tem jogos demais.")
    return data


def _single(local, incoming, mode):
    """-> (outcome, new value). Never clears anything: an empty incoming value changes nothing."""
    if incoming in (None, ""):
        return "none", local
    if local in (None, ""):
        return "set", incoming
    if local == incoming:
        return "same", local
    return ("overwrite", incoming) if mode == "overwrite" else ("kept", local)


def _multi(local: list, incoming: list, mode, merged):
    if not incoming:
        return "none", local
    if not local:
        return "set", incoming
    if {str(x).lower() for x in local} == {str(x).lower() for x in incoming}:
        return "same", local
    if mode == "merge":
        return "merge", merged(local + incoming)
    return ("overwrite", incoming) if mode == "overwrite" else ("kept", local)


def _run_import(conn: sqlite3.Connection, ws: Workspace, data: dict, mode: str, *, writing: bool) -> dict:
    """Does the whole import inside ONE transaction on `conn`. On a preview `conn` is an in-memory copy."""
    data = _validate(data)
    report = {"mode": mode, "warnings": [], "conflicts": [], "conflicts_total": 0,
              "file": {"exported_at": data.get("exported_at"), "groups": len(data.get("groups", [])),
                       "manual_games": len(data.get("manual_games", [])), "splits": len(data.get("splits", [])),
                       "aliases": len(data.get("aliases", []))},
              "groups": {"found_here": 0, "matched_by_license": 0, "waiting_for_the_game": 0, "touched": 0},
              "fields": {"written": 0, "unchanged": 0, "kept_local": 0, "overwritten": 0, "merged": 0, "skipped_invalid": 0},
              "manual_games": {"added": 0, "already_there": 0, "invalid": 0},
              "splits": {"added": 0, "already_there": 0}, "aliases": {"added": 0, "already_there": 0, "conflicting": 0}}
    warn = report["warnings"].append
    if ws.alias_problem and data.get("aliases"):
        warn(ws.alias_problem)
    now = int(time.time())
    conn.execute("BEGIN")
    try:
        # 1. manual games (dedupe by store+id, then by store+title: the same rule as adding one by hand)
        have_id = {(e["platform"], manual.entry_id(e)) for e in ws.manual}
        have_title = {(e["platform"], e["title"].lower()) for e in ws.manual}
        for raw in data.get("manual_games", []):
            try:
                title = " ".join(str(raw["title"]).split())[:200]
                platform = manual.ALIASES.get(" ".join(str(raw["platform"]).lower().split())[:30], " ".join(str(raw["platform"]).lower().split())[:30])
                if not title or not platform:
                    raise ValueError
                entry = {"platform": platform, "title": title}
                if raw.get("platform_id"):  # older entries have none: their identity is the title (manual.entry_id)
                    entry["platform_id"] = str(raw["platform_id"])[:64]
                if cover := _url_or_none(raw.get("cover_url")):
                    entry["cover_url"] = cover
                if link := _url_or_none(raw.get("url")):
                    entry["url"] = link
                if raw.get("playtime_minutes") is not None:
                    entry["playtime_minutes"] = check_minutes(raw["playtime_minutes"])
                if raw.get("developer"):
                    entry["developer"] = check_developer(raw["developer"])
            except (KeyError, TypeError, ValueError):
                report["manual_games"]["invalid"] += 1
                continue
            row_id = manual.entry_id(entry)
            if (platform, row_id) in have_id or (platform, title.lower()) in have_title:
                report["manual_games"]["already_there"] += 1
                continue
            if conn.execute("SELECT 1 FROM games WHERE platform = ? AND platform_id = ? AND source = 'api'",
                            (platform, row_id)).fetchone():
                entry["platform_id"] = row_id = uuid.uuid4().hex[:12]  # a hand-typed id must never collide with a real one
            ws.manual.append(entry)
            have_id.add((platform, row_id))
            have_title.add((platform, title.lower()))
            conn.execute(
                "INSERT OR IGNORE INTO games (platform, platform_id, title, playtime_minutes, url, cover_url, developer,"
                " source, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?)",
                (platform, row_id, title, entry.get("playtime_minutes"), entry.get("url"),
                 entry.get("cover_url"), entry.get("developer", ""), now))
            report["manual_games"]["added"] += 1

        # 2. splits (add only)
        existing_splits = store.split_keys(conn)
        for raw in data.get("splits", []):
            if not isinstance(raw, str) or ":" not in raw or len(raw) > 200:
                continue
            if raw in existing_splits:
                report["splits"]["already_there"] += 1
            else:
                conn.execute("INSERT OR IGNORE INTO entry_splits (entry_key) VALUES (?)", (raw,))
                existing_splits.add(raw)
                report["splits"]["added"] += 1

        # 3. aliases: a title that already has a destination here is never changed
        if ws.alias_pairs is not None:
            incoming: dict[str, list[tuple[str, str]]] = {}
            for raw in data.get("aliases", []):
                if isinstance(raw, list) and len(raw) == 2 and all(isinstance(x, str) and 0 < len(x) <= 300 for x in raw):
                    incoming.setdefault(normalize(raw[0]), []).append((raw[0], raw[1]))
            local_by_source: dict[str, set[str]] = {}
            for k, v in ws.alias_pairs:
                local_by_source.setdefault(normalize(k), set()).add(normalize(v))
            for source, pairs in incoming.items():
                targets = {normalize(v) for _, v in pairs}
                if source not in local_by_source:
                    ws.alias_pairs.extend(pairs)
                    report["aliases"]["added"] += len(pairs)
                elif local_by_source[source] == targets:
                    report["aliases"]["already_there"] += len(pairs)
                else:
                    report["aliases"]["conflicting"] += len(pairs)
                    report["conflicts_total"] += 1
                    if len(report["conflicts"]) < 50:
                        report["conflicts"].append({"key": source, "title": pairs[0][0], "field": "aliases",
                                                    "local": "outro destino", "imported": pairs[0][1], "resolution": "kept_local"})

        # 4. where does each imported game live on this computer?
        meta = store.load_meta(conn)
        alias_table = _build_aliases(ws.alias_pairs or [])
        rows = conn.execute("SELECT * FROM games").fetchall()
        local = group_games(rows, meta, aliases=alias_table)
        local_keys = {g["key"]: g for g in local}
        license_key = {(e["platform"], e["platform_id"]): g["key"] for g in local for e in g["entries"]}

        # 5. the organization
        seen: set[str] = set()
        for raw in data.get("groups", []):
            if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw["key"] or len(raw["key"]) > 300:
                warn("Um item do arquivo foi ignorado: sem chave válida.")
                continue
            key = raw["key"]
            if key in local_keys:
                report["groups"]["found_here"] += 1
            else:
                remapped = next((license_key[(lic.get("platform"), lic.get("platform_id"))] for lic in raw.get("licenses", [])
                                 if isinstance(lic, dict) and (lic.get("platform"), lic.get("platform_id")) in license_key), None)
                if remapped:
                    key = remapped
                    report["groups"]["matched_by_license"] += 1
                else:
                    report["groups"]["waiting_for_the_game"] += 1
            title = (local_keys[key]["title"] if key in local_keys else str(raw.get("title") or key))[:200]
            if key in seen:  # the same game twice in the file: the second sees what the first wrote
                meta = store.load_meta(conn)
            seen.add(key)
            changed = _apply_group(conn, meta, key, title, raw, mode, report)
            report["groups"]["touched"] += 1 if changed else 0
        if writing:
            ws.write()
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return report


def _apply_group(conn, meta, key: str, title: str, raw: dict, mode: str, report: dict) -> bool:
    fields, any_change = report["fields"], False

    def decide(name, outcome, local_value, incoming_value, new_value):
        nonlocal any_change
        if outcome == "none":
            return False
        if outcome == "same":
            fields["unchanged"] += 1
            return False
        if outcome == "kept":
            fields["kept_local"] += 1
        else:
            fields[{"set": "written", "overwrite": "overwritten", "merge": "merged"}[outcome]] += 1
            any_change = True
        report["conflicts_total"] += outcome in ("kept", "overwrite", "merge")
        if outcome in ("kept", "overwrite", "merge") and len(report["conflicts"]) < 50:
            report["conflicts"].append({"key": key, "title": title, "field": name, "local": local_value, "imported": incoming_value,
                                        "resolution": {"kept": "kept_local", "overwrite": "imported", "merge": "merged"}[outcome]})
        return outcome != "kept"

    def clean(name, check, value):
        try:
            return check(value)
        except (TypeError, ValueError):
            fields["skipped_invalid"] += 1
            report["warnings"].append(f"'{title}': o campo '{name}' tem um valor inválido e foi ignorado.")
            return None

    # statuses and tags: sets
    for name, check, local_map, table, column in (("statuses", check_statuses, meta.status, "game_status", "status"),
                                                  ("tags", check_tags, meta.tags, "tags", "tag")):
        if name not in raw:
            continue
        incoming = clean(name, check, raw[name])
        if not incoming:
            continue
        local_value = list(local_map.get(key, []))
        merged = (lambda v: [s for s in store.STATUSES if s in v]) if name == "statuses" else (lambda v: check_tags(v))
        outcome, new = _multi(local_value, incoming, mode, merged)
        if decide(name, outcome, local_value, incoming, new) and new != local_value:
            conn.execute(f"DELETE FROM {table} WHERE key = ?", (key,))
            conn.executemany(f"INSERT OR IGNORE INTO {table} (key, {column}) VALUES (?, ?)", [(key, v) for v in new])

    singles = (("rating", check_rating, meta.ratings, "ratings", "rating"),
               ("note", check_note, meta.notes, "notes", "note"),
               ("developer", check_developer, meta.developers, "developers", "developer"),
               ("release_year", check_year, meta.release_years, "release_years", "year"),
               ("playtime_minutes", check_minutes, meta.playtimes, "playtimes", "minutes"),
               ("cover", check_cover, meta.covers, "covers", "url"))
    for name, check, local_map, table, column in singles:
        if name not in raw:
            continue
        incoming = clean(name, check, raw[name])
        if incoming in (None, ""):
            continue
        outcome, new = _single(local_map.get(key), incoming, mode)
        if decide(name, outcome, local_map.get(key), incoming, new) and new != local_map.get(key):
            conn.execute(f"INSERT OR REPLACE INTO {table} (key, {column}) VALUES (?, ?)", (key, new))

    # dates: each end on its own, then the pair must still make sense
    local_start, local_end = meta.dates.get(key, (None, None))
    start_in = clean("started", check_date, raw["started"]) if "started" in raw else None
    end_in = clean("finished", check_date, raw["finished"]) if "finished" in raw else None
    new_start, new_end, wrote = local_start, local_end, False
    for name, incoming, current in (("started", start_in, local_start), ("finished", end_in, local_end)):
        if not incoming:
            continue
        outcome, new = _single(current, incoming, mode)
        if decide(name, outcome, current, incoming, new) and new != current:
            wrote = True
            if name == "started":
                new_start = new
            else:
                new_end = new
    if wrote:
        if new_start and new_end and new_end < new_start:
            report["warnings"].append(f"'{title}': as datas do arquivo e as daqui não combinam (o fim ficaria antes do início); datas ignoradas.")
            fields["skipped_invalid"] += 1
        else:
            conn.execute("INSERT OR REPLACE INTO dates (key, started, finished) VALUES (?, ?, ?)", (key, new_start, new_end))

    # hidden: only ever added, never undone
    if raw.get("hidden") is True:
        if key in meta.hidden:
            fields["unchanged"] += 1
        else:
            conn.execute("INSERT OR REPLACE INTO hidden (key, title) VALUES (?, ?)", (key, title))
            fields["written"] += 1
            any_change = True
    return any_change


def preview_import(conn: sqlite3.Connection, data: dict, mode: str = "fill") -> dict:
    """What an import would do, computed by running it on an in-memory copy. Nothing real is touched."""
    _check_mode(mode)
    clone = sqlite3.connect(":memory:")
    clone.row_factory = sqlite3.Row
    try:
        conn.backup(clone)
        return _run_import(clone, Workspace.load(), data, mode, writing=False)
    finally:
        clone.close()


def apply_import(conn: sqlite3.Connection, data: dict, mode: str = "fill") -> dict:
    """Back everything up, then import all-or-nothing."""
    _check_mode(mode)
    with _lock:
        _validate(data)
        backup = make_backup(conn)
        ws = Workspace.load()
        report = _run_import(conn, ws, data, mode, writing=True)
        report["backup"] = str(backup)
        return report


def make_backup(conn: sqlite3.Connection) -> Path:
    folder = store.DATA_DIR / "backups" / f"before-import-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    folder.mkdir(parents=True)
    target = sqlite3.connect(folder / "library.db")
    try:
        conn.backup(target)
    finally:
        target.close()
    for source in (manual.MANUAL_PATH, _aliases_path()):
        if source.exists():
            shutil.copy2(source, folder / source.name)
    return folder


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"modo inválido: {mode!r}")
