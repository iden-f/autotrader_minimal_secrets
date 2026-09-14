"""The words and numbers this bot says, defined once.

Terminology drifts when every module spells it out. This project has had the
same sentence in three places and fixed it in one: a removal was "gone" on
Telegram, "REMOVED" in email and "Removed" in the digest; the plural helper
existed twice with two different docstrings; "3 failed runs in a row" outlived
the change that made it a count of checks. None of those were noticed by a
test, because each copy was self-consistent.

So the vocabulary lives here and the modules import it. If a word is wrong it
is wrong in one place, and changing it changes it everywhere.
"""

from __future__ import annotations


def many(count, one: str, more: str = "") -> str:
    """Three requests, or one request. Never one request with an (s) after it.

    This line is the first thing anyone reads in an Actions log, it is quoted
    verbatim into the watchdog's alert, and it is most of the terminal output.
    It is copy, not formatting.
    """
    return f"{count} {one if count == 1 else (more or one + 's')}"


def span(hours: float | None) -> str:
    """How long something has been going on, in the unit that fits.

    A watch three hours old reported "over 0 days of watching", which reads as
    a rounding error rather than as the true and useful statement that the
    searches were read this morning.
    """
    hours = max(0.0, float(hours or 0))
    if hours < 1:
        minutes = int(round(hours * 60))
        return "under an hour" if minutes < 1 else many(minutes, "minute")
    if hours < 48:
        return many(int(round(hours)), "hour")
    return days(int(hours // 24))


def days(count: int) -> str:
    """Two days, or one day. Never one day with an (s) after it."""
    return many(int(count), "day")


def money(amount) -> str:
    """Canadian dollars, no cents. $72,000 - never $72000 and never $72,000.00.

    Every price this bot shows is a whole-dollar asking price scraped from a
    listing; cents on one would be false precision, and three of the channels
    had their own f-string for it.
    """
    try:
        return f"${int(round(float(amount))):,}"
    except (TypeError, ValueError):
        return ""
