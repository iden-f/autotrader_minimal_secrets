"""Client-side filters applied on top of whatever the search URL already does.

The pasted search link does most of the work.  These filters exist for the
things AutoTrader's own form cannot express well - "never show me anything
from a dealer whose name contains X", "must mention manual", and so on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .listing import Listing


@dataclass
class Verdict:
    keep: bool
    reason: str = ""


def _haystack(listing: Listing) -> str:
    return " ".join(str(v) for v in (
        listing.title, listing.display_title, listing.trim, listing.color,
        listing.seller, listing.location, listing.body, listing.transmission,
        listing.drivetrain, listing.fuel, listing.engine,
    )).lower()


def check(listing: Listing, filters: dict[str, Any] | None) -> Verdict:
    """Decide whether a listing survives the user's filters."""
    f = filters or {}

    min_price = f.get("min_price")
    max_price = f.get("max_price")
    if listing.price is None:
        if f.get("require_price"):
            return Verdict(False, "no price listed")
    else:
        if min_price and listing.price < int(min_price):
            return Verdict(False, f"price ${listing.price:,} below minimum ${int(min_price):,}")
        if max_price and listing.price > int(max_price):
            return Verdict(False, f"price ${listing.price:,} above maximum ${int(max_price):,}")

    if listing.year is not None:
        if f.get("min_year") and listing.year < int(f["min_year"]):
            return Verdict(False, f"year {listing.year} below minimum {f['min_year']}")
        if f.get("max_year") and listing.year > int(f["max_year"]):
            return Verdict(False, f"year {listing.year} above maximum {f['max_year']}")

    max_km = f.get("max_mileage_km")
    if max_km and listing.mileage_km is not None and listing.mileage_km > int(max_km):
        return Verdict(False, f"{listing.mileage_km:,} km above maximum {int(max_km):,} km")

    text = _haystack(listing)

    include = [k.strip().lower() for k in (f.get("include_keywords") or []) if str(k).strip()]
    if include and not any(k in text for k in include):
        return Verdict(False, f"none of the required keywords matched: {', '.join(include)}")

    for word in (f.get("exclude_keywords") or []):
        word = str(word).strip().lower()
        if word and word in text:
            return Verdict(False, f"excluded keyword matched: {word}")

    for seller in (f.get("exclude_sellers") or []):
        seller = str(seller).strip().lower()
        if seller and listing.seller and seller in listing.seller.lower():
            return Verdict(False, f"excluded seller: {listing.seller}")

    return Verdict(True)


def apply(listings: list[Listing], filters: dict[str, Any] | None
          ) -> tuple[list[Listing], list[tuple[Listing, str]]]:
    """Split listings into kept and rejected (with the reason for each)."""
    kept: list[Listing] = []
    dropped: list[tuple[Listing, str]] = []
    for listing in listings:
        verdict = check(listing, filters)
        (kept if verdict.keep else dropped).append(
            listing if verdict.keep else (listing, verdict.reason))
    return kept, dropped


def is_significant_drop(old_price: int, new_price: int,
                        min_pct: float, min_abs: int) -> bool:
    """Is this price change worth a notification, or just a rounding tweak?"""
    if old_price <= 0 or new_price >= old_price:
        return False
    delta = old_price - new_price
    pct = delta / old_price * 100.0
    # Both thresholds must be cleared, so a $50 nudge on a cheap car and a
    # 0.2% nudge on an expensive one are both ignored.
    return delta >= max(0, int(min_abs)) and pct >= max(0.0, float(min_pct))
