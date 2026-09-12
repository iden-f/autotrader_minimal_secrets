"""Durable state: what we have seen, what it cost, and what changed.

The old bot kept a flat list of listing ids in ``seen_listings.json`` and wrote
it once, at the very end of a successful run.  Any error before that point -
including a single listing failing to archive - threw the whole run away and
re-notified everything on the next pass.

This version keeps a record per car (so it can spot price drops and removals),
writes atomically, and is saved by the runner in a ``finally`` block so a crash
mid-run can never cost more than the work of that run.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .listing import Listing

# The opening of every "we said nothing because a rule of yours hid it"
# reason. It is a prefix rather than a flag because the rest of the sentence
# names the rule, and because the dashboard and the ledger quote it verbatim.
# One definition: the runner and the invariants both used to spell it out
# themselves, which is two places for it to drift from the one that writes it.
HIDDEN_REASON_PREFIX = "hidden by your rules: "

log = logging.getLogger(__name__)

STATE_PATH = Path(os.getenv("AUTOTRADER_STATE", "state.json"))
LEGACY_SEEN_PATH = Path("seen_listings.json")
MAX_RUN_HISTORY = 60
# Keys record() sets itself; everything else on an entry is carried forward.
_MANAGED_KEYS = {"first_seen", "last_seen", "status", "notified", "price_history",
                 "price_disputed", "imported_from"}
MAX_PRICE_POINTS = 40


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Change:
    """Something worth telling the user about."""

    NEW = "new"
    PRICE_DROP = "price_drop"
    PRICE_RISE = "price_rise"
    PRICED = "priced"
    REMOVED = "removed"
    RELISTED = "relisted"
    # A car that was here all along and did not qualify, and now does. The
    # price ceiling alone hides 114 of the 192 cars on this market, so one of
    # them crossing the line is not a new listing and not a price drop the
    # user ever saw - it is the only moment they would ever hear about that
    # car, and until now nothing generated an event for it.
    QUALIFIED = "qualified"

    def __init__(self, kind: str, listing: Listing, *, old_price: int | None = None,
                 new_price: int | None = None) -> None:
        self.kind = kind
        self.listing = listing
        self.old_price = old_price
        self.new_price = new_price

    @property
    def delta(self) -> int | None:
        if self.old_price is None or self.new_price is None:
            return None
        return self.new_price - self.old_price

    @property
    def delta_pct(self) -> float | None:
        if not self.old_price or self.delta is None:
            return None
        return self.delta / self.old_price * 100.0

    def describe(self) -> str:
        if self.kind == Change.NEW:
            return "New listing"
        if self.kind in (Change.PRICE_DROP, Change.PRICE_RISE):
            delta = self.delta or 0
            arrow = "down" if delta < 0 else "up"
            pct = self.delta_pct or 0.0
            return (f"Price {arrow} ${abs(delta):,} ({abs(pct):.1f}%) - "
                    f"${self.old_price:,} to ${self.new_price:,}")
        if self.kind == Change.PRICED:
            return (f"Price published - ${self.new_price:,}"
                    if self.new_price else "Price published")
        if self.kind == Change.REMOVED:
            return "Listing removed"
        if self.kind == Change.RELISTED:
            # Relisted at a different number is a different event from
            # relisted. A seller who took a car down and put it back $4,000
            # cheaper has told you something; one who put it back unchanged
            # has told you the listing expired.
            if self.delta:
                direction = "cheaper" if self.delta < 0 else "dearer"
                return (f"Back on the market ${abs(self.delta):,} {direction} "
                        f"- ${self.old_price:,} to ${self.new_price:,}")
            return "Back on the market"
        if self.kind == Change.QUALIFIED:
            return "Now within your rules"
        return self.kind

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "listing_id": self.listing.id,
            "old_price": self.old_price,
            "new_price": self.new_price,
            "delta": self.delta,
            "at": utcnow(),
        }


class State:
    def __init__(self, data: dict[str, Any] | None = None, path: Path = STATE_PATH) -> None:
        self.data: dict[str, Any] = data or {
            "version": 2, "updated_at": None, "listings": {}, "searches": {}, "runs": [],
        }
        self.data.setdefault("listings", {})
        self.data.setdefault("searches", {})
        self.data.setdefault("runs", [])
        self.path = path

    # ---------------- persistence ----------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> "State":
        path = Path(path) if path else STATE_PATH
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(raw, dict) and raw.get("version"):
                    state = cls(raw, path)
                    state.upgrade()
                    return state
                log.warning("%s has no version marker; starting fresh", path)
            except json.JSONDecodeError as exc:
                # Never crash on a corrupt state file - that would re-notify
                # everything.  Keep the bad file for inspection and start over.
                broken = path.with_suffix(".corrupt.json")
                try:
                    path.replace(broken)
                    log.error("%s was corrupt (%s); moved to %s", path, exc, broken)
                except OSError:
                    log.error("%s was corrupt and could not be moved: %s", path, exc)
        state = cls(path=path)
        state.import_legacy()
        return state

    def upgrade(self) -> dict[str, int]:
        """Fill in bookkeeping that older entries were written without.

        Idempotent, and deliberately conservative: it reconstructs records the
        code used to keep only implicitly, and never invents a delivery for a
        car that was genuinely never handled - those stay visible as
        violations, because that is exactly the failure worth seeing.
        """
        filled = {"unpriced": 0, "quiet_reason": 0, "notified_at": 0}
        for entry in self.listings.values():
            if "unpriced" not in entry:
                entry["unpriced"] = entry.get("price") is None
                filled["unpriced"] += 1
            if entry.get("notified_at") or entry.get("quiet_reason") or entry.get("pending"):
                continue
            if entry.get("imported_from") or entry.get("migrated_from"):
                continue          # history, never a candidate for an alert
            if entry.get("filtered"):
                why = entry.get("filter_reason") or "one of your rules"
                entry["quiet_reason"] = f"{HIDDEN_REASON_PREFIX}{why}"[:200]
                filled["quiet_reason"] += 1
            elif entry.get("notified"):
                # It was handled - delivered or deliberately quiet - before the
                # bot recorded which. Say when it was last seen and mark the
                # timestamp as reconstructed rather than observed.
                entry["notified_at"] = entry.get("last_seen") or entry.get("first_seen")
                entry["notified_at_backfilled"] = True
                filled["notified_at"] += 1

        # And take the mark off anything that has since been delivered about
        # for real. The reconstruction copies last_seen (or first_seen); a
        # stamp that is neither was written by a run that watched itself send,
        # so it is observed and saying otherwise understates what is known.
        # The car this was found on is gone from the market, so no future
        # delivery would ever have cleared it.
        for entry in self.listings.values():
            if not entry.get("notified_at_backfilled"):
                continue
            reconstructed = entry.get("last_seen") or entry.get("first_seen")
            if entry.get("notified_at") and entry["notified_at"] != reconstructed:
                entry.pop("notified_at_backfilled", None)
                filled["notified_at_observed"] = \
                    filled.get("notified_at_observed", 0) + 1
        return filled

    def import_legacy(self, legacy_path: Path = LEGACY_SEEN_PATH) -> int:
        """Adopt v1's seen_listings.json so upgrading does not re-notify.

        Ids are recorded as already-seen with no price, which means the first
        run after the upgrade stays quiet instead of announcing 50 old cars.
        """
        if not legacy_path.exists():
            return 0
        try:
            ids = json.loads(legacy_path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            return 0
        if not isinstance(ids, list):
            return 0
        added = 0
        for raw_id in ids:
            lid = str(raw_id).strip()
            if not lid or lid in self.data["listings"]:
                continue
            self.data["listings"][lid] = {
                "id": lid,
                "url": "",
                "title": f"AutoTrader listing {lid}",
                "first_seen": None,
                "last_seen": None,
                "status": "archived",
                "notified": True,
                "imported_from": "seen_listings.json",
                "price_history": [],
            }
            added += 1
        if added:
            log.info("imported %d ids from %s", added, legacy_path)
        return added

    def save(self, path: Path | None = None) -> Path:
        """Write atomically so an interrupted run cannot corrupt state."""
        target = Path(path) if path else self.path
        self.data["updated_at"] = utcnow()
        self.data["version"] = 2
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, target)
        return target

    # ---------------- listings ----------------

    @property
    def listings(self) -> dict[str, dict[str, Any]]:
        return self.data["listings"]

    def known(self, listing_id: str) -> bool:
        return listing_id in self.listings

    def get_listing(self, listing_id: str) -> Listing | None:
        raw = self.listings.get(listing_id)
        return Listing.from_dict(raw) if raw else None

    def record(self, listing: Listing, *, seen_at: str | None = None,
               filtered: bool = False, filter_reason: str = "") -> Change | None:
        """Store a listing and return what changed about it, if anything.

        Returns a Change for a genuinely new car or a price move, and None when
        nothing noteworthy happened.
        """
        now = seen_at or utcnow()
        existing = self.listings.get(listing.id)

        if existing is None:
            entry = listing.to_dict()
            entry.update({
                "first_seen": now, "last_seen": now, "status": "active",
                "notified": False, "filtered": filtered,
                "filter_reason": filter_reason,
                # A fact about the car, not about the filters: it is tracked
                # either way, and this is what tells the dashboard and the
                # "price published" alert apart from a price drop.
                "unpriced": entry.get("price") is None,
                "price_history": ([{"at": now, "price": listing.price}]
                                  if listing.price is not None else []),
            })
            self.listings[listing.id] = entry
            return Change(Change.NEW, listing)

        # An id we imported from v1 has no data: fill it in, but stay quiet.
        was_imported = existing.get("imported_from") and not existing.get("url")
        old_price = existing.get("price")
        merged = Listing.from_dict(existing)
        merged.merge(listing)
        # Your own mark travels with the car, so a notifier can lead with a
        # shortlisted one without reaching back into state to ask.
        merged.shortlisted = bool((existing.get("you") or {}).get("shortlisted"))
        for field in ("price", "mileage_km", "title", "url", "images", "search_id",
                      "search_name", "source", "enriched", "card_price"):
            value = getattr(listing, field)
            if value not in (None, "", [], False):
                setattr(merged, field, value)

        entry = merged.to_dict()
        # to_dict() only knows Listing fields, so anything else we keep on an
        # entry - a held notification, the miss counter, migration markers -
        # would be silently dropped here. Carry it across.
        for key, value in existing.items():
            if key not in entry and key not in _MANAGED_KEYS:
                entry[key] = value
        entry["first_seen"] = existing.get("first_seen") or now
        entry["last_seen"] = now
        entry["status"] = "active"
        # Seeing a car resets the "how many runs has it been gone" counter, so
        # a car that reappears cannot be carried across the removal threshold
        # by misses it accrued before.
        entry["misses"] = 0
        # A car we had written off has come back. Worth counting even when it
        # is not worth an alert: a watcher that keeps resurrecting cars is
        # telling you its removal detection is wrong.
        came_back = existing.get("status") == "gone"
        if came_back:
            entry["relisted_at"] = now
        entry["filtered"] = filtered
        # Why it was hidden, so the dashboard can say so instead of just
        # showing a smaller number than the site does.
        entry["filter_reason"] = filter_reason
        was_unpriced = (existing.get("unpriced") if "unpriced" in existing
                        else existing.get("price") is None)
        entry.pop("removed_at", None)
        entry["notified"] = True if was_imported else existing.get("notified", False)
        # A rule that no longer applies cannot still be the reason this car
        # was kept quiet. Leaving it there is a contradiction the invariants
        # catch and fail the run on - a $139,888 car hidden by a $100,000
        # ceiling turned up with no price on its card, which is not a
        # rejection, so it stopped being filtered while keeping the ceiling as
        # its excuse, and every run for thirty-two hours failed on it.
        #
        # Clearing `notified` with it is the point rather than a side effect:
        # the car now has nothing accounting for it, so the run must either
        # announce it or say out loud why it is staying quiet. Loudly wrong
        # beats quietly wrong.
        if not filtered and str(existing.get("quiet_reason") or "").startswith(
                HIDDEN_REASON_PREFIX):
            entry.pop("quiet_reason", None)
            entry["notified"] = False
        # The first photos on a car that had none. Nine of the cars live right
        # now show nothing at all, and a car you cannot see is a car you
        # cannot judge - so the moment it becomes possible to look at it is
        # worth recording. Deliberately not an alert: nothing about the car
        # changed, only what can be seen of it, and a push notification for
        # "you can now look at this" is a push notification too many.
        had_photos = bool(existing.get("images"))
        if not had_photos and listing.images and not was_imported:
            entry["photos_at"] = now

        # The moment a hidden car crosses back into the rules. Recorded
        # whatever else happened on this observation, so the dashboard can
        # mark it even when the headline is the price drop that caused it.
        crossed_in = bool(existing.get("filtered")) and not filtered \
            and not was_imported and not came_back
        if crossed_in:
            entry["qualified_at"] = now
            entry["qualified_from"] = str(existing.get("filter_reason") or "")[:200]
        history = list(existing.get("price_history") or [])

        change: Change | None = None
        old_source = existing.get("price_source") or ""
        new_source = listing.price_source or ""
        # A search card and a detail page can quote different figures for the
        # same car (taxes, incentives, "from" pricing). Comparing across the
        # two produced a phantom price drop on every single run, so a change is
        # only believed when both observations come from the same place.
        comparable = (old_price is None or not old_source or not new_source
                      or old_source == new_source)

        if listing.price is not None and listing.price != old_price and not comparable:
            # Keep the older, better-sourced figure until we can re-check the
            # detail page; the runner puts this listing first in the enrichment
            # queue precisely so that happens on the next pass.
            entry["price"] = old_price
            entry["price_source"] = old_source
            entry["price_disputed"] = listing.price
        elif listing.price is not None and listing.price != old_price:
            history.append({"at": now, "price": listing.price})
            entry.pop("price_disputed", None)
            if came_back and not was_imported:
                # Coming back at a different number is one event, not two, and
                # it is the stronger one. This branch used to sit behind the
                # price-drop test, which requires only that an old price
                # exists - so a car that was pulled and relisted $4,000
                # cheaper was reported as an ordinary price drop and the fact
                # that it had been withdrawn at all was thrown away. A seller
                # who takes a car down and puts it back cheaper is telling you
                # something a seller who edits a live listing is not.
                change = Change(Change.RELISTED, merged, old_price=old_price,
                                new_price=listing.price)
            elif old_price is not None and not was_imported:
                kind = Change.PRICE_DROP if listing.price < old_price else Change.PRICE_RISE
                change = Change(kind, merged, old_price=old_price, new_price=listing.price)
            elif was_unpriced and not was_imported:
                # A "call for price" car has put a figure on itself. That is
                # not a price drop - there is nothing to compare against - but
                # it is the moment the car becomes judgeable, which is the
                # whole reason for tracking it while it had no price.
                entry["priced_at"] = now
                change = Change(Change.PRICED, merged, new_price=listing.price)
        else:
            entry.pop("price_disputed", None)
            if not history and listing.price is not None:
                history.append({"at": now, "price": listing.price})
            if came_back and not was_imported:
                change = Change(Change.RELISTED, merged, old_price=old_price,
                                new_price=listing.price)

        # A car that crossed into the rules with no other change to report.
        # When a price drop caused the crossing, the drop is the better story
        # and already carries the numbers - announcing both would be the same
        # car twice in one digest.
        if change is None and crossed_in:
            change = Change(Change.QUALIFIED, merged, old_price=old_price,
                            new_price=entry.get("price"))

        entry["price_history"] = history[-MAX_PRICE_POINTS:]
        # Set last, from the entry rather than from this observation. A results
        # card that shows no price does not make a car call-for-price when we
        # already have one from its listing page - and the two disagreeing is
        # how a car with a price ends up flagged as having none.
        entry["unpriced"] = entry.get("price") is None
        if was_imported:
            entry.pop("imported_from", None)
        self.listings[listing.id] = entry
        return change

    def silence(self, listing_id: str, reason: str) -> None:
        """Record that we deliberately said nothing about this car, and why.

        The failure this exists to make impossible is a car that matters and
        never gets mentioned. Every listing has to end up in exactly one of
        three states - delivered, owed, or deliberately quiet with a reason -
        so "we never told you" is always a decision someone can read back,
        never an accident nobody noticed.
        """
        entry = self.listings.get(listing_id)
        if entry is None:
            return
        entry["quiet_reason"] = reason[:200]
        if entry.get("pending"):
            # It is already owed an alert. Deciding to keep quiet about it from
            # now on is not a licence to cancel a debt: a price drop detected
            # while the car still qualified is real news, and dropping it here
            # is exactly the silent loss this whole path exists to prevent.
            return
        entry["notified"] = True

    def mark_notified(self, listing_ids: Iterable[str]) -> None:
        for lid in listing_ids:
            if lid in self.listings:
                self.listings[lid]["notified_at"] = utcnow()
                self.listings[lid].pop("quiet_reason", None)
                self.listings[lid]["notified"] = True
                self.listings[lid].pop("pending", None)
                # This time was observed, not inferred. The flag is set when a
                # delivery predates the bot recording them and the timestamp
                # had to be reconstructed from last_seen; leaving it on a car
                # the bot has since genuinely delivered about makes the ledger
                # describe a real alert as a guess.
                self.listings[lid].pop("notified_at_backfilled", None)

    def defer(self, changes: Iterable[Change]) -> None:
        """Remember an alert we could not send yet, so it is not lost.

        Quiet hours and a channel outage both need this: without it, a change
        detected once and not delivered would never be mentioned again, because
        the next run sees no change at all.
        """
        for change in changes:
            entry = self.listings.get(change.listing.id)
            if entry is None:
                continue
            entry["notified"] = False
            entry["pending"] = {"kind": change.kind, "old_price": change.old_price,
                                "new_price": change.new_price, "since": utcnow()}

    def pending_changes(self) -> list[Change]:
        """Alerts that were worked out on an earlier run but never delivered."""
        out: list[Change] = []
        for entry in self.listings.values():
            pending = entry.get("pending")
            if not pending:
                continue
            out.append(Change(pending.get("kind", Change.NEW),
                              Listing.from_dict(entry),
                              old_price=pending.get("old_price"),
                              new_price=pending.get("new_price")))
        return out

    def mark_missing(self, search_id: str, seen_ids: set[str],
                     *, grace_runs: int = 2, confirm=None) -> list[Change]:
        """Flag listings from ``search_id`` that stopped appearing.

        A car needs to be absent from several consecutive runs before it counts
        as gone, because a single page of results can drop a car for reasons
        that have nothing to do with it being sold.

        Absence is only evidence when we looked at the whole result set. On a
        search with more results than the bot reads - 186 cars against the 60
        it samples - the site rotates which listings surface, so cars come and
        go from the sample constantly. Left alone that produced fourteen
        "removed" alerts in one run for cars that were still on the front page.
        ``confirm`` is asked about each candidate in that situation and answers
        True (really gone), False (still listed) or None (could not tell, so
        ask again next run rather than guessing).
        """
        changes: list[Change] = []
        for lid, entry in self.listings.items():
            if entry.get("search_id") != search_id or entry.get("status") != "active":
                continue
            if lid in seen_ids:
                entry["misses"] = 0
                entry.pop("gone_evidence", None)
                entry.pop("gone_checks", None)
                continue
            misses = int(entry.get("misses", 0)) + 1
            entry["misses"] = misses
            if misses < grace_runs:
                continue

            verdict = confirm(entry) if confirm is not None else True
            if verdict is False:
                # It fell out of the sample, not off the market.
                entry["misses"] = 0
                entry["last_seen"] = utcnow()
                continue
            if verdict is None:
                # Hold at the threshold: the next run asks again instead of
                # announcing a sale we could not establish.
                entry["misses"] = grace_runs
                continue

            entry["status"] = "gone"
            entry["removed_at"] = utcnow()
            changes.append(Change(Change.REMOVED, Listing.from_dict(entry)))
        return changes

    # ---------------- run + search health ----------------

    def search_health(self, search_id: str) -> dict[str, Any]:
        return self.data["searches"].setdefault(
            search_id, {"consecutive_failures": 0, "last_ok": None,
                        "last_error": None, "last_count": 0, "last_strategy": None},
        )

    def record_search_ok(self, search_id: str, count: int, strategy: str) -> None:
        health = self.search_health(search_id)
        health.update({"consecutive_failures": 0, "last_ok": utcnow(),
                       "last_error": None, "last_count": count,
                       "last_strategy": strategy})

    def record_search_error(self, search_id: str, error: str) -> int:
        health = self.search_health(search_id)
        health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
        health["last_error"] = error[:500]
        health["last_error_at"] = utcnow()
        return health["consecutive_failures"]

    # ---------------- channel health ----------------

    def channel_health(self, name: str) -> dict[str, Any]:
        channels = self.data.setdefault("channels", {})
        return channels.setdefault(name, {"consecutive_failures": 0,
                                          "permanent_failures": 0,
                                          "last_error": None, "last_ok": None,
                                          "disabled_at": None})

    def record_channel(self, name: str, ok: bool, detail: str = "",
                       permanent: bool = False) -> int:
        """Track a channel's outcome. Returns consecutive permanent failures."""
        health = self.channel_health(name)
        if ok:
            health.update({"consecutive_failures": 0, "permanent_failures": 0,
                           "last_ok": utcnow(), "last_error": None})
            return 0
        health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
        health["last_error"] = (detail or "")[:300]
        health["last_error_at"] = utcnow()
        if permanent:
            health["permanent_failures"] = int(health.get("permanent_failures", 0)) + 1
        else:
            # A transient error does not count towards giving up on a channel.
            health["permanent_failures"] = 0
        return int(health["permanent_failures"])

    def mark_channel_disabled(self, name: str) -> None:
        health = self.channel_health(name)
        health["disabled_at"] = utcnow()
        health["permanent_failures"] = 0

    # ---------------- page shape ----------------

    def search_shape(self, search_id: str) -> dict[str, Any] | None:
        return (self.data.get("searches", {}).get(search_id) or {}).get("shape")

    def record_shape(self, search_id: str, shape: dict[str, Any]) -> None:
        self.search_health(search_id)["shape"] = shape

    def record_run(self, summary: dict[str, Any]) -> None:
        summary = {"at": utcnow(), **summary}
        runs = self.data.get("runs", [])
        # The run log is a rolling sixty. Anything that needs to know when the
        # watch *began* - and the market layer does, because it decides which
        # of its own figures are floors rather than measurements - cannot read
        # it off the end of a window that slides forward every half hour.
        # Written once and then left alone.
        if not self.data.get("watch_started"):
            oldest = min([r.get("at") for r in runs if r.get("at")]
                         + [summary["at"]])
            self.data["watch_started"] = oldest
        self.data["runs"] = ([summary] + runs)[:MAX_RUN_HISTORY]

    @property
    def runs(self) -> list[dict[str, Any]]:
        return self.data.get("runs") or []

    @property
    def watch_started(self) -> str | None:
        """When this bot first ran, as far as it can still tell."""
        if self.data.get("watch_started"):
            return self.data["watch_started"]
        ats = [r.get("at") for r in (self.data.get("runs") or []) if r.get("at")]
        return min(ats) if ats else None

    @property
    def last_run(self) -> dict[str, Any] | None:
        runs = self.data.get("runs") or []
        return runs[0] if runs else None

    # ---------------- housekeeping ----------------

    def forget_searches(self, keep_ids: set[str]) -> list[str]:
        """Drop health rows for searches that are no longer configured.

        Without this, a search you removed keeps its last failure forever and
        shows up on the dashboard as permanently broken.
        """
        searches = self.data.get("searches") or {}
        gone = [sid for sid in searches if sid not in keep_ids]
        for sid in gone:
            del searches[sid]

        # Its cars have to stop being live too. They are not sold - you simply
        # stopped watching - but leaving them active makes them cars owned by a
        # search that does not exist, which is either a dashboard full of
        # listings nothing is checking or, once the run started auditing
        # itself, a failed run every time.
        released = 0
        for entry in self.listings.values():
            if entry.get("search_id") not in keep_ids and entry.get("status") == "active":
                entry["status"] = "gone"
                entry["removed_at"] = utcnow()
                entry["quiet_reason"] = "the search that was watching this was removed"
                entry["notified"] = True
                released += 1
        if released:
            log.info("released %d listing(s) from removed searches", released)
        return gone

    def prune(self, *, keep_days: int = 730, keep_max: int = 5000) -> int:
        """Forget cars that went away a long time ago, so state stays small."""
        if keep_days <= 0 and keep_max <= 0:
            return 0
        removed = 0
        if keep_days > 0:
            # Zero means "no age limit", as it does for keep_max. It used to
            # mean a cutoff of right now, which quietly deleted every piece of
            # history the moment someone set it to zero to switch it off.
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=keep_days)).isoformat()
            for lid, entry in list(self.listings.items()):
                if entry.get("status") != "gone" or entry.get("pending"):
                    continue          # never forget a car still owed an alert
                if (entry.get("removed_at") or entry.get("last_seen") or "") < cutoff:
                    del self.listings[lid]
                    removed += 1
        if keep_max and len(self.listings) > keep_max:
            # Only history is disposable. Dropping a car that is still for sale
            # would make the next run rediscover and re-announce it, which is
            # the one thing state exists to prevent.
            ordered = sorted((kv for kv in self.listings.items()
                              if kv[1].get("status") == "gone"
                              and not kv[1].get("pending")),
                             key=lambda kv: kv[1].get("last_seen") or "")
            for lid, _ in ordered[: len(self.listings) - keep_max]:
                del self.listings[lid]
                removed += 1
        return removed

        # Thin old price points. Every observation is kept for a month, then
        # one a week is enough to draw the line - and the endpoints are always
        # kept, because losing one moves the very numbers a history exists to
        # give you.
        from . import insight
        for entry in self.listings.values():
            history = entry.get("price_history") or []
            if len(history) > 8:
                entry["price_history"] = insight.compact_history(history)

    def stats(self) -> dict[str, Any]:
        # A car hidden by the user's own filters is not something they are
        # watching, so it does not count towards what is live.
        active = [e for e in self.listings.values()
                  if e.get("status") == "active" and not e.get("filtered")]
        priced = [e["price"] for e in active if e.get("price")]
        return {
            "total": len(self.listings),
            "active": len(active),
            "gone": sum(1 for e in self.listings.values() if e.get("status") == "gone"),
            "with_price": len(priced),
            "min_price": min(priced) if priced else None,
            "max_price": max(priced) if priced else None,
            "median_price": sorted(priced)[len(priced) // 2] if priced else None,
        }
