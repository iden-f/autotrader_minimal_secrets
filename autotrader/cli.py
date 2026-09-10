"""Command line: everything you can do without touching a config file by hand."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

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
    from .lock import AlreadyRunning, run_lock
    from .runner import run as run_once
    cfg = Config.load(args.config)
    state = State.load(args.state)
    try:
        with run_lock(enabled=not args.no_lock and not args.dry_run):
            report = run_once(cfg, state, dry_run=args.dry_run,
                              notify=not args.no_notify)
    except AlreadyRunning as exc:
        print(_warn(str(exc)))
        return 0        # not an error: the other run is doing the work
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
        own = []
        for key, value in (search.filters or {}).items():
            if value not in (None, [], ""):
                own.append(f"{key}={value}")
        for key, value in (search.notify_on or {}).items():
            own.append(f"{key}={'on' if value else 'off'}")
        if search.price_drop_min_abs is not None:
            own.append(f"drops>=${search.price_drop_min_abs:,}")
        if own:
            print(f"     {YELLOW}own rules:{RESET} {', '.join(own)}")
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


def _coerce(text: str) -> Any:
    """Turn a command-line value into the JSON type it obviously is."""
    lowered = text.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"none", "null", ""}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


def cmd_set(args: argparse.Namespace) -> int:
    """Change one setting, globally or for a single search."""
    cfg = Config.load(args.config)
    value = _coerce(args.value)

    if args.search:
        matches = [s for s in cfg.searches
                   if s.id == args.search or s.name.lower() == args.search.lower()]
        if not matches:
            print(_bad(f"no search called {args.search!r}"))
            print(f"   {DIM}known: {', '.join(s.id for s in cfg.searches) or 'none'}{RESET}")
            return 1
        target = matches[0]
        for raw in cfg.data["searches"]:
            if raw["id"] != target.id:
                continue
            # Bare keys belong to that search's own filter block; dotted keys
            # address its other settings directly.
            if "." in args.key:
                head, _, tail = args.key.partition(".")
                raw.setdefault(head, {})[tail] = value
            else:
                raw.setdefault("filters", {})[args.key] = value
        cfg.save()
        print(_ok(f"{target.name}: {args.key} = {value!r}"))
        return 0

    before = cfg.get(args.key, "<unset>")
    cfg.set(args.key, value)
    cfg.save()
    print(_ok(f"{args.key}: {before!r} -> {value!r}"))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the setup end to end and say exactly what is wrong."""
    cfg = Config.load(args.config)
    state = State.load(args.state)
    problems = 0
    warnings = 0

    # ---- config file -------------------------------------------------
    print(f"{BOLD}Config{RESET}")
    path = Path(args.config)
    if path.exists():
        print(_ok(f"{path} ({path.stat().st_size} bytes)"))
    else:
        print(_warn(f"{path} does not exist yet - defaults are being used"))
        warnings += 1
    for key, want in (("scraping.max_pages", int), ("scraping.delay_ms", int),
                      ("archive.mode", str), ("notifications.timezone", str)):
        value = cfg.get(key)
        if not isinstance(value, want):
            print(_bad(f"{key} should be {want.__name__}, found {value!r}"))
            problems += 1
    mode = cfg.get("archive.mode")
    if mode not in ("off", "metadata", "full"):
        print(_bad(f"archive.mode must be off/metadata/full, found {mode!r}"))
        problems += 1
    tz = str(cfg.get("notifications.timezone") or "")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        print(_warn(f"unknown time zone {tz!r} - quiet hours will use UTC"))
        warnings += 1

    # ---- searches ----------------------------------------------------
    print(f"\n{BOLD}Searches{RESET}")
    if not cfg.active_searches:
        print(_bad("no searches configured"))
        print(f'   {DIM}python -m autotrader add "<paste your autotrader.ca link>"{RESET}')
        problems += 1
    for search in cfg.active_searches:
        summary = describe_search(search.url)
        if summary.valid:
            print(_ok(f"{search.name}: {' | '.join(summary.describe()) or 'no filters'}"))
        else:
            print(_bad(f"{search.name}: {'; '.join(summary.problems)}"))
            problems += 1
        for problem in (summary.problems if summary.valid else []):
            print(_warn(f"   {problem}"))
            warnings += 1
    disabled = [s for s in cfg.searches if not s.enabled]
    if disabled:
        print(f"   {DIM}{len(disabled)} search(es) paused: "
              f"{', '.join(s.name for s in disabled)}{RESET}")

    # ---- notification channels --------------------------------------
    print(f"\n{BOLD}Notifications{RESET}")
    status = cfg.channel_status()
    active = [n for n, i in status.items() if i["active"]]
    if not active:
        print(_bad("no channel is configured - you will never be told anything"))
        print(f"   {DIM}Free options: Telegram, Discord, ntfy, Slack, email.{RESET}")
        print(f"   {DIM}See SETUP.md for the exact steps.{RESET}")
        problems += 1
    for name, info in status.items():
        cost = "" if info["free"] else f" {YELLOW}(costs money){RESET}"
        if info["active"]:
            print(_ok(f"{info['label']}{cost}"))
        elif info["setting"] in (False, "false"):
            print(f"{DIM}-- {info['label']}: switched off{RESET}")
        else:
            print(_warn(f"{info['label']}: missing {', '.join(info['missing'])}"))

    if active and not args.offline:
        print(f"\n{BOLD}Channel reachability{RESET} {DIM}(no messages are sent){RESET}")
        for result in notifiers.verify_all(cfg):
            if result.ok and result.skipped:
                print(_warn(f"{result.channel}: {result.detail}"))
            elif result.ok:
                print(_ok(f"{result.channel}: {result.detail}"))
            else:
                print(_bad(f"{result.channel}: {result.detail}"))
                problems += 1

    # ---- stored data -------------------------------------------------
    print(f"\n{BOLD}Stored data{RESET}")
    stats = state.stats()
    print(_ok(f"{stats['total']} listing(s) tracked, {stats['active']} live, "
              f"{stats['gone']} gone"))
    if stats["median_price"]:
        print(f"   {DIM}prices ${stats['min_price']:,} - ${stats['max_price']:,} "
              f"(median ${stats['median_price']:,}){RESET}")
    if Path("state.corrupt.json").exists():
        print(_warn("state.corrupt.json exists - a previous state file was unreadable"))
        warnings += 1
    held = len(state.pending_changes())
    if held:
        print(_warn(f"{held} alert(s) waiting to be delivered"))
    last = state.last_run
    if last:
        mark = _ok if last.get("ok") else _bad
        print(mark(f"last run {last.get('at')}: {last.get('listings_seen', 0)} seen, "
                   f"{last.get('new', 0)} new, {last.get('searches_failed', 0)} failed"))
        for error in (last.get("errors") or [])[:3]:
            print(f"   {RED}{error}{RESET}")
    else:
        print(_warn("no run recorded yet - try: python -m autotrader run --dry-run"))

    # ---- does the bookkeeping hold together? --------------------------
    print(f"\n{BOLD}Bookkeeping{RESET}")
    from . import invariants
    try:
        payload = dashboard.build_payload(cfg, state, dict(os.environ))
    except Exception:  # noqa: BLE001 - the check is more useful than the payload
        payload = None
    broken = invariants.check(cfg, state, None, payload)
    if broken:
        for violation in broken:
            print(_bad(str(violation)))
            problems += 1
        written = invariants.write(broken, cfg, state)
        if written:
            print(f"   {DIM}details written to {written}{RESET}")
    else:
        print(_ok("every car is owned by one search and accounted for"))

    # ---- disk --------------------------------------------------------
    print(f"\n{BOLD}Archive{RESET}")
    report = size_report()
    print(f"   {report['folders']} folder(s), {report['bytes'] / 1048576:.1f} MB "
          f"({report['html_bytes'] / 1048576:.1f} MB saved pages)")
    if report["html_bytes"] > 20 * 1048576:
        print(_warn("saved pages are large; set archive.mode to 'metadata' and run: "
                    "python -m autotrader prune"))
        warnings += 1
    free = _free_disk_mb()
    if free is not None:
        line = f"   {free:,} MB free on this disk"
        print(_bad(line) if free < 50 else f"{line}")
        if free < 50:
            problems += 1

    # ---- live -------------------------------------------------------
    if args.live:
        problems += _live_check(cfg, sample=not args.no_sample)

    print()
    if problems:
        print(_bad(f"{problems} problem(s) need attention"))
    elif warnings:
        print(_warn(f"usable, with {warnings} thing(s) worth a look"))
    else:
        print(_ok("everything checks out"))
    if not args.live:
        print(f"   {DIM}Add --live to fetch autotrader.ca and test the parser.{RESET}")
    return 1 if problems else 0


def _free_disk_mb() -> int | None:
    try:
        import shutil as _shutil
        return _shutil.disk_usage(".").free // 1048576
    except OSError:
        return None


def _live_check(cfg: Config, sample: bool = True) -> int:
    """Fetch each search for real and show exactly what the parser made of it."""
    print(f"\n{BOLD}Live check{RESET} {DIM}(fetching autotrader.ca){RESET}")
    scraping = cfg.get("scraping", {}) or {}
    fetcher = Fetcher(timeout=int(scraping.get("timeout_seconds", 30)),
                      retries=int(scraping.get("retries", 3)),
                      delay_ms=int(scraping.get("delay_ms", 1200)),
                      user_agent=str(scraping.get("user_agent", "auto")))
    problems = 0
    try:
        for search in cfg.active_searches:
            url = page_url(search.url, 1, int(scraping.get("results_per_page", 50)))
            print(f"\n  {BOLD}{search.name}{RESET}")
            print(f"  {DIM}{url}{RESET}")
            try:
                response = fetcher.get(url)
            except Exception as exc:  # noqa: BLE001
                print(_bad(f"  could not fetch: {exc}"))
                problems += 1
                continue

            size_kb = len(response.text) // 1024
            print(f"  HTTP {response.status} - {size_kb} KB in {response.elapsed_ms} ms")
            result = parse_search_page(response.text, url)

            print(f"  {BOLD}Strategy results{RESET}")
            for name, count in result.candidates.items():
                won = " <-- used" if name == result.strategy else ""
                mark = GREEN if count else DIM
                print(f"    {mark}{name:<16}{RESET} {count:>3} listing(s){BOLD}{won}{RESET}")

            if not result.listings:
                print(_bad("  the page loaded but NO listings were parsed."))
                print(f"     {DIM}Either the search genuinely has no results, or every"
                      f" parser strategy has fallen behind the site.{RESET}")
                print(f"     {DIM}A real run treats this as a failure and alerts you.{RESET}")
                problems += 1
                continue

            print(_ok(f"  {len(result.listings)} listing(s) via '{result.strategy}'"))
            if not sample:
                continue

            print(f"  {BOLD}Sample parse{RESET} {DIM}(eyeball these against the site){RESET}")
            for listing in result.listings[:3]:
                print(f"    {BOLD}{listing.display_title}{RESET}")
                print(f"      id         {listing.id}")
                source = (f"   {DIM}(from the {listing.price_source} page){RESET}"
                          if listing.price is not None and listing.price_source else "")
                print(f"      price      {listing.price_text}{source}")
                print(f"      odometer   {listing.mileage_text}")
                print(f"      where      {listing.location or '?'}"
                      f"{', ' + listing.province if listing.province else ''}")
                print(f"      photos     {len(listing.images)}")
                print(f"      url        {DIM}{listing.url}{RESET}")

            missing_price = sum(1 for l in result.listings if l.price is None)
            missing_km = sum(1 for l in result.listings if l.mileage_km is None)
            if missing_price:
                print(f"    {DIM}{missing_price}/{len(result.listings)} have no price "
                      f"(normal for 'call for price' listings){RESET}")
            if missing_km > len(result.listings) / 2:
                print(_warn(f"    {missing_km}/{len(result.listings)} have no odometer "
                            f"- the parser may be missing it"))

            print(f"  {DIM}requests so far: {fetcher.stats['requests']}, "
                  f"retries: {fetcher.stats['retries']}, "
                  f"blocked: {fetcher.stats['blocked']}{RESET}")
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

    if args.compact:
        from .archive import compact
        result = compact(dry_run=args.dry_run, keep_images=args.keep_images)
        verb = "would free" if args.dry_run else "freed"
        print(_ok(f"{verb} {result['bytes_freed'] / 1048576:.1f} MB across "
                  f"{result['folders']} v1 folder(s)"))
        print(f"   {DIM}{result['upgraded']} rewritten as metadata, "
              f"{len(result['removed'])} file(s) removed{RESET}")
        if result["failed"]:
            # Nothing is deleted from a folder we could not read, so this is a
            # report, not a loss.
            print(_warn(f"left alone (could not be read): "
                        f"{', '.join(result['failed'][:8])}"))
        return 0

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


def cmd_capture(args: argparse.Namespace) -> int:
    """Save what the live site is serving right now, on purpose.

    The bot captures a page by itself when a parse goes wrong, but a strategy
    that quietly scores zero while another one carries the run is not "wrong"
    enough to trigger that. This is how you get the evidence deliberately.
    """
    from . import diagnose
    from .http import Fetcher
    from .urls import page_url

    cfg = Config.load(args.config)
    searches = [s for s in cfg.active_searches
                if not args.search or args.search in (s.id, s.name)]
    if not searches:
        print(_bad("no matching enabled search"))
        return 2

    scraping = cfg.get("scraping", {}) or {}
    fetcher = Fetcher(
        timeout=int(scraping.get("timeout_seconds", 30) or 30),
        retries=int(scraping.get("retries", 3) or 0),
        delay_ms=int(scraping.get("delay_ms", 1200) or 0),
        user_agent=str(scraping.get("user_agent", "auto")),
        budget=int(scraping.get("request_budget", 250) or 0),
    )

    failed = 0
    for search in searches:
        url = page_url(search.url, args.page,
                       int(scraping.get("results_per_page", 50) or 50))
        try:
            response = fetcher.get(url)
        except Exception as exc:  # noqa: BLE001 - report, do not traceback
            print(_bad(f"{search.name}: {exc}"))
            failed += 1
            continue
        data = diagnose.capture(response.url, response.text, response.status,
                                response.elapsed_ms, search.name)
        slug = search.id if args.page == 1 else f"{search.id}-p{args.page}"
        written = diagnose.write(data, slug)
        print(_ok(f"{search.name}: {data['listings_found']} listing(s), "
                  f"strategy {data['strategy_used']}, scores {data['strategy_scores']}"))
        print(f"   {DIM}{written}{RESET}")
        if args.raw:
            raw = diagnose.write_raw(response.text, slug)
            if raw:
                print(f"   {DIM}{raw} ({raw.stat().st_size:,} bytes gzipped){RESET}")
    return 1 if failed else 0


SOAK_REPORT = Path("SOAK.md")


def cmd_soak_note(args: argparse.Namespace) -> int:
    """Append one line to the soak log describing the run that just happened.

    Written from state rather than from the run's own return value, because
    the soak drives the bot as a subprocess - exactly as the schedule does -
    so what is recorded here is what actually survived to disk.
    """
    state = State.load(args.state)
    last = state.last_run
    if not last:
        return 0

    live = [e for e in state.listings.values() if e.get("status") != "gone"]

    header = ("| # | at (UTC) | seen | new | drop | rise | priced | gone | back | "
              "unpriced | filtered | reqs | s | strategies | notes |\n"
              "|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|---|\n")
    existing = SOAK_REPORT.read_text(encoding="utf-8") if SOAK_REPORT.exists() else ""
    if not existing:
        SOAK_REPORT.write_text(
            "# Soak log\n\nOne line per cycle, written by the bot as it ran "
            "against the live site.\n\n" + header, encoding="utf-8")
    elif header not in existing:
        # The bot was upgraded mid-soak and reports a column it did not
        # before. Start a new table rather than writing rows the old header
        # cannot describe.
        with SOAK_REPORT.open("a", encoding="utf-8") as handle:
            handle.write("\n" + header)

    notes: list[str] = []
    for name in ("errors", "warnings"):
        for item in (last.get(name) or [])[:2]:
            notes.append(f"{name[:-1]}: {str(item)[:110]}")
    for drift in (last.get("shape_drift") or []):
        notes.append(("DRIFT " if drift.get("serious") else "drift ")
                     + "; ".join(drift.get("reasons") or [])[:110])
    if not notes:
        notes.append("clean")

    strategies = ", ".join(sorted(set((last.get("strategies") or {}).values())))
    row = (f"| {args.cycle}/{args.total} | {str(last.get('at'))[:19]} "
           f"| {last.get('listings_seen', 0)} | {last.get('new', 0)} "
           f"| {last.get('price_drops', 0)} | {last.get('price_rises', 0)} "
           f"| {last.get('priced', 0)} | {last.get('removed', 0)} "
           f"| {last.get('relisted', 0)} "
           f"| {last.get('unpriced', 0)} | {last.get('filtered_out', 0)} "
           f"| {last.get('requests_made', 0)} | {last.get('duration_s', 0)} "
           f"| {strategies or '-'} | " + "<br>".join(notes) + " |\n")
    with SOAK_REPORT.open("a", encoding="utf-8") as handle:
        handle.write(row)

    print(f"cycle {args.cycle}/{args.total}: {len(live)} live, "
          f"{last.get('new', 0)} new, {last.get('price_drops', 0)} drop(s), "
          f"{last.get('removed', 0)} removed, ok={last.get('ok')}")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    """Record the first time each kind of market event really happened.

    Read-only: it looks at what the watcher already stored and writes a
    ledger. It never scrapes and never notifies, which is what keeps it
    incapable of holding up or wedging a check.
    """
    from . import events

    state = State.load(args.state)
    record = events.update(state)
    first, waiting = record.get("first", {}), record.get("waiting", [])

    for kind, label in events.KINDS.items():
        seen = first.get(kind)
        if seen:
            fresh = " (new)" if kind in record.get("new_kinds", []) else ""
            print(_ok(f"{label}{fresh}: {str(seen['at'])[:19]} - "
                      f"{seen.get('detail') or ''}"))
            print(f"   {DIM}{seen.get('title', '')[:70]}{RESET}")
            print(f"   {DIM}{seen.get('delivered', '')}{RESET}")
        else:
            print(f"-- {label}: still waiting")
    print(f"\n   {DIM}{len(first)} of {len(events.KINDS)} seen; "
          f"ledger written to {events.LEDGER_PATH}{RESET}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from .migrate import migrate
    return migrate(config_path=Path(args.config), state_path=Path(args.state),
                   dry_run=args.dry_run)


def cmd_setup(args: argparse.Namespace) -> int:
    """Get from nothing to working, with or without a human present."""
    from . import provision

    cfg = Config.load(args.config)

    if args.new_topic:
        topic = provision.generate_topic()
        cfg.set("notifications.channels.ntfy.topic", topic)
        cfg.set("notifications.channels.ntfy.enabled", True)
        cfg.save()
        provision.write_notify_file(cfg)
        print(_ok(f"new ntfy topic: {topic}"))
        print(f"   subscribe at {BOLD}{provision.subscribe_url(cfg)}{RESET}")
        return 0

    interactive = not args.non_interactive and sys.stdin.isatty()

    if interactive:
        print(f"{BOLD}AutoTrader watcher setup{RESET}\n")
        print("Open autotrader.ca, run the search you want to watch, then copy the")
        print("address bar and paste it here.\n")
        while True:
            try:
                url = input("Search link (blank when done): ").strip()
            except EOFError:
                break
            if not url:
                break
            try:
                search = cfg.add_search(url)
            except ConfigError as exc:
                print(_bad(str(exc)))
                continue
            summary = describe_search(search.url)
            print(_ok(f'watching "{search.name}" - '
                      f'{" | ".join(summary.describe()) or "no filters"}'))
            name = input(f"   Name it [{search.name}]: ").strip()
            if name:
                for raw in cfg.data["searches"]:
                    if raw["id"] == search.id:
                        raw["name"] = name
        cfg.save()

    for url in (args.add or []):
        try:
            search = cfg.add_search(url)
            print(_ok(f'watching "{search.name}"'))
        except ConfigError as exc:
            print(_warn(str(exc)))
    if args.add:
        cfg.save()

    # Everything below is what the workflow runs unattended: adopt the old
    # secret, make sure something can reach the user, write the instructions.
    result = provision.bootstrap(cfg, dict(os.environ))
    for step in result["steps"]:
        mark = _ok if step.get("changed") else _warn
        print(mark(f"{step['step']}: {step['reason']}"))

    if result["subscribe_url"]:
        print()
        print(f"{BOLD}Your alerts go here:{RESET}")
        print(f"   {BOLD}{result['subscribe_url']}{RESET}")
        print(f"   {DIM}Open it in a browser, or install the ntfy app and subscribe")
        print(f"   to the topic '{result['subscribe_url'].rsplit('/', 1)[-1]}'.")
        print(f"   Written to NOTIFY.md as well.{RESET}")

    print()
    if not cfg.active_searches:
        print(_warn("no searches yet - add one with:"))
        print(f'   {DIM}python -m autotrader add "<paste an autotrader.ca link>"{RESET}')
    else:
        print(_ok(f"{len(cfg.active_searches)} search(es) being watched"))
    print(f"   {DIM}Check everything with: python -m autotrader doctor{RESET}")
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
    p.add_argument("--no-lock", action="store_true",
                   help="skip the single-run lock (not recommended)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("add", help="watch a pasted AutoTrader search link")
    p.add_argument("url", nargs="+")
    p.add_argument("--name", default="")
    p.add_argument("--force", action="store_true", help="accept a link that fails validation")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="show watched searches")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("set", help="change a setting, globally or per search")
    p.add_argument("key", help="e.g. filters.max_price, or max_price with --search")
    p.add_argument("value")
    p.add_argument("--search", help="apply to this search only (id or name)")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("remove", help="stop watching a search")
    p.add_argument("id")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("enable", help="turn a search on or off")
    p.add_argument("id")
    p.add_argument("--off", action="store_true")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("doctor", help="check the whole setup and explain problems")
    p.add_argument("--live", action="store_true",
                   help="also fetch autotrader.ca and show what the parser made of it")
    p.add_argument("--offline", action="store_true",
                   help="skip the channel reachability checks")
    p.add_argument("--no-sample", action="store_true",
                   help="with --live, skip the sample listing dump")
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
    p.add_argument("--compact", action="store_true",
                   help="rewrite v1 folders as metadata and drop the raw HTML")
    p.add_argument("--keep-images", type=int, default=0,
                   help="photos to keep per folder when compacting (default 0)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("capture", help="save what the live search page looks like now")
    p.add_argument("--search", help="only this search id or name")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--raw", action="store_true",
                   help="also save the page itself, gzipped, for fixture work")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("soak-note", help="record one soak cycle in SOAK.md")
    p.add_argument("cycle")
    p.add_argument("total")
    p.set_defaults(func=cmd_soak_note)

    p = sub.add_parser("events", help="record the first real market event of each kind")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("migrate", help="import v1 data (seen_listings.json + archives)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("setup", help="get from nothing to working")
    p.add_argument("--non-interactive", action="store_true",
                   help="never prompt; take sane defaults (what CI runs)")
    p.add_argument("--add", action="append", metavar="URL",
                   help="also watch this search link (repeatable)")
    p.add_argument("--new-topic", action="store_true",
                   help="generate a fresh ntfy topic and stop")
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
