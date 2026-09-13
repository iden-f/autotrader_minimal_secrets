"""What the numbers mean, worked out once and published.

The dashboard used to receive a list of cars and a health blob, which meant
every question worth asking - is this one cheap for its year? what actually
changed since I last looked? is the bot keeping up? - was either answered in
the browser from incomplete data or not answered at all.

None of this scrapes or writes anything. It reads stored state and derives,
so it can be run over a fixture and checked.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .events import delivery_state
from .listing import name_of

# A price comparison drawn from a handful of cars is a coincidence with a
# percentage sign on it. Below this many comparables the dashboard says so
# instead of scoring.
MIN_COMPARABLES = 6
# And this many before a PERCENTAGE, which is a claim about a market rather
# than about the sample in hand. Between the two, a car gets its rank.
MIN_FOR_A_PERCENTAGE = 12
# How long a car must have been watched before "it did not cut its price"
# means anything about the car rather than about the bot.
BACKTEST_MIN_DAYS = 1
# How far either side of a car's year its comparables may come from. Wider
# than this and a 2015 is being judged against a 2021.
YEAR_BAND = 1
# A car more than this far from the median either way is worth pointing at.
NOTABLE_PCT = 8.0

KINDS = ("new", "price_drop", "price_rise", "priced", "removed", "relisted")


def _dt(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


# ---------------------------------------------------------------- comparables

def _group_key(entry: dict[str, Any]) -> tuple[str, str] | None:
    make = str(entry.get("make") or "").strip().lower()
    model = str(entry.get("model") or "").strip().lower()
    if not make or not model:
        return None
    return (make, model)


def comparables(entries: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """For each priced car, how it sits against others of its kind.

    Three answers, and which one a car gets depends only on how many peers it
    has - never on how interesting the number would be:

      n >= 12   a percentage against the cohort median
      6..11     its RANK inside the cohort, and no percentage
      below 6   why not, naming the bot's limit rather than the market's

    The middle rule is the one that matters. A rank is a true statement about
    the sample in hand; a percentage is a claim about a market. On the eight
    peers this bot had when the rule was written, the inter-quartile range was
    $14,796 - 22.7% of the median - so the 8% threshold used to decide a car
    was worth pointing at fired inside a third of one IQR. Enumerating all 28
    six-car subsets of that cohort, the same car read anywhere from "2% under"
    to "13% under", and 18 of the 28 would have flagged it.

    One median per cohort, quoted identically on every car in it. Nine cars
    used to be given four different medians - $59,449, $61,749, $62,399,
    $65,149 - for what reads as one market, a 9.6% spread, wider than the
    threshold used to call a car cheap.
    """
    # Read once. This is an Iterable, and the second pass below - the one
    # that explains the cars the pool excluded - would see nothing at all if
    # a caller handed in a generator.
    entries = list(entries)
    # Cars your rules keep. A 2020 M4 judged against 2021 M4s - which the year
    # rule exists to exclude - is judged against a market you are not shopping
    # in, and reads as a bargain for being older.
    pool = [e for e in entries
            if e.get("price") and e.get("year") and _group_key(e)
            and e.get("status") == "active" and not e.get("filtered")]

    # One cohort per (make, model, year band), so every car in it is quoted
    # the same median.
    cohorts: dict[tuple, list[dict[str, Any]]] = {}
    for entry in pool:
        key = (_group_key(entry), int(entry["year"]) // (YEAR_BAND * 2 + 1))
        cohorts.setdefault(key, []).append(entry)

    out: dict[str, dict[str, Any]] = {}
    for entry in pool:
        key = (_group_key(entry), int(entry["year"]) // (YEAR_BAND * 2 + 1))
        cohort = cohorts[key]
        odo = entry.get("mileage_km")
        # Peers must match on wear as well as on name. A 137,241 km M4 was
        # being called "9% under the median" of eight cars whose median
        # odometer was 58,256 - it carries 2.36 times the kilometres, and the
        # $6,150 discount works out at 8 cents per extra kilometre.
        peers = [p for p in cohort if p["id"] != entry["id"]
                 and _similar_wear(odo, p.get("mileage_km"))]
        prices = sorted(int(p["price"]) for p in peers)
        row: dict[str, Any] = {"sample": len(prices), "band": YEAR_BAND,
                               "cohort": _cohort_name(entry)}

        if len(prices) >= MIN_FOR_A_PERCENTAGE:
            everyone = sorted([int(p["price"]) for p in peers] + [int(entry["price"])])
            median = statistics.median(everyone)
            row["median"] = int(median)
            row["delta"] = int(entry["price"]) - int(median)
            row["pct"] = round((entry["price"] - median) / median * 100.0, 1)
            row["notable"] = abs(row["pct"]) >= NOTABLE_PCT
            row["cheaper_than"] = sum(1 for p in prices if p > entry["price"])
            odos = [p.get("mileage_km") for p in peers if p.get("mileage_km")]
            row["peer_median_km"] = int(statistics.median(odos)) if odos else None
        elif len(prices) >= MIN_COMPARABLES:
            # A rank, which is true of this sample, instead of a percentage,
            # which would be a claim about a market this size cannot support.
            everyone = sorted([int(p["price"]) for p in peers] + [int(entry["price"])])
            row["rank"] = everyone.index(int(entry["price"])) + 1
            row["of"] = len(everyone)
            row["why_not"] = (
                f"{_ordinal(row['rank'])} cheapest of {row['of']} "
                f"{row['cohort']} here - too few to call it cheap or dear")
        else:
            row["why_not"] = _no_cohort(entry, len(prices), cohort)
        out[str(entry["id"])] = row

    # And a row for every car that never reached the pool, saying which of
    # the five preconditions it failed.
    #
    # Without this the page had one blank for five different situations: a
    # car with no asking price, a car that has left the market, a car a rule
    # hides, a car whose year did not parse, and a car with a cohort of two.
    # Only the last of those was ever explained, so silence meant "we looked
    # and there was nothing" on one card and "we never looked" on the next.
    seen = {str(e["id"]) for e in pool}
    for entry in entries:
        key = str(entry.get("id") or "")
        if not key or key in seen:
            continue
        out[key] = {"sample": 0, "why_not": _never_compared(entry)}
    return out


def _never_compared(entry: dict[str, Any]) -> str:
    """Why a car was not put up against any other."""
    if entry.get("status") != "active":
        return ("it has left the market - the last price the bot saw is the "
                "one recorded above, and it is not compared against cars "
                "still for sale")
    if entry.get("filtered"):
        return ("a rule of yours hides it, and the comparison is made only "
                "against the cars you are actually shopping")
    if not entry.get("price"):
        return "it has no asking price, so there is nothing to compare"
    if not entry.get("year"):
        return ("its model year did not parse, and the comparison is made "
                "within a year band")
    return ("its make and model did not parse, so it has no cohort on this "
            "dashboard")


def _similar_wear(mine: Any, theirs: Any, tolerance: float = 0.35) -> bool:
    """Close enough on the odometer to be the same kind of car.

    A car with no reading is never excluded - the table of what we know is
    the thing most likely to be incomplete, and dropping a peer for a missing
    field shrinks the sample that decides whether there is a sample at all.
    """
    try:
        mine, theirs = int(mine), int(theirs)
    except (TypeError, ValueError):
        return True
    if mine <= 0 or theirs <= 0:
        return True
    return abs(theirs - mine) <= max(mine, theirs) * tolerance


def _cohort_name(entry: dict[str, Any]) -> str:
    make = str(entry.get("make") or "").strip()
    model = str(entry.get("model") or "").strip()
    year = entry.get("year")
    span = (f"{int(year) - YEAR_BAND}-{int(year) + YEAR_BAND}" if year else "")
    return " ".join(p for p in (make, model, span) if p).strip() or "cars like it"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _no_cohort(entry: dict[str, Any], peers: int, cohort: list[dict[str, Any]]) -> str:
    """Why this car is not scored, naming the bot's limit rather than the
    market's.

    "only 0 other BMW X3 within a year of 2010 to compare against" reads as
    one short of a threshold. The truth was that the car has no market on this
    dashboard at all - it is a stray result from a search for a different car.
    """
    name = _cohort_name(entry)
    if not peers and len(cohort) <= 1:
        return (f"nothing else here is a {name} - this car has no market on "
                f"this dashboard to be judged against")
    if peers < len(cohort) - 1:
        held = len(cohort) - 1 - peers
        return (f"{_count(peers, 'comparable')} close enough on age and "
                f"mileage to judge it against. "
                + ("One more is listed with very different kilometres on it."
                   if held == 1 else
                   f"Another {held} are listed with very different kilometres "
                   f"on them.")
                + " Not scored.")
    return (f"{_count(peers, 'comparable')} to compare it against - fewer "
            f"than the {MIN_COMPARABLES} this needs. Not scored.")


# Where price per kilometre says anything at all.
#
# Below the floor the denominator is not wear, it is dealer stock: twelve live
# cars in this watch read exactly 90 km and one reads 18, which is a car being
# reversed off a transporter. The old guard was 5,000, and it was a cliff
# rather than a floor - a real 2025 M3 with exactly 5,000 km on it cleared it
# (5000 < 5000 is false) and printed "$23,380 /1000km", the largest number on
# the site, 2,900 times the smallest.
#
# Above the ceiling the asking price has stopped tracking kilometres and
# started tracking condition, and the ratio reads a quarter of a million
# kilometres of wear as a quarter of a million units of value received.
PER_KM_FLOOR = 20_000
PER_KM_CEILING = 200_000


def per_1000km(price: Any, km: Any) -> float | None:
    """Asking price per thousand kilometres, where that means something."""
    try:
        price = int(price)
        km = int(km)
    except (TypeError, ValueError):
        return None
    if price <= 0 or not (PER_KM_FLOOR <= km <= PER_KM_CEILING):
        return None
    return round(price / (km / 1000.0), 1)


def per_1000km_withheld(price: Any, km: Any) -> str | None:
    """Why this car has no price-per-kilometre, when it has none.

    Absence must never be the only signal: a blank reads identically to "this
    car has no odometer", and the two are different things.
    """
    if per_1000km(price, km) is not None:
        return None
    try:
        km = int(km)
    except (TypeError, ValueError):
        return "no odometer reading"
    try:
        if int(price) <= 0:
            return "no asking price"
    except (TypeError, ValueError):
        return "no asking price"
    if km < PER_KM_FLOOR:
        return (f"only {km:,} km on it - that is delivery mileage, not wear, "
                f"and the ratio would be meaningless")
    return (f"at {km:,} km the asking price tracks condition rather than "
            f"kilometres, so the ratio stops meaning anything")


# ---------------------------------------------------------------- the feed

def _delivery(entry: dict[str, Any]) -> dict[str, Any]:
    """Whether you heard about this, and if not, why not.

    The decision - which of the four states, and in which order they are
    asked - lives in events.delivery_state. Only the wording is here: this
    writes a sentence onto a card, and the ledger writes a line for a log.
    Both used to write the ordering down as well, and the bug the docstring
    over there describes was fixed in one copy first.
    """
    state = delivery_state(entry)
    if state == "queued":
        return {"state": state, "text": "queued for delivery, not sent yet"}
    if state == "quiet":
        text = str(entry["quiet_reason"])
        if entry.get("notified_at"):
            text += (f" (you were told about this car at "
                     f"{entry['notified_at']}, before that rule applied)")
        return {"state": state, "text": text}
    if state == "sent":
        return {"state": state, "at": entry["notified_at"],
                "text": f"sent {entry['notified_at']}"}
    return {"state": state,
            "text": "no record of telling you - which is itself a fault"}


def events(entries: Iterable[dict[str, Any]], limit: int = 400
           ) -> list[dict[str, Any]]:
    """Everything that happened to a car, newest first.

    Derived rather than stored: the run counters only ever counted changes on
    cars that passed the filters, so a price drop on a hidden car was real,
    recorded, and invisible in every number the bot printed.
    """
    out: list[dict[str, Any]] = []

    def add(kind: str, at: Any, entry: dict[str, Any], **extra: Any) -> None:
        when = _dt(at)
        if when is None:
            return
        out.append({
            "kind": kind,
            "at": when.isoformat(timespec="seconds"),
            "listing_id": str(entry.get("id")),
            # name_of, not entry["title"]: a hidden car is never enriched from
            # its own page, so its title can still be the parser's placeholder
            # long after make, model and year are known.
            "title": name_of(entry),
            "year": entry.get("year"),
            "make": entry.get("make"),
            "model": entry.get("model"),
            "price": entry.get("price"),
            "filtered": bool(entry.get("filtered")),
            "filter_reason": entry.get("filter_reason") or "",
            "delivery": _delivery(entry),
            **extra,
        })

    for entry in entries:
        if entry.get("imported_from") or entry.get("migrated_from"):
            continue
        add("new", entry.get("first_seen"), entry)

        history = entry.get("price_history") or []
        for before, after in zip(history, history[1:]):
            old, new = before.get("price"), after.get("price")
            if not old or not new or old == new:
                continue
            add("price_drop" if new < old else "price_rise", after.get("at"),
                entry, old_price=old, new_price=new, delta=new - old)

        if entry.get("priced_at"):
            add("priced", entry["priced_at"], entry,
                new_price=entry.get("price"))
        if entry.get("relisted_at"):
            add("relisted", entry["relisted_at"], entry)
        if entry.get("qualified_at"):
            add("qualified", entry["qualified_at"], entry,
                was_hidden_by=entry.get("qualified_from") or "")
        if entry.get("seller_changed_at"):
            add("seller", entry["seller_changed_at"], entry,
                was=entry.get("seller_was") or "",
                now=entry.get("seller_type") or "")
        if entry.get("photos_at"):
            add("photos", entry["photos_at"], entry,
                photos=len(entry.get("images") or []))
        if entry.get("removed_at") and entry.get("status") == "gone":
            add("removed", entry["removed_at"], entry)

    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- coverage

def _read_the_site(run: dict[str, Any]) -> bool:
    """Did this run actually look at the searches?

    That, not the exit code, is what decides whether a slot was covered. A
    run that was skipped (another one held the lock, or it was too soon) never
    looked; one whose searches all failed to load never looked either. Every
    other run did, whatever it thought of its own bookkeeping afterwards.
    """
    if run.get("skipped"):
        return False
    ran = int(run.get("searches_run") or 0)
    failed = int(run.get("searches_failed") or 0)
    if ran:
        return failed < ran
    # An older run record from before these counters existed. Fall back to the
    # exit code rather than crediting a slot nothing is known about.
    return bool(run.get("ok"))


def coverage(runs: list[dict[str, Any]], expected_minutes: int = 30,
             window_hours: int = 24, now: datetime | None = None,
             since_change: str | None = None) -> dict[str, Any]:
    """How much of the last day was actually watched.

    The honest measure of this bot is not whether the last run worked, it is
    what fraction of the checks it was supposed to make it actually made.
    GitHub serves roughly a tenth of the schedules asked of it here, so this
    number is the one that says whether the car you want could have come and
    gone between checks.
    """
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)

    # A coverage figure is a statement about a schedule, so it may only be
    # measured over a period when that schedule was the one running.
    #
    # This bot's interval changed from 30 minutes to 120 at 06:30 one morning.
    # For the 24 hours after that, the window still held 54 half-hourly runs,
    # and bucketing them into 12 two-hour slots filled every one: the Status
    # tab read "100% - 12 of 12" about a schedule that had produced two
    # checks. Every word of it was arithmetically true and the number was
    # evidence about the wrong thing.
    changed = _dt(since_change)
    partial = False
    if changed is not None and changed > start:
        start = changed
        partial = True
    measured_hours = max(0.0, _hours_between(now, start))
    # Complete slots only. The slot in progress has not finished failing yet,
    # and counting it either flatters the figure (if a check has landed) or
    # damns it (if one is still due).
    import math
    complete = int(math.floor(measured_hours * 60 / max(1, expected_minutes)))
    expected = max(1, complete)

    stamps = sorted(t for t in (_dt(r.get("at")) for r in runs)
                    if t is not None and t >= start)

    # Covered, not ok.
    #
    # This counted runs whose exit code was zero, and those are different
    # questions. A run that fetched both searches, read two hundred listings
    # and wrote them all down, then exited 1 because a bookkeeping invariant
    # tripped, covered its slot completely: no car came or went unseen. For a
    # day and a half one such invariant was failing on every run, and this
    # figure read 18.8% while the site was being read every time a check ran.
    # It said the market was unwatched. It meant the ledger was inconsistent.
    #
    # The question the number claims to answer is "could the car I want have
    # come and gone between checks", and only a search that failed to load
    # leaves that hole. A run that read the site and then complained about
    # itself is a separate fault, reported separately, and the Status view
    # shows both.
    read_stamps = sorted(
        t for t in (_dt(r.get("at")) for r in runs if _read_the_site(r))
        if t is not None and t >= start)
    ok_stamps = sorted(t for t in (_dt(r.get("at")) for r in runs
                                   if r.get("ok"))
                       if t is not None and t >= start)

    # Distinct slots, not checks. Two checks in the same half hour cover one
    # half hour; counting them as two lets a burst of manual runs report
    # coverage the schedule never delivered - which is exactly what a day of
    # working on this repository looks like from the inside.
    slot = max(1, expected_minutes) * 60
    covered = {int((t - start).total_seconds() // slot) for t in read_stamps}
    covered = {i for i in covered if 0 <= i < expected}

    gaps: list[float] = []
    edge = [start] + read_stamps + [now]
    for before, after in zip(edge, edge[1:]):
        gaps.append(_hours_between(after, before) * 60.0)

    # The run history is capped, so a window that reaches past the oldest run
    # kept would report a coverage of zero for hours nobody has a record of.
    # Measuring from the first run in the window keeps the figure about what
    # happened rather than about how much history is retained.
    truncated = bool(runs) and (_dt(runs[-1].get("at")) or start) > start

    return {
        "window_hours": round(measured_hours, 1),
        "asked_window_hours": window_hours,
        # True when the window was cut short because the schedule changed
        # inside it. The page must say so rather than presenting a partial
        # measurement as a day's worth.
        "partial": partial,
        # Three slots is the fewest that can distinguish a schedule from an
        # accident. Below that there is no percentage worth printing, and the
        # page says how long it has been measuring instead.
        "too_short": complete < 3,
        "since_change": changed.isoformat(timespec="seconds") if changed else None,
        "expected": expected,
        "checks": len(stamps),
        "successful": len(read_stamps),
        "slots_covered": len(covered),
        # Runs that read the site but reported a problem about themselves.
        # Shown next to the coverage figure rather than folded into it.
        "complained": len(read_stamps) - len(ok_stamps),
        "clean": len(ok_stamps),
        "pct": round(min(100.0, len(covered) / expected * 100.0), 1),
        "longest_gap_minutes": round(max(gaps), 1) if gaps else None,
        "expected_interval_minutes": expected_minutes,
        "truncated": truncated,
        "since": start.isoformat(timespec="seconds"),
        # One entry per expected slot, oldest first: 0 for a half hour with
        # no check, 1 for one, 2 for one that also complained about itself.
        # A percentage says how much; this says when, and the shape is what
        # tells you whether the schedule is thin or simply absent for hours.
        "slots": _slot_row(runs, start, slot, expected),
    }


def _slot_row(runs: list[dict[str, Any]], start: datetime, slot: int,
              expected: int) -> list[int]:
    row = [0] * expected
    for run in runs:
        when = _dt(run.get("at"))
        if when is None or when < start or not _read_the_site(run):
            continue
        index = int((when - start).total_seconds() // slot)
        if 0 <= index < expected:
            row[index] = max(row[index], 1 if run.get("ok") else 2)
    return row


def minutes_spent(runs: list[dict[str, Any]], window_hours: int = 24,
                  now: datetime | None = None) -> dict[str, Any]:
    """Runner time the checks themselves cost, per day.

    This is wall clock inside the run, which is not what GitHub charges:
    every JOB is rounded up to a whole minute, so twelve 35-second checks cost
    twelve minutes and not seven. ``billed_minutes`` is the charged figure and
    is the one to quote; ``minutes`` is kept because it is what shows whether
    the runs themselves are getting slower.
    """
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    durations = [float(r.get("duration_s") or 0) for r in runs
                 if (_dt(r.get("at")) or start) >= start]
    total = sum(durations) / 60.0
    import math
    overhead = 25.0
    billed = sum(max(1, math.ceil((d + overhead) / 60.0)) for d in durations)
    return {
        "checks": len(durations),
        "minutes": round(total, 1),
        "billed_minutes": billed,
        "mean_seconds": round(statistics.mean(durations), 1) if durations else None,
        "window_hours": window_hours,
    }


# ---------------------------------------------------------------- the week

def weekly(entries: Iterable[dict[str, Any]], runs: list[dict[str, Any]],
           days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    """What the market did this week, for a digest nobody has to decode.

    Deliberately about the market rather than about the bot: the per-run
    alerts already say what the bot did, and a weekly note that leads with
    "412 checks completed" is a note about the wrong thing.
    """
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    since = start.isoformat(timespec="seconds")
    entries = list(entries)

    window = [e for e in events(entries, limit=10_000) if e["at"] >= since]
    by_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
    for event in window:
        by_kind.setdefault(event["kind"], []).append(event)

    drops = sorted(by_kind.get("price_drop", []), key=lambda e: e.get("delta") or 0)
    live = [e for e in entries if e.get("status") == "active" and e.get("price")]
    prices = sorted(int(e["price"]) for e in live)

    # A median moves for two reasons - prices changed, or the mix of cars
    # changed - and over a week on a couple of hundred listings it is nearly
    # always the second. Said plainly rather than dressed up as a trend.
    older = [e for e in entries
             if e.get("price_history") and len(e["price_history"]) > 1]
    then: list[int] = []
    for entry in older:
        for point in entry["price_history"]:
            if str(point.get("at") or "") <= since and point.get("price"):
                then.append(int(point["price"]))
                break

    out: dict[str, Any] = {
        "since": since,
        "days": days,
        "new": len(by_kind.get("new", [])),
        "gone": len(by_kind.get("removed", [])),
        "back": len(by_kind.get("relisted", [])),
        "qualified": len(by_kind.get("qualified", [])),
        "priced": len(by_kind.get("priced", [])),
        "drops": len(drops),
        "rises": len(by_kind.get("price_rise", [])),
        "cut_total": sum(abs(e.get("delta") or 0) for e in drops),
        "biggest_drop": drops[0] if drops else None,
        "live": len(live),
        "median": int(statistics.median(prices)) if prices else None,
        "checks": sum(1 for r in runs if str(r.get("at") or "") >= since and r.get("ok")),
    }
    if then and prices:
        was = int(statistics.median(sorted(then)))
        out["median_then"] = was
        out["median_move"] = out["median"] - was
    return out


def weekly_text(summary: dict[str, Any]) -> str:
    """The week as something worth reading on a phone."""
    lines = [f"The last {summary['days']} days on your searches", ""]
    if summary["new"]:
        lines.append(f"{_count(summary['new'], 'new listing')} appeared.")
    if summary["drops"]:
        total = f"${summary['cut_total']:,}"
        lines.append(f"{_count(summary['drops'], 'price drop')}, "
                     f"{total} off in total.")
        best = summary.get("biggest_drop")
        if best:
            lines.append(f"  Biggest: {best.get('title') or 'a listing'} "
                         f"${abs(best.get('delta') or 0):,} off, now "
                         f"${(best.get('new_price') or 0):,}.")
    if summary["rises"]:
        lines.append(f"{_count(summary['rises'], 'price increase')}.")
    if summary["priced"]:
        lines.append(f"{_count(summary['priced'], 'call-for-price car')} "
                     f"named a figure.")
    if summary["gone"]:
        lines.append(f"{summary['gone']} left the market.")
    if summary["back"]:
        lines.append(f"{summary['back']} came back.")
    if summary.get("qualified"):
        lines.append(f"{summary['qualified']} came back inside your rules.")
    if not any(summary[k] for k in ("new", "drops", "rises", "priced", "gone",
                                    "back", "qualified")):
        lines.append("Nothing moved. Every car is where it was.")

    lines.append("")
    if summary.get("median") is not None:
        line = f"{summary['live']} cars live, median asking ${summary['median']:,}"
        move = summary.get("median_move")
        if move:
            way = "up" if move > 0 else "down"
            line += (f" - {way} ${abs(move):,} on a week ago, though a median moves "
                     f"as much on which cars are listed as on what they cost")
        lines.append(line + ".")
    lines.append(f"Built from {_count(summary['checks'], 'successful check')} "
                 f"this week.")
    return "\n".join(lines)


# ---------------------------------------------------------------- the market

# How long a price point has to survive before compaction stops thinning it.
KEEP_DAILY_DAYS = 30


def compact_history(history: list[dict[str, Any]], *,
                    keep_daily_days: int = KEEP_DAILY_DAYS,
                    now: datetime | None = None) -> list[dict[str, Any]]:
    """Thin a price history without losing its shape.

    Every observation is kept for the first month, because that is the window
    a person is actually looking at. Older than that, one point per week is
    enough to draw the trend, and the first and last points are always kept -
    losing an endpoint would move the very numbers the history exists for.
    """
    if len(history) <= 2:
        return list(history)
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=keep_daily_days)

    kept: list[dict[str, Any]] = [history[0]]
    last_week: str | None = None
    for point in history[1:-1]:
        when = _dt(point.get("at"))
        if when is None:
            continue
        if when >= cut:
            kept.append(point)
            continue
        week = f"{when.isocalendar().year}-{when.isocalendar().week}"
        if week != last_week:
            kept.append(point)
            last_week = week
    kept.append(history[-1])
    return kept


def _trim_of(entry: dict[str, Any]) -> str:
    """A trim name coarse enough to group on.

    Dealer titles carry a paragraph of options; the word that matters for
    price is Competition, Touring, CS and so on. Anything else is "base",
    which is honest about what is known rather than inventing a category.
    """
    import unicodedata
    raw = " ".join(str(entry.get(k) or "") for k in ("trim", "title"))
    # Fold the accents before matching. A real car in this watch is titled
    # "BMW M3 COMPETITION" with an acute E, and it was bucketed as "other"
    # alongside cars with no trim at all.
    text = "".join(c for c in unicodedata.normalize("NFKD", raw)
                   if not unicodedata.combining(c)).lower()
    for word in ("competition", "touring", "cs", "m carbon", "lci"):
        if word in text:
            return word.replace("m carbon", "carbon")
    return "base"


def _count(n: int, word: str, plural: str = "") -> str:
    """Three price drops, or one price drop. Not one price drop with an (s).

    The weekly digest is the one thing here a person reads end to end, and
    every count in it had the parenthesis.
    """
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _days(n: int) -> str:
    """Two days, or one day. Never one day with an (s) after it."""
    return "1 day" if n == 1 else f"{n} days"


def _span(hours: float) -> str:
    """How long something has been going on, in the unit that fits.

    A watch three hours old reported "over 0 days of watching", which reads as
    a rounding error rather than as the true and useful statement that the
    searches were read this morning.
    """
    hours = max(0.0, float(hours or 0))
    if hours < 1:
        minutes = int(round(hours * 60))
        return "under an hour" if minutes < 1 else (
            "1 minute" if minutes == 1 else f"{minutes} minutes")
    if hours < 48:
        whole = int(round(hours))
        return "1 hour" if whole == 1 else f"{whole} hours"
    return _days(int(hours // 24))


# A median from four cars is a coincidence with a dollar sign on it. Below
# this, the cars themselves are shown instead of a statistic drawn from them -
# which is both more honest and, at these sizes, more useful.
MIN_FOR_A_MEDIAN = 5


def _spread(prices: list[int]) -> dict[str, Any]:
    prices = sorted(prices)
    row: dict[str, Any] = {"n": len(prices)}
    if len(prices) < MIN_FOR_A_MEDIAN:
        # Everything, in order, and no summary of it.
        row["prices"] = prices
        row["thin"] = True
        return row
    row.update({
        "median": int(statistics.median(prices)),
        "low": prices[0], "high": prices[-1],
        "q1": int(statistics.quantiles(prices, n=4)[0]) if len(prices) >= 4 else None,
        "q3": int(statistics.quantiles(prices, n=4)[2]) if len(prices) >= 4 else None,
        "thin": False,
    })
    return row


def _model_label(entry: dict[str, Any]) -> str:
    make = str(entry.get("make") or "").strip()
    model = str(entry.get("model") or "").strip()
    return " ".join(p for p in (make, model) if p) or "unknown"


def _by_model(live: list[dict[str, Any]]) -> dict[str, Any]:
    """Every model on its own, with year and trim breakdowns inside it.

    Sorted by how many cars each has, so the page leads with the one there is
    something to say about.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in live:
        if entry.get("price"):
            groups.setdefault(_model_label(entry), []).append(entry)

    out: dict[str, Any] = {}
    for label, cars in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        years: dict[int, list[int]] = {}
        trims: dict[str, list[int]] = {}
        for car in cars:
            if car.get("year"):
                years.setdefault(int(car["year"]), []).append(int(car["price"]))
            trims.setdefault(_trim_of(car), []).append(int(car["price"]))
        row = _spread([int(c["price"]) for c in cars])
        row["label"] = label
        row["by_year"] = {str(y): _spread(p) for y, p in sorted(years.items())}
        row["by_trim"] = {t: _spread(p) for t, p in
                          sorted(trims.items(), key=lambda kv: -len(kv[1]))}
        out[label] = row
    return out


def market(entries: Iterable[dict[str, Any]], *, now: datetime | None = None,
           runs: Iterable[dict[str, Any]] | None = None,
           since: str | None = None) -> dict[str, Any]:
    """What the whole dataset says, rather than what one car says.

    Two hundred listings and a few months of history is not a research
    dataset. Everything below carries its sample size, and anything drawn
    from fewer than a handful of cars is left out rather than rounded into a
    number that looks authoritative.
    """
    now = now or datetime.now(timezone.utc)
    entries = [e for e in entries
               if not e.get("imported_from") and not e.get("migrated_from")]
    live = [e for e in entries if e.get("status") == "active"]
    gone = [e for e in entries if e.get("status") == "gone" and e.get("removed_at")]

    # ---- price by model, then by year and trim within it ------------------
    #
    # By year alone was wrong, and stayed wrong until the watched cars changed
    # from one model to three. "2017: median $51,972, from $15,980 to $62,999"
    # was an ordinary X3 and an M3 averaged together, and the trim table put
    # "competition" at n=23 by mixing M4 Competitions with X3 M Competitions.
    # A median across two different cars is not a number about either of them.
    # Cars the rules keep, not every car the search returned. A watch for an
    # M3 up to 2020 that reports "BMW M3, median $111,947" is quoting a market
    # of 2025s the rules exist to exclude - true about the search results, and
    # useless to the person reading it. The hidden ones are counted beside
    # each model rather than folded into its median.
    yours = [e for e in live if not e.get("filtered")]
    by_model = _by_model(yours)
    hidden_by_model: dict[str, int] = {}
    for entry in live:
        if entry.get("filtered"):
            label = _model_label(entry)
            hidden_by_model[label] = hidden_by_model.get(label, 0) + 1
    for label, row in by_model.items():
        row["hidden"] = hidden_by_model.pop(label, 0)
    # A model that exists only as hidden cars still deserves a line, or the
    # page silently omits a whole car you are searching for.
    for label, count in hidden_by_model.items():
        by_model[label] = {"label": label, "n": 0, "hidden": count,
                           "thin": True, "prices": [],
                           "by_year": {}, "by_trim": {}}
    # Kept flat as well, because the page and the weekly digest both read it,
    # but now scoped to the model with the most cars rather than to whatever
    # happened to share a year.
    leader = max(by_model.values(), key=lambda m: m["n"], default=None)
    if leader is not None and not leader["n"]:
        leader = None
    by_year = leader["by_year"] if leader else {}
    by_trim = leader["by_trim"] if leader else {}

    # ---- how long things last --------------------------------------------
    # "Sold" is not knowable from a listing disappearing, and saying so would
    # be inventing data. What is knowable is how long a car was listed before
    # it stopped being listed.
    lifespans: list[int] = []
    for entry in gone:
        first, last = _dt(entry.get("first_seen")), _dt(entry.get("removed_at"))
        if first and last and last > first:
            lifespans.append(max(0, (last - first).days))
    lifespans.sort()

    standing: list[int] = []
    for entry in live:
        first = _dt(entry.get("first_seen"))
        if first:
            standing.append(max(0, (now - first).days))
    standing.sort()

    # ---- how fast the market moves ---------------------------------------
    week = now - timedelta(days=7)
    arrivals = sum(1 for e in entries
                   if (_dt(e.get("first_seen")) or now) >= week)
    departures = sum(1 for e in gone if (_dt(e.get("removed_at")) or now) >= week)

    cut_total = 0
    cut_cars = 0
    for entry in entries:
        history = entry.get("price_history") or []
        drops = sum(max(0, a["price"] - b["price"])
                    for a, b in zip(history, history[1:])
                    if a.get("price") and b.get("price") and b["price"] < a["price"])
        if drops:
            cut_total += drops
            cut_cars += 1

    # How much of this is actually observation. The id record here goes back
    # fifteen months because v1's listing ids were imported, but a price can
    # only have moved on a car this bot has seen twice - and most of these it
    # has seen for three days. Saying "median asking is up" off that would be
    # a sentence about the sample, not about the market.
    seen = sorted(t for t in (_dt(e.get("first_seen")) for e in entries) if t)
    tracked = [e for e in entries if len(e.get("price_history") or []) > 1]
    span_days = (now - seen[0]).days if seen else 0
    watched = sorted(t for t in (_dt(e.get("first_seen")) for e in live) if t)

    # When the watch itself began, which is not the same as when the oldest
    # car was first seen - every car alive on the first run was "first seen"
    # that day whether it went up that morning or two years ago. Without this
    # the censoring test below compares a number against itself and passes
    # vacuously, which is exactly how it was written the first time.
    started = sorted(t for t in (_dt(r.get("at")) for r in (runs or [])) if t)
    # ``since`` is the state's own written-once record. The run log is a
    # rolling window, so falling back to it alone makes a bot that has watched
    # for a year report that it has watched for three days, forever.
    watch_began = (_dt(since) or (started[0] if started else None)
                   or (watched[0] if watched else now))
    watch_days = max(0, (now - watch_began).days)
    watch_hours = max(0.0, _hours_between(now, watch_began))

    return {
        "at": now.isoformat(timespec="seconds"),
        "window": {
            "ids_span_days": span_days,
            "watching_days": watch_days,
            "cars_with_two_prices": len(tracked),
            "thin": len(tracked) < 20 or watch_days < 14,
            "note": (f"{len(tracked)} of {len(entries)} cars have been priced "
                     f"more than once, over {_span(watch_hours)} of watching. "
                     f"Anything below described as a trend is really a "
                     f"snapshot until that number grows."),
        },
        "live": len(live),
        "gone": len(gone),
        "by_year": by_year,
        "by_trim": by_trim,
        "by_model": by_model,
        "leader": leader["label"] if leader else None,
        "listed_days": {
            "n": len(lifespans),
            "median": lifespans[len(lifespans) // 2] if lifespans else None,
            "p10": lifespans[len(lifespans) // 10] if len(lifespans) >= 10 else None,
            "p90": lifespans[len(lifespans) * 9 // 10] if len(lifespans) >= 10 else None,
            # Deliberately not called "days to sell". A listing coming down
            # means the seller stopped advertising it, which is not the same
            # thing, and the difference matters to anyone reading this.
            "note": "how long a car was listed before it came down - not how "
                    "long it took to sell, which a listing cannot tell you",
            # Only a listing that both arrived and left inside the watch can
            # be measured, so a short watch can only ever see short lives.
            # The number is real and the sample is biased, which is worse
            # than either alone if nobody says it.
            "biased_short": watch_days < 30,
            "watching_days": watch_days,
        },
        "still_listed_days": {
            "n": len(standing),
            "median": standing[len(standing) // 2] if standing else None,
            "longest": standing[-1] if standing else None,
            # Right-censored: a car first seen on the day the watch started
            # has been listed for *at least* that long, and nothing here can
            # say how much longer. Reporting the floor as the figure is how a
            # two-day-old bot claims the market turns over every two days.
            # A car first seen on the first run has been listed for *at
            # least* this long; how much longer is not knowable from here.
            #
            # Against when THESE searches began, which is not when the bot was
            # installed. The two dates diverge the moment the watch list
            # changes: this bot had been running three days when its searches
            # were swapped for different cars, so every car was three days
            # younger than "the watch", the guard compared against the wrong
            # date and did not fire - and the Market tab reported "still
            # listed, median 0 days, longest 0 days" about a market it had
            # been watching for a hundred minutes.
            #
            # Not `min(watched)`, which is the same number on the other side
            # of the comparison and therefore true of every dataset ever
            # collected. That was the original bug here and it was briefly
            # reintroduced fixing this one.
            "censored": bool(standing and watched
                             and watched[0] <= watch_began + timedelta(hours=6)),
            "watching_days": watch_days,
        },
        "velocity": {"arrived_7d": arrivals, "left_7d": departures,
                     # A bot that started watching on Tuesday reports that
                     # every car on the market "arrived this week". True, and
                     # useless. The reader needs to know the window is the
                     # watch, not the week.
                     "window_is_the_watch": watch_days < 7},
        "discounting": {"cars": cut_cars, "total": cut_total,
                        "mean": int(cut_total / cut_cars) if cut_cars else None},
    }


def backtest(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Did the deal score say anything useful, in hindsight?

    The score claims a car is under or over the median for its kind. If that
    means anything, cars it called dear should have cut their prices more
    often than cars it called cheap. This checks, over this dataset, and says
    so either way - a score nobody has tested is decoration.
    """
    entries = list(entries)
    scores = comparables(entries)
    # Cars are re-scored against today's market, so a car that has since been
    # discounted is compared at its current price. Using its first price is
    # the honest test of "was it dear when it appeared".
    cheap_cut = cheap_n = dear_cut = dear_n = 0
    for entry in entries:
        row = scores.get(str(entry.get("id")))
        if not row or row.get("pct") is None:
            continue
        history = [p for p in (entry.get("price_history") or []) if p.get("price")]
        if len(history) < 1:
            continue
        # Watched long enough that a cut would have been seen. A car first
        # read this morning has not "failed to cut its price"; it has not had
        # the chance, and counting it as one fills the denominator with cases
        # that could never have gone the other way. With 83 cars all first
        # seen within five minutes of each other, that was the whole
        # denominator.
        first_seen, last_seen = _dt(entry.get("first_seen")), _dt(entry.get("last_seen"))
        if first_seen and last_seen and (last_seen - first_seen) < timedelta(
                days=BACKTEST_MIN_DAYS):
            continue
        first = history[0]["price"]
        last = history[-1]["price"]
        cut = last < first
        if row["pct"] <= -NOTABLE_PCT:
            cheap_n += 1
            cheap_cut += 1 if cut else 0
        elif row["pct"] >= NOTABLE_PCT:
            dear_n += 1
            dear_cut += 1 if cut else 0

    out: dict[str, Any] = {
        "called_cheap": cheap_n, "cheap_that_cut": cheap_cut,
        "called_dear": dear_n, "dear_that_cut": dear_cut,
    }
    if cheap_n >= 5 and dear_n >= 5:
        cheap_rate = cheap_cut / cheap_n
        dear_rate = dear_cut / dear_n
        out["cheap_rate"] = round(cheap_rate * 100, 1)
        out["dear_rate"] = round(dear_rate * 100, 1)
        out["verdict"] = (
            "cars it called dear cut their prices more often than cars it "
            "called cheap, which is what the score claims"
            if dear_rate > cheap_rate else
            "cars it called dear did not cut more often than cars it called "
            "cheap - on this data the score is not predicting anything")
    else:
        out["verdict"] = (
            f"not enough scored cars to test it: {cheap_n} called cheap and "
            f"{dear_n} called dear, and five of each is the minimum worth "
            f"drawing a conclusion from")
    return out
