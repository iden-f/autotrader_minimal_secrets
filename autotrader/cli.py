"""Command line: everything you can do without touching a config file by hand."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import dashboard, notifiers
from .archive import prune as prune_archives
from .archive import size_report
from .config import CHANNEL_SECRETS, Config, ConfigError
from .http import Fetcher
from .listing import Listing
from .parser import parse_search_page
from .state import Change, State
from .urls import describe_search, normalise_search_url, page_url

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m")


def _colour(enabled: bool) -> None:
    if not enabled:
        globals().update(GREEN="", RED="", YELLOW="", DIM="", BOLD="", RESET="")


def _ok(text: str) -> str:
    return f"{GREEN}OK{RESET} {text}"


def _bad(text: str) -> str:
    return f"{RED}!!{RESET} {text}"


def _warn(text: str) -> str:
    return f"{YELLOW}--{RESET} {text}"


# ----------------------------------------------------------------- commands


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run as run_once
    cfg = Config.load(args.config)
    state = State.load(args.state)
    report = run_once(cfg, state, dry_run=args.dry_run, notify=not args.no_notify)
    print(report.summary())
    for warning in report.warnings:
        print(_warn(warning))
    for error in report.errors:
        print(_bad(error))
    for line in report.notified:
        print(f"   {line}")
    if not args.dry_run:
        written = dashboard.write(cfg, state)
        if written:
            print(f"   dashboard data written to {written}")
    return 0 if report.ok else 1


def cmd_add(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    url = " ".join(args.url).strip()
    try:
        search = cfg.add_search(url, args.name or "", strict=not args.force)
    except ConfigError as exc:
        print(_bad(str(exc)))
        return 1
    cfg.save()
    summary = describe_search(search.url)
    print(_ok(f'added "{search.name}" ({search.id})'))
    if summary.chips if hasattr(summary, "chips") else summary.describe():
        print(f"   {DIM}{' | '.join(summary.describe())}{RESET}")
    for problem in summary.problems:
        print(_warn(problem))
    print(f"   {DIM}{search.url}{RESET}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    state = State.load(args.state)
    searches = cfg.searches
    if not searches:
        print("No searches yet. Add one with:")
        print("   python -m autotrader add \"<paste your autotrader.ca search link>\"")
        return 0
    for search in searches:
        health = (state.data.get("searches") or {}).get(search.id, {})
        mark = f"{GREEN}on {RESET}" if search.enabled else f"{DIM}off{RESET}"
        fails = health.get("consecutive_failures", 0)
        status = (f"{RED}{fails} failed run(s){RESET}" if fails
                  else f"{health.get('last_count', 0)} listing(s) last run")
        print(f"{mark} {BOLD}{search.name}{RESET}  {DIM}[{search.id}]{RESET}")
        print(f"     {' | '.join(describe_search(search.url).describe()) or 'no filters'}")
        print(f"     {status}   {DIM}{search.url}{RESET}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    if cfg.remove_search(args.id):
        cfg.save()
        print(_ok(f"removed {args.id}"))
        return 0
    print(_bad(f"no search with id {args.id}"))
    return 1


def cmd_enable(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    for search in cfg.data.get("searches", []):
        if search.get("id") == args.id:
            search["enabled"] = not args.off
            cfg.save()
            print(_ok(f"{args.id} is now {'off' if args.off else 'on'}"))
            return 0
    print(_bad(f"no search with id {args.id}"))
    return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the setup end to end and say exactly what is wrong."""
    cfg = Config.load(args.config)
    state = State.load(args.state)
    problems = 0

    print(f"{BOLD}Searches{RESET}")
    if not cfg.active_searches:
        print(_bad("no searches configured - run: python -m autotrader add <url>"))
        problems += 1
    for search in cfg.active_searches:
        summary = describe_search(search.url)
        if summary.valid:
            print(_ok(f"{search.name}: {' | '.join(summary.describe()) or 'no filters'}"))
        else:
            print(_bad(f"{search.name}: {'; '.join(summary.problems)}"))
            problems += 1

    print(f"\n{BOLD}Notifications{RESET}")
    status = cfg.channel_status()
    if not any(s["active"] for s in status.values()):
        print(_bad("no channel is configured - you will not be told about anything"))
        print(f"   {DIM}The free ones: Telegram, Discord, ntfy, Slack, email.{RESET}")
        problems += 1
    for name, info in status.items():
        cost = "" if info["free"] else f" {YELLOW}(costs money){RESET}"
        if info["active"]:
            print(_ok(f"{info['label']}{cost}"))
        elif info["setting"] in (False, "false"):
            print(f"{DIM}-- {info['label']}: switched off{RESET}")
        else:
            print(_warn(f"{info['label']}: missing {', '.join(info['missing'])}"))

    print(f"\n{BOLD}State{RESET}")
    stats = state.stats()
    print(_ok(f"{stats['total']} listing(s) tracked, {stats['active']} active, "
              f"{stats['gone']} gone"))
    if stats["median_price"]:
        print(f"   {DIM}prices ${stats['min_price']:,} - ${stats['max_price']:,} "
              f"(median ${stats['median_price']:,}){RESET}")
    last = state.last_run
    if last:
        mark = _ok if last.get("ok") else _bad
        print(mark(f"last run {last.get('at')}: {last.get('new', 0)} new, "
                   f"{last.get('searches_failed', 0)} failed"))
        for error in (last.get("errors") or [])[:3]:
            print(f"   {RED}{error}{RESET}")

    print(f"\n{BOLD}Archive{RESET}")
    report = size_report()
    print(f"   {report['folders']} folder(s), {report['bytes'] / 1048576:.1f} MB "
          f"({report['html_bytes'] / 1048576:.1f} MB HTML)")
    if report["html_bytes"] > 20 * 1048576:
        print(_warn("archived HTML is large; consider archive.mode = metadata "
                    "and: python -m autotrader prune"))

    if args.live:
        print(f"\n{BOLD}Live check{RESET} {DIM}(fetches autotrader.ca){RESET}")
        problems += _live_check(cfg)

    print()
    print(_ok("everything looks usable") if not problems
          else _bad(f"{problems} problem(s) above need attention"))
    return 1 if problems else 0


def _live_check(cfg: Config) -> int:
    """Fetch each search for real and report what the parser managed to read."""
    scraping = cfg.get("scraping", {}) or {}
    fetcher = Fetcher(timeout=int(scraping.get("timeout_seconds", 30)),
                      retries=int(scraping.get("retries", 3)),
                      delay_ms=int(scraping.get("delay_ms", 1200)),
                      user_agent=str(scraping.get("user_agent", "auto")))
    problems = 0
    try:
        for search in cfg.active_searches:
            url = page_url(search.url, 1, int(scraping.get("results_per_page", 50)))
            try:
                response = fetcher.get(url)
            except Exception as exc:  # noqa: BLE001
                print(_bad(f"{search.name}: {exc}"))
                problems += 1
                continue
            result = parse_search_page(response.text, url)
            if result.listings:
                print(_ok(f"{search.name}: {len(result.listings)} listing(s) "
                          f"via '{result.strategy}'  {DIM}{result.candidates}{RESET}"))
                sample = result.listings[0]
                print(f"   {DIM}e.g. {sample.display_title} - {sample.price_text} "
                      f"- {sample.mileage_text}{RESET}")
            else:
                print(_bad(f"{search.name}: page loaded ({len(response.text) // 1024} KB) "
                           f"but no listings were found. Strategies: {result.candidates}"))
                problems += 1
    finally:
        fetcher.close()
    return problems


def cmd_test_notify(args: argparse.Namespace) -> int:
    """Send a sample alert through every configured channel."""
    cfg = Config.load(args.config)
    channels = notifiers.build(cfg)
    if not channels:
        print(_bad("no channel is configured - nothing to test"))
        for name, spec in CHANNEL_SECRETS.items():
            tag = "free" if spec["free"] else "paid"
            print(f"   {BOLD}{spec['label']}{RESET} ({tag}): "
                  f"set {', '.join(spec['required']) or 'no secrets'}")
            print(f"      {DIM}{spec['help']}{RESET}")
        return 1

    sample = Listing(
        id="0", url="https://www.autotrader.ca/", title="Test message",
        year=2022, make="BMW", model="M5", trim="Competition", price=102199,
        mileage_km=30244, location="St. Catharines", drivetrain="AWD",
        transmission="Automatic", color="Black Sapphire",
        search_name="Test", price_source="detail",
    )
    changes = [Change(Change.NEW, sample),
               Change(Change.PRICE_DROP, sample, old_price=109999, new_price=102199)]
    failures = 0
    for channel in channels:
        result = channel.send(changes, {"test": True})
        print(_ok(str(result)) if result.ok else _bad(str(result)))
        failures += 0 if result.ok else 1
    return 1 if failures else 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    state = State.load(args.state)
    path = dashboard.write(cfg, state)
    if path is None:
        print(_warn("dashboard is disabled in config (dashboard.enabled)"))
        return 0
    print(_ok(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)"))
    index = path.parent / "index.html"
    if index.exists():
        print(f"   {DIM}open {index} in a browser, or publish docs/ with GitHub Pages{RESET}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui import serve
    return serve(host=args.host, port=args.port, config_path=Path(args.config),
                 state_path=Path(args.state), open_browser=not args.no_browser)


def cmd_prune(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    conf = dict(cfg.get("archive", {}) or {})
    if args.keep_last is not None:
        conf["keep_last"] = args.keep_last
    if args.keep_days is not None:
        conf["keep_days"] = args.keep_days
    removed = prune_archives(conf, dry_run=args.dry_run)
    verb = "would remove" if args.dry_run else "removed"
    print(_ok(f"{verb} {len(removed)} archive folder(s)"))
    if removed[:10]:
        print(f"   {DIM}{', '.join(removed[:10])}{'...' if len(removed) > 10 else ''}{RESET}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from .migrate import migrate
    return migrate(config_path=Path(args.config), state_path=Path(args.state),
                   dry_run=args.dry_run)


def cmd_setup(args: argparse.Namespace) -> int:
    """A short interactive first-run wizard."""
    cfg = Config.load(args.config)
    print(f"{BOLD}AutoTrader watcher setup{RESET}\n")
    print("1. Open autotrader.ca, run the search you want to watch, then copy")
    print("   the address bar.\n")
    while True:
        url = input("Paste your search link (blank to stop): ").strip()
        if not url:
            break
        try:
            search = cfg.add_search(url)
        except ConfigError as exc:
            print(_bad(str(exc)))
            continue
        summary = describe_search(search.url)
        print(_ok(f'watching "{search.name}" - {" | ".join(summary.describe()) or "no filters"}'))
        name = input(f'   Name it [{search.name}]: ').strip()
        if name:
            for raw in cfg.data["searches"]:
                if raw["id"] == search.id:
                    raw["name"] = name

    print(f"\n2. Notifications. The free options:\n")
    for name, spec in CHANNEL_SECRETS.items():
        if spec["free"]:
            print(f"   {BOLD}{spec['label']}{RESET}: {spec['help']}")
    print(f"\n   Set the secrets as environment variables (or GitHub repository")
    print(f"   secrets). Then check them with: python -m autotrader doctor\n")

    topic = input("ntfy topic (optional, easiest phone push - just make one up): ").strip()
    if topic:
        cfg.set("notifications.channels.ntfy.topic", topic)
        cfg.set("notifications.channels.ntfy.enabled", True)
        print(_ok(f"subscribe to '{topic}' in the ntfy app to get alerts"))

    cfg.save()
    print(f"\n{_ok(f'saved {cfg.path}')}")
    print(f"   Try it: python -m autotrader run --dry-run")
    return 0


# ----------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autotrader",
        description="Watch AutoTrader.ca searches and get told about new listings.",
    )
    parser.add_argument("--config", default=os.getenv("AUTOTRADER_CONFIG", "config.json"))
    parser.add_argument("--state", default=os.getenv("AUTOTRADER_STATE", "state.json"))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-colour", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="check every search once")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would happen, change nothing, send nothing")
    p.add_argument("--no-notify", action="store_true", help="update state but stay silent")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("add", help="watch a pasted AutoTrader search link")
    p.add_argument("url", nargs="+")
    p.add_argument("--name", default="")
    p.add_argument("--force", action="store_true", help="accept a link that fails validation")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="show watched searches")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="stop watching a search")
    p.add_argument("id")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("enable", help="turn a search on or off")
    p.add_argument("id")
    p.add_argument("--off", action="store_true")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("doctor", help="check the whole setup and explain problems")
    p.add_argument("--live", action="store_true", help="also fetch autotrader.ca for real")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("test-notify", help="send a sample alert to every channel")
    p.set_defaults(func=cmd_test_notify)

    p = sub.add_parser("dashboard", help="regenerate docs/data.json")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("ui", help="open the settings and dashboard UI in a browser")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("prune", help="delete old archive folders")
    p.add_argument("--keep-last", type=int)
    p.add_argument("--keep-days", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("migrate", help="import v1 data (seen_listings.json + archives)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("setup", help="interactive first-run wizard")
    p.set_defaults(func=cmd_setup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _colour(sys.stdout.isatty() and not args.no_colour and not os.getenv("NO_COLOR"))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s" if args.verbose else "%(message)s",
        stream=sys.stderr,
    )
    if not getattr(args, "func", None):
        # Bare invocation is the common case in CI: just do a run.
        args = parser.parse_args((argv or []) + ["run"])
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(_bad(str(exc)), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
