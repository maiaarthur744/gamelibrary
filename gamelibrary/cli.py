import argparse
import sys

from dotenv import load_dotenv

from . import server, store
from .providers import PROVIDERS, epic, gog


def cmd_sync(args: argparse.Namespace) -> int:
    conn = store.connect()
    names = [args.provider] if args.provider else list(PROVIDERS)
    failed = False
    for name in names:
        try:
            games = PROVIDERS[name]()
        except Exception as e:  # keep going so one broken provider doesn't block the rest
            print(f"{name}: FAILED - {e}", file=sys.stderr)
            failed = True
            continue
        if name == "manual":
            # manual entries carry their own platform (battlenet, amazon, ...)
            for platform in {g.platform for g in games}:
                store.replace_platform(conn, platform, [g for g in games if g.platform == platform])
        else:
            store.replace_platform(conn, name, games)
        print(f"{name}: {len(games)} games")
    return 1 if failed else 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = store.query(store.connect(), args.platform, args.search)
    for r in rows:
        hours = f"{r['playtime_minutes'] / 60:.1f}h" if r["playtime_minutes"] else "-"
        print(f"{r['platform']:<10} {r['title']:<50} {hours:>8}")
    print(f"\n{len(rows)} games")
    return 0


def cmd_gog_login(_: argparse.Namespace) -> int:
    print("1. Open this URL and log in to GOG:\n")
    print(f"   {gog.login_url()}\n")
    print("2. After login you'll land on a blank page. Copy the full URL from the address bar")
    print("   (it contains ?code=...) and paste it here.\n")
    gog.login(input("URL or code: "))
    print("GOG login saved.")
    return 0


def cmd_epic_login(_: argparse.Namespace) -> int:
    epic.login()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    server.serve(args.port, not args.no_browser)
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="gamelibrary")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="fetch libraries into the local database")
    p.add_argument("provider", nargs="?", choices=list(PROVIDERS))
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("list", help="show games from the local database")
    p.add_argument("-p", "--platform")
    p.add_argument("-s", "--search")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("gog-login", help="authenticate with GOG")
    p.set_defaults(func=cmd_gog_login)

    p = sub.add_parser("epic-login", help="authenticate with Epic Games (via Legendary)")
    p.set_defaults(func=cmd_epic_login)

    p = sub.add_parser("serve", help="open the web interface")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
