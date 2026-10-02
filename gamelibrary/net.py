"""Every request this app sends to an outside service goes through here.

- Only allowed inside `session()` (the `sync` and `*-login` commands). Anywhere else it raises.
- Each request is logged to the terminal (host and path only: never the query string, which holds keys).
- Each provider has a request budget per run; going over it stops the run before anything is sent.
- Requests are spaced out, so there are no bursts.
"""
import contextlib
import os
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx

MIN_INTERVAL = 0.3  # seconds between requests
# Requests allowed per provider in one run. Steam/GOG need 2-6 in practice; Epic needs a handful once
# Legendary's cache is warm, and hundreds on a first run, which this spreads over several runs.
BUDGETS = {"steam": 20, "gog": 20, "epic": 100, "manual": 0}
DEFAULT_BUDGET = 20


class NetworkNotAllowed(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


_lock = threading.Lock()
_active = False
_provider = ""
_budget = 0
_counts: dict[str, int] = {}
_last_request = 0.0


def log(message: str) -> None:
    print(f"[net] {message}", file=sys.stderr, flush=True)


def budget_for(name: str) -> int:
    if name == "manual":
        return 0  # reads a local file: it must never touch the network
    override = os.environ.get("GAMELIBRARY_MAX_REQUESTS")
    if override:
        try:
            return int(override)
        except ValueError:
            raise ValueError("GAMELIBRARY_MAX_REQUESTS must be a whole number") from None
    return BUDGETS.get(name, DEFAULT_BUDGET)


@contextlib.contextmanager
def session():
    """Outside requests are only possible while this is open."""
    global _active, _counts
    with _lock:
        _active, _counts = True, {}
    try:
        yield
    finally:
        with _lock:
            _active = False


@contextlib.contextmanager
def provider(name: str):
    """Names who is asking and sets the budget for that provider."""
    global _provider, _budget
    with _lock:
        _provider, _budget = name, budget_for(name)
        _counts.setdefault(name, 0)
    try:
        yield
    finally:
        with _lock:
            _provider, _budget = "", 0


def check_allowed(what: str) -> None:
    if not (_active and _provider):
        raise NetworkNotAllowed(f"chamada externa bloqueada fora do sync: {what}")


def remaining() -> int:
    return max(_budget - _counts.get(_provider, 0), 0)


def note_external(n: int = 1) -> None:
    """Count requests made by a subprocess (Legendary), which enforces the budget itself."""
    with _lock:
        _counts[_provider] = _counts.get(_provider, 0) + n


def _reserve(what: str) -> int:
    global _last_request
    with _lock:
        check_allowed(what)
        if _counts[_provider] >= _budget:
            log(f"{_provider:<6} BLOQUEADA {what} (limite de {_budget} chamadas)")
            raise BudgetExceeded(
                f"{_provider}: limite de {_budget} chamadas externas por sync atingido; nada mais foi enviado"
            )
        _counts[_provider] += 1
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        return _counts[_provider]


def get(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = 30) -> httpx.Response:
    parts = urlsplit(url)
    where = f"{parts.netloc}{parts.path}"  # no query string: it carries API keys
    n = _reserve(f"GET {where}")
    label = f"{_provider:<6} GET {where}"
    started = time.monotonic()
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        log(f"{label} -> ERRO {type(e).__name__} [{n}/{_budget}]")
        raise
    log(f"{label} -> {response.status_code} ({time.monotonic() - started:.1f}s) [{n}/{_budget}]")
    return response


def print_summary() -> None:
    used = {name: count for name, count in _counts.items() if count}
    if not used:
        log("Nenhuma chamada externa neste sync.")
        return
    detail = ", ".join(f"{name} {count}" for name, count in used.items())
    log(f"Resumo: {detail}. Total: {sum(used.values())} chamadas externas.")
