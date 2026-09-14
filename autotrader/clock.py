"""One clock, so the whole bot can be asked what time it thinks it is.

Forty-four call sites asked `datetime.now(timezone.utc)` directly, and one
asked `datetime.now()` with no timezone at all - which is the local clock of
whatever machine happened to run it, silently correct on a UTC runner and
silently wrong anywhere else.

Two things follow from having a seam here:

  * the test suite can be run AS IF it were another date. A test that quietly
    depends on today - and there were several, including one that seeded
    "195 minutes times the day of the month" and crossed a ceiling the morning
    the date rolled from the 13th to the 14th - fails in the gate rather than
    on a random Tuesday six months from now.

  * every timestamp the bot writes comes from the same place, so a run cannot
    disagree with itself about when it happened.

AUTOTRADER_NOW overrides it, for that gate and for nothing else. It is read
once per call rather than cached, so a test can move time forward inside a
single process.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

#: Set by tests that need to hold time still or move it. Takes precedence over
#: the environment so a fixture can nest inside the date gate.
_FROZEN: datetime | None = None

ENV_VAR = "AUTOTRADER_NOW"


def _from_env() -> datetime | None:
    raw = (os.environ.get(ENV_VAR) or "").strip()
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # A malformed override must not silently become "actually, now" - that
        # would make the gate pass while testing nothing.
        raise ValueError(
            f"{ENV_VAR}={raw!r} is not an ISO timestamp. Use e.g. "
            f"2026-12-31T23:59:00Z") from None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def now() -> datetime:
    """The current moment, always timezone-aware and always UTC."""
    if _FROZEN is not None:
        return _FROZEN
    return _from_env() or datetime.now(timezone.utc)


def stamp(when: datetime | None = None) -> str:
    """The ISO-8601 string this bot writes into state, to the second.

    Seconds, not microseconds: these are compared, sorted and shown to people,
    and six decimal places of precision on a number that moves every two hours
    is noise in a file a person may have to read.
    """
    return (when or now()).isoformat(timespec="seconds")


def freeze(when: datetime | str | None) -> None:
    """Hold time still. Pass None to let it run again."""
    global _FROZEN
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    _FROZEN = when


def advance(delta: timedelta) -> datetime:
    """Move frozen time forward. Only meaningful while frozen."""
    global _FROZEN
    if _FROZEN is None:
        raise RuntimeError("advance() needs a frozen clock; call freeze() first")
    _FROZEN = _FROZEN + delta
    return _FROZEN


# ------------------------------------------------- how long ago was that

def parse(value: object) -> datetime | None:
    """A stored stamp as an aware UTC datetime, or None if it is not one.

    Always aware, never naive. Three modules had their own version of this
    line and two of them returned whatever the string carried, so a stamp
    that had lost its offset - a hand-edited state file, a record written by
    the bot before it kept them consistently - raised TypeError the moment it
    met an aware `now`, inside a comparison nobody expected to be able to
    fail. Everything this bot writes is UTC, so a stamp without an offset is
    read as UTC rather than as the machine's local time.
    """
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def minutes_since(value: object, at: datetime | None = None) -> float | None:
    """Minutes between a stored stamp and now, or None if it is not a stamp.

    Never negative. A runner with a skewed clock can write a timestamp in the
    future, and an age that goes negative turns "checked 20 minutes ago" into
    "checked in -40 minutes" on the page and makes every window comparison
    read the wrong way round. Clamped at zero, a future stamp reads as "just
    now", which is wrong by minutes rather than wrong by direction.
    """
    when = parse(value)
    if when is None:
        return None
    return max(0.0, ((at or now()) - when).total_seconds() / 60.0)


def hours_since(value: object, at: datetime | None = None) -> float | None:
    """Hours between a stored stamp and now, or None if it is not a stamp."""
    mins = minutes_since(value, at)
    return None if mins is None else mins / 60.0
