"""Runs the Legendary CLI with every HTTP request it makes logged, spaced out and capped.

Used instead of the plain `legendary` command:  python -m gamelibrary.legendary_logged list --json
Legendary fetches metadata for every game missing from its cache, in bursts of 16 threads, which is
hundreds of requests on a first run. Here they are spaced out and capped per run; Legendary saves what
it already fetched, so running again continues from where it stopped.
"""
import os
import sys
import threading
import time
from urllib.parse import urlsplit

MIN_INTERVAL = 0.3


class BudgetExceeded(Exception):
    pass


def _log(message: str) -> None:
    print(f"[net] {message}", file=sys.stderr, flush=True)


def install(budget: int) -> None:
    import requests

    original = requests.sessions.Session.request
    lock = threading.Lock()
    state = {"count": 0, "last": 0.0}

    def request(self, method, url, *args, **kwargs):
        parts = urlsplit(str(url))
        label = f"epic   {str(method).upper()} {parts.netloc}{parts.path}"  # never the query string
        with lock:
            state["count"] += 1
            n = state["count"]
            if n > budget:
                _log(f"epic   BLOQUEADA {str(method).upper()} {parts.netloc}{parts.path} (limite de {budget} chamadas)")
                raise BudgetExceeded(f"limite de {budget} chamadas externas atingido")
            wait = state["last"] + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            state["last"] = time.monotonic()
        started = time.monotonic()
        try:
            response = original(self, method, url, *args, **kwargs)
        except Exception as e:
            _log(f"{label} -> ERRO {type(e).__name__} [{n}/{budget}]")
            raise
        _log(f"{label} -> {response.status_code} ({time.monotonic() - started:.1f}s) [{n}/{budget}]")
        return response

    requests.sessions.Session.request = request


def main() -> None:
    install(int(os.environ.get("GAMELIBRARY_EPIC_BUDGET", "100")))
    from legendary.cli import main as legendary_main

    sys.argv = ["legendary", *sys.argv[1:]]
    legendary_main()


if __name__ == "__main__":
    main()
