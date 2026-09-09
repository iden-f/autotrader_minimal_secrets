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
    REMOVED = "removed"

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
        if self.kind == Change.REMOVED:
            return "Listing removed"
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
                    return cls(raw, path)
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

    def record(self, listing: Listing, *, seen_at: str | None = None) -> Change | None:
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
                "notified": False,
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
        entry["notified"] = True if was_imported else existing.get("notified", False)
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
            if old_price is not None and not was_imported:
                kind = Change.PRICE_DROP if listing.price < old_price else Change.PRICE_RISE
                change = Change(kind, merged, old_price=old_price, new_price=listing.price)
        else:
            entry.pop("price_disputed", None)
            if not history and listing.price is not None:
                history.append({"at": now, "price": listing.price})

        entry["price_history"] = history[-MAX_PRICE_POINTS:]
        if was_imported:
            entry.pop("imported_from", None)
        self.listings[listing.id] = entry
        return change

    def mark_notified(self, listing_ids: Iterable[str]) -> None:
        for lid in listing_ids:
            if lid in self.listings:
                self.listings[lid]["notified"] = True
                self.listings[lid].pop("pending", None)

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
                     *, grace_runs: int = 2) -> list[Change]:
        """Flag listings from ``search_id`` that stopped appearing.

        A car needs to be absent from several consecutive runs before it counts
        as gone, because a single page of results can drop a car for reasons
        that have nothing to do with it being sold.
        """
        changes: list[Change] = []
        for lid, entry in self.listings.items():
            if entry.get("search_id") != search_id or entry.get("status") != "active":
                continue
            if lid in seen_ids:
                entry["misses"] = 0
                continue
            misses = int(entry.get("misses", 0)) + 1
            entry["misses"] = misses
            if misses >= grace_runs:
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

    def record_run(self, summary: dict[str, Any]) -> None:
        summary = {"at": utcnow(), **summary}
        self.data["runs"] = ([summary] + self.data.get("runs", []))[:MAX_RUN_HISTORY]

    @property
    def last_run(self) -> dict[str, Any] | None:
        runs = self.data.get("runs") or []
        return runs[0] if runs else None

    # ---------------- housekeeping ----------------

    def prune(self, *, keep_days: int = 730, keep_max: int = 5000) -> int:
        """Forget cars that went away a long time ago, so state stays small."""
        if keep_days <= 0 and keep_max <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        removed = 0
        for lid, entry in list(self.listings.items()):
            if entry.get("status") != "gone":
                continue
            if (entry.get("removed_at") or entry.get("last_seen") or "") < cutoff:
                del self.listings[lid]
                removed += 1
        if keep_max and len(self.listings) > keep_max:
            ordered = sorted(self.listings.items(),
                             key=lambda kv: kv[1].get("last_seen") or "")
            for lid, _ in ordered[: len(self.listings) - keep_max]:
                del self.listings[lid]
                removed += 1
        return removed

    def stats(self) -> dict[str, Any]:
        active = [e for e in self.listings.values() if e.get("status") == "active"]
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
