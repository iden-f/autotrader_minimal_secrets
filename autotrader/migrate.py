"""Upgrading a v1 repository in place.

v1 stored two things: a flat list of ids in ``seen_listings.json`` and, per
car, an ``archives/<id>/`` folder holding the raw listing page.  Both are
worth keeping - the archived pages carry schema.org data that is far richer
than anything v1 ever recorded.

This turns them into v2 state, so the dashboard opens with real history rather
than an empty page, and the first run after upgrading stays quiet instead of
announcing fifty cars from last year as brand new.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .archive import ARCHIVE_DIR
from .config import Config
from .enrich import detail_from_html
from .listing import Listing
from .state import LEGACY_SEEN_PATH, State
from .urls import canonical_listing_url

log = logging.getLogger(__name__)


def _iso(stamp: str | None) -> str | None:
    """v1 wrote 'YYYY-MM-DD HH:MM:SS' with no timezone; assume UTC."""
    if not stamp:
        return None
    text = str(stamp).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


def read_archive(folder: Path) -> tuple[Listing | None, str | None]:
    """Rebuild a Listing from a v1 archive folder."""
    meta: dict[str, Any] = {}
    meta_path = folder / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}

    listing: Listing | None = None
    page = folder / "page.html"
    if page.exists():
        try:
            listing = detail_from_html(
                page.read_text(encoding="utf-8", errors="replace"),
                folder.name, meta.get("url", ""))
        except Exception as exc:  # noqa: BLE001 - a bad archive is not fatal
            log.debug("could not read %s: %s", page, exc)

    if listing is None and meta.get("url"):
        if "archived_at" in meta or "price" in meta or "year" in meta:
            # A v2 metadata file: the facts were read out of the page before
            # the page was thrown away, so rebuild from them rather than
            # falling back to the id and a title.
            try:
                listing = Listing.from_dict(meta)
            except (TypeError, ValueError) as exc:
                log.debug("could not rebuild %s from metadata: %s", folder, exc)
        if listing is None:
            # v1 metadata: no usable HTML, but it still names the car.
            listing = Listing(id=folder.name, url=meta["url"],
                              title=str(meta.get("title", ""))[:120])

    if listing is not None:
        listing.id = folder.name
        if meta.get("url"):
            listing.url = canonical_listing_url(meta["url"])
        if not listing.title and meta.get("title"):
            listing.title = str(meta["title"])[:120]

    return listing, _iso(meta.get("saved") or meta.get("archived_at"))


def migrate(config_path: Path = Path("config.json"),
            state_path: Path = Path("state.json"),
            archive_dir: Path = ARCHIVE_DIR,
            legacy_seen: Path = LEGACY_SEEN_PATH,
            *, dry_run: bool = False) -> int:
    """Import v1 data into v2 state.  Returns a process exit code."""
    cfg = Config.load(config_path)
    state = State.load(state_path)

    # State.load() already adopts the legacy file when there is no state yet,
    # so count what is actually present rather than what this call added.
    state.import_legacy(legacy_seen)
    legacy_total = 0
    if legacy_seen.exists():
        try:
            legacy_total = len(json.loads(legacy_seen.read_text(encoding="utf-8")) or [])
        except (json.JSONDecodeError, OSError):
            legacy_total = 0

    enriched = 0
    plain = 0
    failed: list[str] = []

    if archive_dir.exists():
        for folder in sorted(p for p in archive_dir.iterdir() if p.is_dir()):
            listing, saved_at = read_archive(folder)
            if listing is None:
                failed.append(folder.name)
                continue
            entry = listing.to_dict()
            existing = state.listings.get(listing.id, {})
            entry["first_seen"] = saved_at or existing.get("first_seen")
            entry["last_seen"] = saved_at or existing.get("last_seen")
            # These are historical: they were listed months ago and are almost
            # certainly sold. Marking them "gone" keeps them visible as history
            # without pretending they are live inventory.
            entry["status"] = "gone"
            entry["removed_at"] = saved_at
            entry["notified"] = True          # never re-announce an old car
            entry["migrated_from"] = "v1-archive"
            entry["price_history"] = ([{"at": saved_at, "price": listing.price}]
                                      if listing.price and saved_at else [])
            entry.pop("imported_from", None)
            state.listings[listing.id] = entry
            if listing.enriched and listing.price:
                enriched += 1
            else:
                plain += 1

    total = len(state.listings)
    # Anything still flagged as imported had no archive to rebuild it from.
    bare = sum(1 for e in state.listings.values()
               if e.get("imported_from") == "seen_listings.json")
    print(f"ids known from {legacy_seen.name}:      {legacy_total}"
          + (f" ({bare} with no archive to rebuild from)" if bare else ""))
    print(f"archives rebuilt with full detail:  {enriched}")
    print(f"archives rebuilt partially:         {plain}")
    if failed:
        print(f"archives that could not be read:    {len(failed)} ({', '.join(failed[:5])}...)")
    print(f"listings now tracked:               {total}")

    if dry_run:
        print("\ndry run - nothing written")
        return 0

    state.save(state_path)
    print(f"\nwrote {state_path}")

    from . import dashboard
    written = dashboard.write(cfg, state)
    if written:
        print(f"wrote {written}")
    return 0
