"""Client-side filters applied on top of whatever the search URL already does.

The pasted search link does most of the work.  These filters exist for the
things AutoTrader's own form cannot express well - "never show me anything
from a dealer whose name contains X", "must mention manual", and so on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import geo
from .listing import Listing

log = logging.getLogger(__name__)


@dataclass
class Verdict:
    keep: bool
    reason: str = ""
    # A car with no published figure is not the same as a car that failed a
    # filter. "Call for price" is how dealers advertise the ones they expect
    # to negotiate on, and hiding them - which is what require_price used to
    # do - hides exactly the listings someone hunting a bargain wants to see.
    unpriced: bool = False


def _number(value: Any) -> int | None:
    """A filter value as a number, or None if it is not one.

    config.json is a file people edit by hand and the settings UI writes text
    boxes into. A max price of "" or "100,000" or "not a number" must mean
    "no ceiling", not an exception that takes the whole run down before a
    single car has been recorded.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(str(value).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        log.warning("ignoring unusable filter value %r", value)
        return None


def _haystack(listing: Listing) -> str:
    return " ".join(str(v) for v in (
        listing.title, listing.display_title, listing.trim, listing.color,
        listing.seller, listing.location, listing.body, listing.transmission,
        listing.drivetrain, listing.fuel, listing.engine,
    )).lower()


def _as_list(value: Any) -> list[Any]:
    """A keyword setting as a list. A bare string is one keyword, not N letters."""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def check(listing: Listing, filters: dict[str, Any] | None) -> Verdict:
    """Decide whether a listing survives the user's filters."""
    f = filters or {}

    min_price = _number(f.get("min_price"))
    max_price = _number(f.get("max_price"))
    if listing.price is not None:
        if min_price and listing.price < min_price:
            return Verdict(False, f"price ${listing.price:,} below minimum ${min_price:,}")
        if max_price and listing.price > max_price:
            return Verdict(False, f"price ${listing.price:,} above maximum ${max_price:,}")

    min_year, max_year = _number(f.get("min_year")), _number(f.get("max_year"))
    if listing.year is not None:
        if min_year and listing.year < min_year:
            return Verdict(False, f"year {listing.year} below minimum {min_year}")
        if max_year and listing.year > max_year:
            return Verdict(False, f"year {listing.year} above maximum {max_year}")

    max_km = _number(f.get("max_mileage_km"))
    if max_km and listing.mileage_km is not None and listing.mileage_km > max_km:
        return Verdict(False, f"{listing.mileage_km:,} km above maximum {max_km:,} km")

    # Where the car is. The pasted link's own "near this postal code" is
    # ignored by the current platform, so if you want it honoured the bot has
    # to be the one honouring it.
    near = str(f.get("near") or "").strip()
    radius = _number(f.get("max_distance_km"))
    if near and radius:
        reference = geo.locate_reference(near)
        if reference is None:
            log.warning("cannot place %r, so distance is not being enforced", near)
        else:
            far, away = geo.too_far(listing.location, listing.province,
                                    reference, radius)
            if far:
                where = ", ".join(p for p in (listing.location, listing.province) if p)
                return Verdict(False, (
                    f"{where or 'that location'} is {away:,} km from {near}, "
                    f"beyond the {radius:,} km you asked for" if away else
                    f"{where or 'that location'} is beyond the {radius:,} km "
                    f"you asked for around {near}"))

    provinces = [str(p).strip().upper() for p in _as_list(f.get("provinces"))
                 if str(p).strip()]
    if provinces and listing.province and listing.province.upper() not in provinces:
        return Verdict(False, f"{listing.province} is not one of "
                              f"{', '.join(provinces)}")

    text = _haystack(listing)

    include = [str(k).strip().lower() for k in _as_list(f.get("include_keywords"))
               if str(k).strip()]
    if include and not any(k in text for k in include):
        return Verdict(False, f"none of the required keywords matched: {', '.join(include)}")

    for word in _as_list(f.get("exclude_keywords")):
        word = str(word).strip().lower()
        if word and word in text:
            return Verdict(False, f"excluded keyword matched: {word}")

    for seller in _as_list(f.get("exclude_sellers")):
        seller = str(seller).strip().lower()
        if seller and listing.seller and seller in listing.seller.lower():
            return Verdict(False, f"excluded seller: {listing.seller}")

    # Last, so a car excluded for some other reason is reported for that
    # reason rather than being filed under "call for price".
    if listing.price is None and f.get("require_price"):
        return Verdict(False, "call for price - no figure published", unpriced=True)

    return Verdict(True)


def apply(listings: list[Listing], filters: dict[str, Any] | None
          ) -> tuple[list[Listing], list[Listing], list[tuple[Listing, str]]]:
    """Split listings three ways: kept, call-for-price, and rejected.

    The middle bucket is the point. A car that passes every filter you can
    check and simply has no figure on it is not a rejection - it is a car you
    cannot judge yet, and it stays tracked and visible instead of vanishing.
    """
    kept: list[Listing] = []
    unpriced: list[Listing] = []
    dropped: list[tuple[Listing, str]] = []
    for listing in listings:
        verdict = check(listing, filters)
        if verdict.keep:
            kept.append(listing)
        elif verdict.unpriced:
            unpriced.append(listing)
        else:
            dropped.append((listing, verdict.reason))
    return kept, unpriced, dropped


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
