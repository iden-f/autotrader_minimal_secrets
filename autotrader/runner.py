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
from pathlib import Path
from typing import Any

from . import archive as archive_mod
from . import diagnose, filters, notifiers, provision, shape, validate
from .config import Config
from .enrich import detail_from_html, enrich
from .http import BlockedError, BudgetExhausted, FetchError, Fetcher
from .listing import Listing
from .parser import looks_like_no_results, parse_search_page
from .state import Change, State
from .urls import page_url

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    started_at: float = field(default_factory=time.time)
    searches_run: int = 0
    searches_failed: int = 0
    requests_made: int = 0
    budget_exhausted: bool = False
    empty_parses: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    shape_drift: list[dict[str, Any]] = field(default_factory=list)
    disabled_channels: list[str] = field(default_factory=list)
    channel_results: list[Any] = field(default_factory=list)
    first_run: bool = False
    validation_ok: bool = True
    validation_report: str = ""
    provisioned: dict[str, Any] = field(default_factory=dict)
    listings_seen: int = 0
    new: int = 0
    price_drops: int = 0
    price_rises: int = 0
    removed: int = 0
    filtered_out: int = 0
    # Cars that passed every filter and simply have no figure on them.
    unpriced: int = 0
    priced: int = 0
    relisted: int = 0
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
            "unpriced": self.unpriced, "priced": self.priced,
            "relisted": self.relisted,
            "requests_made": self.requests_made,
            "budget_exhausted": self.budget_exhausted,
            "empty_parses": self.empty_parses,
            "diagnostics": self.diagnostics,
            "shape_drift": self.shape_drift,
            "disabled_channels": self.disabled_channels,
            "first_run": self.first_run,
            "validation_ok": self.validation_ok,
            "notified": self.notified, "errors": self.errors[:10],
            "warnings": self.warnings[:10], "strategies": self.strategies,
            "quiet": self.quiet, "dry_run": self.dry_run,
        }

    def summary(self) -> str:
        return (f"{self.searches_run} search(es), {self.listings_seen} listing(s), "
                f"{self.new} new, {self.price_drops} price drop(s), "
                f"{self.removed} removed, {self.searches_failed} failed, "
                f"{self.requests_made} request(s) in {self.duration_s}s")


# Below this a search is too small for "half of last time" to mean anything.
MIN_COUNT_FOR_COLLAPSE = 6

# Wording a listing page uses once the car is no longer for sale.
# A cap, so a search whose window rotates heavily cannot spend a whole run's
# budget proving that nothing has changed.
REMOVAL_CHECKS_PER_RUN = 12

# Runs that must independently reach "this page is gone" before it is believed.
GONE_EVIDENCE_NEEDED = 2

GONE_MARKERS = (
    "no longer available", "no longer for sale", "this listing has ended",
    "listing not found", "ad has been removed", "has been sold",
    "n'est plus disponible", "annonce n'existe plus", "page not found",
)


def _still_listed(url: str, fetcher: Fetcher) -> bool | None:
    """Is this car still on the site?  True, False, or None for "cannot tell".

    A car missing from a sample of a larger result set has not necessarily
    gone anywhere, so before announcing a sale we ask the listing page itself.
    """
    if not url:
        return None
    try:
        response = fetcher.get(url, allow_block=True)
    except BudgetExhausted:
        raise
    except FetchError as exc:
        # 404/410 is the site telling us plainly. Anything else - a timeout,
        # a 503 - says nothing about the car.
        text = str(exc)
        if "HTTP 404" in text or "HTTP 410" in text:
            return False
        return None
    except Exception:  # noqa: BLE001 - a check that fails proves nothing
        return None

    body = (response.text or "")[:200000].lower()
    if any(marker in body for marker in GONE_MARKERS):
        return False
    if detail_from_html(response.text, url=response.url) is not None:
        return True
    return None


@dataclass
class SearchResult:
    """What one search returned, and how much of it we actually saw."""

    listings: list[Listing]
    strategy: str
    said_no_results: bool
    candidates: dict[str, int]
    first_page: Any
    # True when the last page added nothing new, i.e. we reached the end of the
    # results rather than stopping at max_pages with more still to read.
    complete: bool


def scrape_search(search, cfg: Config, fetcher: Fetcher) -> "SearchResult":
    """Fetch and parse every page of one search.  Raises on a hard failure."""
    scraping = cfg.get("scraping", {}) or {}
    max_pages = max(1, int(search.max_pages or scraping.get("max_pages", 3) or 1))
    per_page = int(scraping.get("results_per_page", 50) or 50)

    found: dict[str, Listing] = {}
    strategy = "none"
    candidates: dict[str, int] = {}
    said_no_results = False
    first_page = None
    complete = False
    page_size = 0
    referer = "https://www.autotrader.ca/"

    for page in range(1, max_pages + 1):
        url = page_url(search.url, page, per_page)
        response = fetcher.get(url, referer=referer)
        referer = url
        result = parse_search_page(response.text, url)
        if page == 1:
            strategy = result.strategy
            candidates = dict(result.candidates)
            said_no_results = looks_like_no_results(response.text)
            first_page = response
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

        # Two ways to know we reached the end of the results, which is what
        # makes "this car is not here any more" mean anything: a page that
        # repeats what we already have, or a page that came back short.
        #
        # A page that parsed *nothing* proves neither. It is far more likely a
        # page we failed to read than a search that ends on an empty page, and
        # treating it as the end would let a broken parser authorise removals
        # for every car on the pages we never got to.
        if not result.listings:
            break
        if page == 1:
            page_size = len(result.listings)
        if fresh == 0 or len(result.listings) < page_size:
            complete = True
            break

    return SearchResult(list(found.values()), strategy, said_no_results,
                        candidates, first_page, complete)


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
    cap = int(scraping.get("enrich_limit", 25) or 25)
    # Leave enough budget for the remaining searches' result pages.
    room = max(0, getattr(fetcher, "budget_left", cap) - 5)
    for listing in listings[:min(cap, room)]:
        try:
            response = fetcher.get(listing.url, referer=listing.url)
            enrich(listing, response.text)
        except BudgetExhausted:
            report.warnings.append("stopped looking up listing details: "
                                   "request budget spent")
            break
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

    # Make the bot reachable and adopt any v1 secret before doing anything
    # else, so a fresh install works without being configured first.
    if not dry_run:
        try:
            report.provisioned = provision.bootstrap(cfg, env)
            for step in report.provisioned.get("steps", []):
                if step.get("changed"):
                    report.warnings.append(f"setup: {step['reason']}")
        except Exception as exc:  # noqa: BLE001 - never block a run on setup
            log.warning("automatic setup failed: %s", exc)
            report.warnings.append(f"automatic setup failed: {exc}")

    # "First run" means no run has ever succeeded, so the parser has never been
    # shown to work against the live site.
    report.first_run = not any(r.get("ok") for r in (state.data.get("runs") or []))

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
        budget=int(scraping.get("request_budget", 250) or 0),
    )

    changes: list[Change] = []
    blocked_searches: list[str] = []
    assessments: list[validate.Assessment] = []
    drifted: list[tuple[str, list[str], dict[str, Any]]] = []

    def queue(change: Change) -> None:
        """Record a change as owed to the user, then remember to send it.

        Writing the intent to state straight away means an interrupt between
        here and the notification (an archive failure, a killed CI job) costs
        nothing: the next run finds it still pending and delivers it.
        """
        changes.append(change)
        if not dry_run:
            state.defer([change])

    try:
        searches = cfg.active_searches
        if not searches:
            report.warnings.append(
                "No searches configured. Paste an AutoTrader search link into "
                "config.json, or run: python -m autotrader add <url>")
            return report

        for search in searches:
            try:
                result = scrape_search(search, cfg, fetcher)
                listings = result.listings
                strategy = result.strategy
                said_no_results = result.said_no_results
                candidates = result.candidates
                first_page = result.first_page
                report.strategies[search.id] = strategy
            except BudgetExhausted as exc:
                # Not a failure: we deliberately stopped. Leave the remaining
                # searches for the next run rather than marking them broken.
                report.warnings.append(str(exc))
                report.budget_exhausted = True
                break
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
            # Read before recording, or "the usual count" is this run's count
            # and the collapse check below can never fire.
            usual_count = int((state.search_health(search.id) or {}).get(
                "last_count", 0) or 0)
            state.record_search_ok(search.id, len(listings), strategy)

            assessments.append(validate.assess(
                listings, strategy, search.name,
                said_no_results=said_no_results,
                candidates=candidates))

            if health_conf.get("watch_page_shape", True) and first_page is not None:
                try:
                    current = shape.fingerprint(first_page.text, strategy,
                                                candidates, len(listings))
                    reasons, serious = shape.compare(
                        state.search_shape(search.id), current)
                    state.record_shape(search.id, current)
                    if reasons:
                        # Always keep the evidence when the shape moves: by the
                        # time the parser actually breaks, the page that broke
                        # it is long gone.
                        report.shape_drift.append(
                            {"search": search.name, "reasons": reasons,
                             "serious": serious})
                        try:
                            data = diagnose.capture(
                                first_page.url, first_page.text,
                                first_page.status, first_page.elapsed_ms,
                                search.name)
                            report.diagnostics.append(
                                str(diagnose.write(data, search.id)))
                            if serious:
                                # A description of the page is enough to see
                                # that something moved; only the page itself is
                                # enough to rewrite a strategy against.
                                raw = diagnose.write_raw(first_page.text, search.id)
                                if raw:
                                    report.diagnostics.append(str(raw))
                        except Exception as exc:  # noqa: BLE001
                            log.warning("could not capture the page: %s", exc)
                    if serious:
                        drifted.append((search.name, reasons, current))
                except Exception as exc:  # noqa: BLE001 - never fatal
                    log.warning("shape check failed for %s: %s", search.name, exc)

            if not listings and said_no_results:
                # The site itself says the search matched nothing. That is a
                # narrow search, not a broken parser.
                report.warnings.append(
                    f"{search.name}: AutoTrader reports no results for this search.")
            elif not listings:
                # A 200 with listings we could not read, and no "no results"
                # message: every strategy has fallen behind the site. Capture
                # what the page actually looked like, because a log line saying
                # "found 0" is almost useless to fix a parser from.
                if first_page is not None:
                    try:
                        data = diagnose.capture(
                            first_page.url, first_page.text, first_page.status,
                            first_page.elapsed_ms, search.name)
                        written = diagnose.write(data, search.id)
                        report.diagnostics.append(str(written))
                        raw = diagnose.write_raw(first_page.text, search.id)
                        if raw:
                            report.diagnostics.append(str(raw))
                        log.warning("wrote a page capture to %s", written)
                    except Exception as exc:  # noqa: BLE001 - never fatal
                        log.warning("could not capture the page: %s", exc)
                report.errors.append(
                    f"{search.name}: the page loaded ({strategy}) but no listings "
                    f"could be read, and the site did not say the search was empty. "
                    f"Run: python -m autotrader doctor --live")
                report.empty_parses.append(search.name)
                state.record_search_error(search.id, "HTTP 200 but zero listings parsed")

            # Enrich cars we have not recorded before - that is where the data
            # matters - plus any whose price no longer matches what we stored,
            # so a price move is confirmed against the listing page before it
            # is announced. Disputed prices go first: they are the ones an
            # exhausted enrichment budget would otherwise leave stale.
            disputed = [l for l in listings if state.known(l.id)
                        and _price_disagrees(l, state)]
            unknown = [l for l in listings if not state.known(l.id)]
            # A card that shows no price often sits above a listing page that
            # does, and a dealer who withheld a figure last week may have
            # published one since. Re-checking is worth a request - but only a
            # few times, or a genuinely call-for-price car would be re-fetched
            # once a run, forever, which is the churn the disputed-price rule
            # above exists to avoid.
            recheck_limit = int(scraping.get("unpriced_rechecks", 3) or 0)
            still_unpriced = [
                l for l in listings
                if l.price is None and state.known(l.id)
                and (state.listings.get(l.id) or {}).get("price") is None
                and int((state.listings.get(l.id) or {}).get("price_checks", 0)) < recheck_limit
            ]
            for listing in still_unpriced:
                entry = state.listings.get(listing.id) or {}
                entry["price_checks"] = int(entry.get("price_checks", 0)) + 1
            enrich_listings(disputed + unknown + still_unpriced, cfg, fetcher, report)

            if report.first_run and not assessments[-1].trustworthy:
                # Keep the evidence: a parse that produced nonsense is as hard
                # to fix from a log line as one that produced nothing.
                if first_page is not None and not report.diagnostics:
                    try:
                        data = diagnose.capture(
                            first_page.url, first_page.text, first_page.status,
                            first_page.elapsed_ms, search.name)
                        report.diagnostics.append(str(diagnose.write(data, search.id)))
                        raw = diagnose.write_raw(first_page.text, search.id)
                        if raw:
                            report.diagnostics.append(str(raw))
                    except Exception as exc:  # noqa: BLE001
                        log.warning("could not capture the page: %s", exc)
                # The parser has never been shown to work against the live
                # site, and this parse looks wrong. Recording it would fill
                # state with debris and archive garbage, and every one of those
                # bad rows would later have to be unpicked by hand. Stop here
                # instead - nothing is written, so the next run simply retries.
                report.errors.append(
                    f"{search.name}: first-run parse check failed - "
                    + "; ".join(assessments[-1].concerns))
                continue

            # Each search may override the global filters and alert rules.
            rules = cfg.rules_for(search)
            search_filters = rules["filters"]
            search_notify = rules["notify_on"]

            kept, unpriced, dropped = filters.apply(listings, search_filters)
            report.filtered_out += len(dropped)
            report.unpriced += len(unpriced)

            seen_ids = {l.id for l in listings}
            for listing in kept:
                change = state.record(listing, filtered=False)
                if change is None:
                    continue
                if change.kind == Change.NEW:
                    entry = state.listings.get(listing.id, {})
                    if entry.get("notified"):
                        continue          # imported from v1: known, stay quiet
                    report.new += 1
                    if search_notify.get("new", True):
                        queue(change)
                    archive_mod.archive_listing(listing, archive_conf, fetcher)
                elif change.kind == Change.PRICE_DROP:
                    if filters.is_significant_drop(
                        change.old_price or 0, change.new_price or 0,
                        rules["price_drop_min_pct"], rules["price_drop_min_abs"],
                    ):
                        report.price_drops += 1
                        if search_notify.get("price_drop", True):
                            queue(change)
                elif change.kind == Change.PRICE_RISE:
                    report.price_rises += 1
                    if search_notify.get("price_rise", False):
                        queue(change)
                elif change.kind == Change.PRICED:
                    report.priced += 1
                    if search_notify.get("priced", True):
                        queue(change)
                elif change.kind == Change.RELISTED:
                    report.relisted += 1
                    if search_notify.get("relisted", False):
                        queue(change)

            # "Call for price" cars are tracked and stay visible; they are not
            # rejections, they are cars we cannot judge yet. require_price now
            # decides whether they are worth an alert, not whether they exist.
            # None means "not set": follow require_price, which is the switch
            # people actually reach for. An explicit true or false wins.
            want_unpriced = search_notify.get("unpriced")
            tell_me_about_unpriced = (
                not bool(search_filters.get("require_price"))
                if want_unpriced is None else bool(want_unpriced))
            for listing in unpriced:
                change = state.record(listing)
                if change is None:
                    continue
                if change.kind == Change.NEW:
                    entry = state.listings.get(listing.id, {})
                    if entry.get("notified"):
                        continue
                    report.new += 1
                    if tell_me_about_unpriced and search_notify.get("new", True):
                        queue(change)
                    else:
                        # Seen, recorded, deliberately quiet - so it cannot
                        # come back as "new" once a price appears on it.
                        state.mark_notified([listing.id])
                    archive_mod.archive_listing(listing, archive_conf, fetcher)
                elif change.kind == Change.PRICED:
                    # A car that was call-for-price now has a figure. That is
                    # worth hearing about whatever require_price says: it is
                    # the moment the car becomes judgeable.
                    report.priced += 1
                    if search_notify.get("priced", True):
                        queue(change)
                elif change.kind == Change.RELISTED:
                    report.relisted += 1
                    if tell_me_about_unpriced and search_notify.get("relisted", False):
                        queue(change)

            # Cars that were filtered out still count as "seen", so they do not
            # look like removals on the next pass.
            for listing, why in dropped:
                state.record(listing, filtered=True, filter_reason=why)
                state.mark_notified([listing.id])

            # A car is only "gone" if we actually looked and did not find it.
            # A page we could not read is not evidence of anything, and a page
            # that collapsed to a fraction of its usual size is evidence of a
            # half-working parser far more often than of a dealer clearing
            # their lot. Left unchecked, four broken runs empty the dashboard
            # and fire a removal alert for every car being watched.
            trustworthy = bool(listings) or said_no_results
            if (trustworthy and not report.first_run
                    and usual_count >= MIN_COUNT_FOR_COLLAPSE
                    and len(listings) * 2 < usual_count):
                trustworthy = False
                report.warnings.append(
                    f"{search.name}: only {len(listings)} of the usual "
                    f"{usual_count} listings were read, so removals are not "
                    f"being called this run.")
            if trustworthy:
                # When the search has more results than we read, absence from
                # our sample is not evidence: the site rotates which listings
                # surface, and cars drop in and out of the window every run.
                # Ask the listing page before calling a sale.
                confirm = None
                if not result.complete:
                    checked = [0]

                    def confirm(entry: dict[str, Any]) -> bool | None:
                        if checked[0] >= REMOVAL_CHECKS_PER_RUN:
                            return None       # ask again next run
                        checked[0] += 1
                        try:
                            live = _still_listed(str(entry.get("url") or ""), fetcher)
                        except BudgetExhausted:
                            return None
                        if live is None:
                            return None
                        if live:
                            entry.pop("gone_evidence", None)
                            return False
                        # The page says gone - but a listing URL carries an SEO
                        # slug in front of its id, and that slug changes when
                        # the seller edits the ad. A single 404 could be a
                        # retitled car as easily as a sold one, so it takes two
                        # runs saying the same thing. The cost of being wrong
                        # here is telling someone a car sold when it did not.
                        seen = int(entry.get("gone_evidence", 0)) + 1
                        entry["gone_evidence"] = seen
                        if seen < GONE_EVIDENCE_NEEDED:
                            return None
                        entry.pop("gone_evidence", None)
                        return True

                for change in state.mark_missing(search.id, seen_ids,
                                                 confirm=confirm):
                    report.removed += 1
                    if search_notify.get("removed", False):
                        queue(change)
            # Otherwise nothing happens at all - not even a miss. The grace
            # period exists to absorb a car dropping off one page, not to be
            # spent on a page we could not read.

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
            report.channel_results.extend(results)
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

        # ---- first-run self-check ------------------------------------
        if report.first_run and assessments:
            report.validation_ok = all(a.trustworthy for a in assessments)
            report.validation_report = validate.report_markdown(
                assessments, ok=report.validation_ok)
            _publish_validation(report, assessments)
            if notify and not dry_run:
                subject = ("AutoTrader watcher is working"
                           if report.validation_ok
                           else "AutoTrader watcher: the first check looks wrong")
                results = notifiers.alert(
                    cfg, subject, validate.report_text(assessments, ok=report.validation_ok), env)
                report.channel_results.extend(results)
                report.warnings.append("sent the first-run check: "
                                       + ", ".join(str(r) for r in results))

        # ---- shape drift ---------------------------------------------
        if drifted and notify and not dry_run:
            body = "\n\n".join(shape.describe(name, reasons, current)
                                 for name, reasons, current in drifted)
            results = notifiers.alert(
                cfg, "AutoTrader changed how its pages are built", body, env)
            report.channel_results.extend(results)
            report.warnings.append("sent a page-shape warning: "
                                   + ", ".join(str(r) for r in results))

        # ---- health -------------------------------------------------
        _health_check(cfg, state, report, env, blocked_searches,
                      int(health_conf.get("alert_after_failures", 3) or 0), notify and not dry_run)

        # ---- retire channels that cannot work ------------------------
        if notify and not dry_run:
            _retire_dead_channels(
                cfg, state, report, env,
                int(health_conf.get("disable_channel_after", 2) or 0))

        # ---- housekeeping -------------------------------------------
        if not dry_run:
            forgotten = state.forget_searches({s.id for s in cfg.searches})
            if forgotten:
                report.warnings.append(
                    f"forgot {len(forgotten)} search(es) no longer configured")
            pruned = archive_mod.prune(archive_conf)
            if pruned:
                report.warnings.append(f"pruned {len(pruned)} old archive folder(s)")
            state.prune()

    finally:
        report.requests_made = getattr(fetcher, "spent", 0)
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


def _retire_dead_channels(cfg: Config, state: State, report: RunReport,
                          env: dict[str, str], threshold: int) -> None:
    """Switch off a channel whose credentials are being rejected.

    A wrong password is wrong every time, so retrying it every half hour just
    prints the same red line forever and buries the failures that matter.

    Judged once per run over every send attempted, not per message: a channel
    that fails an alert is as broken as one that fails a digest, and a quiet
    week should not keep a dead channel alive.
    """
    if not report.channel_results:
        return

    outcomes: dict[str, dict[str, Any]] = {}
    for result in report.channel_results:
        if result.skipped:
            continue
        entry = outcomes.setdefault(result.channel, {"ok": False, "permanent": False,
                                                     "detail": ""})
        if result.ok:
            entry["ok"] = True
        else:
            entry["detail"] = entry["detail"] or result.detail
            if result.permanent:
                entry["permanent"] = True

    alive = sorted(name for name, o in outcomes.items() if o["ok"])

    for channel, outcome in sorted(outcomes.items()):
        strikes = state.record_channel(channel, outcome["ok"], outcome["detail"],
                                       outcome["permanent"])
        if outcome["ok"] or threshold <= 0 or strikes < threshold:
            continue

        cfg.set(f"notifications.channels.{channel}.enabled", False)
        cfg.set(f"notifications.channels.{channel}.disabled_reason",
                f"Switched off automatically after {strikes} runs: "
                f"{outcome['detail'][:160]}")
        state.mark_channel_disabled(channel)
        report.disabled_channels.append(channel)
        try:
            cfg.save()
        except OSError as exc:
            log.warning("could not persist the disabled channel: %s", exc)

        # Say so once, through whatever still works. Deliberately not recorded
        # against the channels, or this notice would count as its own strike.
        if alive:
            notifiers.alert(
                cfg,
                f"Switched off {channel} notifications",
                f"{channel} has been rejecting our credentials:\n\n"
                f"  {outcome['detail'][:300]}\n\n"
                f"It failed that way {strikes} runs in a row, so it is now off "
                f"and will stop filling the log.\n\n"
                f"Still delivering via: {', '.join(alive)}.\n\n"
                f"To bring it back, fix the credentials and set "
                f"notifications.channels.{channel}.enabled to true in "
                f"config.json, or switch it on in the dashboard's Settings tab.",
                env)
        report.warnings.append(
            f"switched off {channel} after {strikes} runs of credential failures")


def _publish_validation(report: RunReport, assessments: list[validate.Assessment]) -> None:
    """Leave the first-run check where CI and the user will both find it."""
    try:
        Path("validation-report.md").write_text(report.validation_report, encoding="utf-8")
    except OSError as exc:
        log.warning("could not write validation-report.md: %s", exc)

    # GitHub renders this at the top of the run page.
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(report.validation_report + "\n")
        except OSError as exc:
            log.warning("could not write the job summary: %s", exc)

    print("\n" + validate.report_text(assessments, ok=report.validation_ok) + "\n")


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
    if report.empty_parses:
        body += ("\n\nThe pages loaded but nothing could be read from them, which "
                 "usually means AutoTrader changed its markup. Run "
                 "'python -m autotrader doctor --live' to see which parser "
                 "strategies still work.")
    body += "\n\nNothing is lost - it will resume as soon as the pages load again."
    results = notifiers.alert(cfg, "AutoTrader watcher needs attention", body, env)
    report.channel_results.extend(results)
    report.warnings.append("sent a health alert: " + ", ".join(str(r) for r in results))
