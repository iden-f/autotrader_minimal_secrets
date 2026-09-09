"""One full pass: scrape every search, work out what changed, tell the user.

The ordering here is deliberate.  State is saved in a ``finally`` block, and
every step that touches the network is wrapped, so the worst case for any
single failure is "that search produced nothing this run" rather than
"the run crashed and we forgot everything we knew".
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from . import archive as archive_mod
from . import filters, notifiers
from .config import Config
from .enrich import enrich
from .http import BlockedError, FetchError, Fetcher
from .listing import Listing
from .parser import parse_search_page
from .state import Change, State
from .urls import page_url

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    started_at: float = field(default_factory=time.time)
    searches_run: int = 0
    searches_failed: int = 0
    listings_seen: int = 0
    new: int = 0
    price_drops: int = 0
    price_rises: int = 0
    removed: int = 0
    filtered_out: int = 0
    notified: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    strategies: dict[str, str] = field(default_factory=dict)
    quiet: bool = False
    dry_run: bool = False

    @property
    def duration_s(self) -> float:
        return round(time.time() - self.started_at, 1)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "duration_s": self.duration_s,
            "searches_run": self.searches_run, "searches_failed": self.searches_failed,
            "listings_seen": self.listings_seen, "new": self.new,
            "price_drops": self.price_drops, "price_rises": self.price_rises,
            "removed": self.removed, "filtered_out": self.filtered_out,
            "notified": self.notified, "errors": self.errors[:10],
            "warnings": self.warnings[:10], "strategies": self.strategies,
            "quiet": self.quiet, "dry_run": self.dry_run,
        }

    def summary(self) -> str:
        return (f"{self.searches_run} search(es), {self.listings_seen} listing(s), "
                f"{self.new} new, {self.price_drops} price drop(s), "
                f"{self.removed} removed, {self.searches_failed} failed "
                f"in {self.duration_s}s")


def scrape_search(search, cfg: Config, fetcher: Fetcher) -> tuple[list[Listing], str]:
    """Fetch and parse every page of one search.  Raises on a hard failure."""
    scraping = cfg.get("scraping", {}) or {}
    max_pages = max(1, int(search.max_pages or scraping.get("max_pages", 3) or 1))
    per_page = int(scraping.get("results_per_page", 50) or 50)

    found: dict[str, Listing] = {}
    strategy = "none"
    referer = "https://www.autotrader.ca/"

    for page in range(1, max_pages + 1):
        url = page_url(search.url, page, per_page)
        response = fetcher.get(url, referer=referer)
        referer = url
        result = parse_search_page(response.text, url)
        if page == 1:
            strategy = result.strategy
            if not result.listings:
                # An empty first page is either a genuinely empty search or a
                # parser that has fallen behind the site.  Both need saying.
                log.warning("no listings on page 1 of %s (strategies tried: %s)",
                            search.name, result.candidates)
        fresh = 0
        for listing in result.listings:
            if listing.id in found:
                continue
            listing.search_id = search.id
            listing.search_name = search.name
            found[listing.id] = listing
            fresh += 1
        # A page that adds nothing new means we have reached the end of the
        # results; asking for page 4 of a 2-page search just wastes requests.
        if fresh == 0:
            break

    return list(found.values()), strategy


def _price_disagrees(listing: Listing, state: State) -> bool:
    """True when the results card has changed its mind about the price.

    Comparing the card against the stored (detail-page) price would re-fetch
    every car whose card and detail figures permanently disagree - once per
    run, forever. Comparing card against card only spends a request when the
    site actually changed something.
    """
    stored = state.listings.get(listing.id) or {}
    if listing.card_price is None:
        return False
    seen_card = stored.get("card_price")
    if seen_card is None:
        # Never confirmed against the listing page: worth doing once.
        return stored.get("price_source") != "detail" and stored.get("price") != listing.card_price
    return seen_card != listing.card_price


def enrich_listings(listings: list[Listing], cfg: Config, fetcher: Fetcher,
                    report: RunReport) -> None:
    """Fill in price/odometer/photos from each detail page's JSON-LD."""
    scraping = cfg.get("scraping", {}) or {}
    if not scraping.get("enrich_details", True):
        return
    budget = int(scraping.get("enrich_limit", 25) or 25)
    for listing in listings[:budget]:
        try:
            response = fetcher.get(listing.url, referer=listing.url)
            enrich(listing, response.text)
        except (FetchError, BlockedError) as exc:
            report.warnings.append(f"could not enrich {listing.id}: {exc}")
        except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
            report.warnings.append(f"enrichment error on {listing.id}: {exc}")


def run(cfg: Config | None = None, state: State | None = None, *,
        dry_run: bool = False, env: dict[str, str] | None = None,
        notify: bool = True, fetcher: Fetcher | None = None) -> RunReport:
    """Execute one full pass and return what happened."""
    cfg = cfg or Config.load()
    state = state or State.load()
    env = env if env is not None else dict(os.environ)
    report = RunReport(dry_run=dry_run)

    settings = cfg.get("notifications", {}) or {}
    notify_on = settings.get("notify_on", {}) or {}
    filter_conf = cfg.get("filters", {}) or {}
    scraping = cfg.get("scraping", {}) or {}
    archive_conf = cfg.get("archive", {}) or {}
    health_conf = cfg.get("health", {}) or {}

    fetcher = fetcher or Fetcher(
        timeout=int(scraping.get("timeout_seconds", 30) or 30),
        retries=int(scraping.get("retries", 3) or 0),
        delay_ms=int(scraping.get("delay_ms", 1200) or 0),
        user_agent=str(scraping.get("user_agent", "auto")),
    )

    changes: list[Change] = []
    blocked_searches: list[str] = []

    try:
        searches = cfg.active_searches
        if not searches:
            report.warnings.append(
                "No searches configured. Paste an AutoTrader search link into "
                "config.json, or run: python -m autotrader add <url>")
            return report

        for search in searches:
            try:
                listings, strategy = scrape_search(search, cfg, fetcher)
                report.strategies[search.id] = strategy
            except BlockedError as exc:
                report.searches_failed += 1
                report.errors.append(f"{search.name}: {exc}")
                blocked_searches.append(search.name)
                state.record_search_error(search.id, str(exc))
                continue
            except FetchError as exc:
                report.searches_failed += 1
                report.errors.append(f"{search.name}: {exc}")
                state.record_search_error(search.id, str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - one search must not end the run
                report.searches_failed += 1
                report.errors.append(f"{search.name}: unexpected error: {exc}")
                log.exception("unexpected failure scraping %s", search.name)
                state.record_search_error(search.id, str(exc))
                continue

            report.searches_run += 1
            report.listings_seen += len(listings)
            state.record_search_ok(search.id, len(listings), strategy)

            if not listings:
                report.warnings.append(
                    f"{search.name}: the page loaded but no listings were found "
                    f"(parser strategy: {strategy}).")

            # Enrich cars we have not recorded before - that is where the data
            # matters - plus any whose price no longer matches what we stored,
            # so a price move is confirmed against the listing page before it
            # is announced. Disputed prices go first: they are the ones an
            # exhausted enrichment budget would otherwise leave stale.
            disputed = [l for l in listings if state.known(l.id)
                        and _price_disagrees(l, state)]
            unknown = [l for l in listings if not state.known(l.id)]
            enrich_listings(disputed + unknown, cfg, fetcher, report)

            kept, dropped = filters.apply(listings, filter_conf)
            report.filtered_out += len(dropped)

            seen_ids = {l.id for l in listings}
            for listing in kept:
                change = state.record(listing)
                if change is None:
                    continue
                if change.kind == Change.NEW:
                    entry = state.listings.get(listing.id, {})
                    if entry.get("notified"):
                        continue          # imported from v1: known, stay quiet
                    report.new += 1
                    if notify_on.get("new", True):
                        changes.append(change)
                    archive_mod.archive_listing(listing, archive_conf, fetcher)
                elif change.kind == Change.PRICE_DROP:
                    if filters.is_significant_drop(
                        change.old_price or 0, change.new_price or 0,
                        float(settings.get("price_drop_min_pct", 1.0) or 0),
                        int(settings.get("price_drop_min_abs", 0) or 0),
                    ):
                        report.price_drops += 1
                        if notify_on.get("price_drop", True):
                            changes.append(change)
                elif change.kind == Change.PRICE_RISE:
                    report.price_rises += 1
                    if notify_on.get("price_rise", False):
                        changes.append(change)

            # Cars that were filtered out still count as "seen", so they do not
            # look like removals on the next pass.
            for listing in (l for l, _ in dropped):
                state.record(listing)
                state.mark_notified([listing.id])

            for change in state.mark_missing(search.id, seen_ids):
                report.removed += 1
                if notify_on.get("removed", False):
                    changes.append(change)

        # ---- notify -------------------------------------------------
        # Anything an earlier run detected but could not deliver (quiet hours,
        # a channel outage) is picked back up here rather than being lost.
        if not dry_run:
            already = {c.listing.id for c in changes}
            held = [c for c in state.pending_changes() if c.listing.id not in already]
            if held:
                report.warnings.append(f"re-sending {len(held)} held alert(s)")
                changes = held + changes

        report.quiet = notifiers.in_quiet_hours(settings)
        if changes and notify and not dry_run and not report.quiet:
            results = notifiers.dispatch(cfg, changes, report.to_dict(), env)
            report.notified = [str(r) for r in results]
            if any(r.ok for r in results):
                state.mark_notified(c.listing.id for c in changes)
            else:
                # Every channel failed. Hold on to them and try again next run.
                state.defer(changes)
            for result in results:
                if not result.ok and not result.skipped:
                    report.warnings.append(f"notification failed: {result}")
        elif changes and not dry_run and (report.quiet or not notify):
            state.defer(changes)
            report.warnings.append(
                f"{len(changes)} change(s) held"
                + (" until quiet hours end." if report.quiet else "."))
        elif changes and dry_run:
            report.notified = ["dry run: nothing sent"]

        # ---- health -------------------------------------------------
        _health_check(cfg, state, report, env, blocked_searches,
                      int(health_conf.get("alert_after_failures", 3) or 0), notify and not dry_run)

        # ---- housekeeping -------------------------------------------
        if not dry_run:
            pruned = archive_mod.prune(archive_conf)
            if pruned:
                report.warnings.append(f"pruned {len(pruned)} old archive folder(s)")
            state.prune()

    finally:
        fetcher.close()
        if not dry_run:
            # This is the whole point: state is written even if something above
            # blew up, so a failure costs one run, never the entire history.
            state.record_run(report.to_dict())
            try:
                state.save()
            except OSError as exc:
                log.error("could not save state: %s", exc)
                report.errors.append(f"could not save state: {exc}")

    return report


def _health_check(cfg: Config, state: State, report: RunReport,
                  env: dict[str, str], blocked: list[str],
                  threshold: int, may_notify: bool) -> None:
    """Tell the user when the bot itself is broken.

    v1 failed silently for a month.  A watcher that stops watching without
    saying so is worse than no watcher at all.
    """
    if threshold <= 0 or not may_notify:
        return
    broken = []
    for search in cfg.active_searches:
        health = state.search_health(search.id)
        failures = int(health.get("consecutive_failures", 0))
        if failures >= threshold:
            broken.append(f"- {search.name}: {failures} failed runs in a row. "
                          f"Last error: {health.get('last_error') or 'unknown'}")
    if not broken:
        return
    if not (cfg.get("notifications.notify_on.errors", True)):
        return
    body = ("Your AutoTrader watcher cannot read one or more searches:\n\n"
            + "\n".join(broken))
    if blocked:
        body += ("\n\nautotrader.ca served an anti-bot page. Try increasing "
                 "scraping.delay_ms or running the bot less often.")
    body += "\n\nNothing is lost - it will resume as soon as the pages load again."
    results = notifiers.alert(cfg, "AutoTrader watcher needs attention", body, env)
    report.warnings.append("sent a health alert: " + ", ".join(str(r) for r in results))
