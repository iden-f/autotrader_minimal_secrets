"""Noticing that the site has changed shape, before it goes dark.

autotrader.ca moved onto the AutoScout24 platform once already and every
parser strategy silently returned zero. The bot only found out because a run
happened to be watched. This records what each search's page looked like -
which strategy won, how many each found, which markers were present - and
compares the next run against it.

The point is early warning: losing three of four strategies still produces
listings, but it means the next change will take the bot out entirely.
"""

from __future__ import annotations

import re
from typing import Any

# Structural markers, chosen because they say something about how the page is
# built rather than what is on it. Counts are bucketed, not exact: a results
# page legitimately has a different number of cards every day.
MARKERS = {
    "json_ld": r"application/ld\+json",
    "next_data": r"__NEXT_DATA__",
    "initial_state": r"__INITIAL_STATE__",
    "offer_links": r"/offers?/[^\"'\s]*[0-9a-f]{8}-[0-9a-f]{4}",
    "legacy_links": r"/a/[^\"'\s]*\d+_\d{5,}_",
    "data_listing_id": r"data-listing-id",
    "vehicle_cdn": r"vehicleimages|autoscout24\.net",
}

# Below this the difference is noise; above it something structural moved.
COUNT_DRIFT_RATIO = 0.5


def _bucket(count: int) -> str:
    """Coarse presence, so ordinary day-to-day variation is not drift."""
    if count == 0:
        return "none"
    if count < 10:
        return "few"
    if count < 100:
        return "many"
    return "lots"


def fingerprint(html: str, strategy: str, candidates: dict[str, int],
                listing_count: int) -> dict[str, Any]:
    """Describe the shape of a page in a way two runs can be compared by."""
    text = html or ""
    return {
        "strategy": strategy,
        "working": sorted(name for name, count in (candidates or {}).items() if count),
        "scores": dict(candidates or {}),
        "listing_count": listing_count,
        "markers": {name: _bucket(len(re.findall(pattern, text)))
                    for name, pattern in MARKERS.items()},
        "bytes": _bucket(len(text) // 1000),
    }


def compare(previous: dict[str, Any] | None,
            current: dict[str, Any]) -> tuple[list[str], bool]:
    """Return (reasons the shape drifted, whether it is worth alerting).

    A first sighting is not drift, but it is still worth capturing: a baseline
    is only useful if we know what it looked like.
    """
    if not previous:
        return ["first time this search has been fingerprinted"], False

    reasons: list[str] = []
    serious = False

    old_strategy = previous.get("strategy")
    new_strategy = current.get("strategy")
    if old_strategy != new_strategy:
        reasons.append(
            f"the page is now being read by '{new_strategy}' instead of "
            f"'{old_strategy}'")
        serious = True

    old_working = set(previous.get("working") or [])
    new_working = set(current.get("working") or [])
    lost = old_working - new_working
    if lost:
        reasons.append(
            f"{', '.join(sorted(lost))} stopped working "
            f"({len(new_working)} of 4 strategies left)")
        # Losing the last of the fallbacks is the moment to say something.
        serious = serious or len(new_working) <= 1

    gained = new_working - old_working
    if gained:
        reasons.append(f"{', '.join(sorted(gained))} started working again")

    old_markers = previous.get("markers") or {}
    for name, bucket in (current.get("markers") or {}).items():
        was = old_markers.get(name)
        if was is not None and was != bucket:
            reasons.append(f"marker '{name}' went from {was} to {bucket}")
            # A structural marker vanishing entirely usually precedes a break.
            serious = serious or bucket == "none"

    old_count = int(previous.get("listing_count") or 0)
    new_count = int(current.get("listing_count") or 0)
    if old_count >= 5 and new_count < old_count * COUNT_DRIFT_RATIO:
        reasons.append(
            f"listings found fell from {old_count} to {new_count}")
        serious = True

    return reasons, serious


def describe(search_name: str, reasons: list[str], current: dict[str, Any]) -> str:
    """A short explanation for a notification."""
    lines = [f"The shape of the page for '{search_name}' has changed.", ""]
    for reason in reasons:
        lines.append(f"- {reason}")
    lines.append("")
    lines.append(f"Currently reading it with '{current.get('strategy')}' "
                 f"({current.get('listing_count')} listings); "
                 f"working strategies: {', '.join(current.get('working') or []) or 'none'}.")
    lines.append("")
    lines.append("Nothing is broken yet. A capture of the page has been committed "
                 "under diagnostics/ so the parser can be updated before it is.")
    return "\n".join(lines)
