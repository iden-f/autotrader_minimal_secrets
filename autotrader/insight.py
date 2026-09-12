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

# A price comparison drawn from a handful of cars is a coincidence with a
# percentage sign on it. Below this many comparables the dashboard says so
# instead of scoring.
MIN_COMPARABLES = 6
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

    "Its kind" is the same make and model within a year either side. The
    sample size travels with the answer everywhere it is shown, because the
    honest reading of "14% under median" depends entirely on whether that is
    fourteen cars or three.
    """
    pool = [e for e in entries
            if e.get("price") and e.get("year") and _group_key(e)
            and e.get("status") == "active"]

    out: dict[str, dict[str, Any]] = {}
    for entry in pool:
        key = _group_key(entry)
        year = int(entry["year"])
        peers = [p for p in pool
                 if p["id"] != entry["id"] and _group_key(p) == key
                 and abs(int(p["year"]) - year) <= YEAR_BAND]
        prices = sorted(int(p["price"]) for p in peers)
        row: dict[str, Any] = {"sample": len(prices), "band": YEAR_BAND}
        if len(prices) >= MIN_COMPARABLES:
            median = statistics.median(prices)
            row["median"] = int(median)
            row["delta"] = int(entry["price"]) - int(median)
            row["pct"] = round((entry["price"] - median) / median * 100.0, 1)
            row["notable"] = abs(row["pct"]) >= NOTABLE_PCT
            row["cheaper_than"] = sum(1 for p in prices if p > entry["price"])
        else:
            row["why_not"] = (
                f"only {len(prices)} other {entry.get('make','')} "
                f"{entry.get('model','')} within a year of {year} to compare "
                f"against - not enough to call it cheap or dear")
        out[str(entry["id"])] = row
    return out


def per_1000km(price: Any, km: Any) -> float | None:
    """Asking price per thousand kilometres. Useful, and easy to mislead with.

    Meaningless under a few thousand km - a 900km car would read as an
    enormous number that says nothing about value - so it is not computed
    there rather than being computed and disclaimed.
    """
    try:
        price = int(price)
        km = int(km)
    except (TypeError, ValueError):
        return None
    if price <= 0 or km < 5000:
        return None
    return round(price / (km / 1000.0), 1)


# ---------------------------------------------------------------- the feed

def _delivery(entry: dict[str, Any]) -> dict[str, Any]:
    """Whether you heard about this, and if not, why not.

    The quiet reason is asked first, and that ordering is the whole point. A
    car can carry both: delivered once when it arrived and visible, then
    hidden by a rule added afterwards. Reading notified_at first made the
    ledger report a suppressed relisting as "delivered at 02:15" - a real
    timestamp, attached to a different event, describing a message that was
    never sent about this one.
    """
    if entry.get("pending"):
        return {"state": "queued",
                "text": "queued for delivery, not sent yet"}
    if entry.get("quiet_reason"):
        text = str(entry["quiet_reason"])
        if entry.get("notified_at"):
            text += (f" (you were told about this car at "
                     f"{entry['notified_at']}, before that rule applied)")
        return {"state": "quiet", "text": text}
    if entry.get("notified_at"):
        return {"state": "sent", "at": entry["notified_at"],
                "text": f"sent {entry['notified_at']}"}
    return {"state": "none",
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
            "title": entry.get("title") or "",
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
        if entry.get("removed_at") and entry.get("status") == "gone":
            add("removed", entry["removed_at"], entry)

    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- coverage

def coverage(runs: list[dict[str, Any]], expected_minutes: int = 30,
             window_hours: int = 24, now: datetime | None = None
             ) -> dict[str, Any]:
    """How much of the last day was actually watched.

    The honest measure of this bot is not whether the last run worked, it is
    what fraction of the checks it was supposed to make it actually made.
    GitHub serves roughly a tenth of the schedules asked of it here, so this
    number is the one that says whether the car you want could have come and
    gone between checks.
    """
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    expected = max(1, int(window_hours * 60 / max(1, expected_minutes)))

    stamps = sorted(t for t in (_dt(r.get("at")) for r in runs)
                    if t is not None and t >= start)
    ok_stamps = sorted(t for t in (_dt(r.get("at")) for r in runs
                                   if r.get("ok"))
                       if t is not None and t >= start)

    gaps: list[float] = []
    edge = [start] + ok_stamps + [now]
    for before, after in zip(edge, edge[1:]):
        gaps.append(_hours_between(after, before) * 60.0)

    # The run history is capped, so a window that reaches past the oldest run
    # kept would report a coverage of zero for hours nobody has a record of.
    # Measuring from the first run in the window keeps the figure about what
    # happened rather than about how much history is retained.
    truncated = bool(runs) and (_dt(runs[-1].get("at")) or start) > start

    return {
        "window_hours": window_hours,
        "expected": expected,
        "checks": len(stamps),
        "successful": len(ok_stamps),
        "pct": round(min(100.0, len(ok_stamps) / expected * 100.0), 1),
        "longest_gap_minutes": round(max(gaps), 1) if gaps else None,
        "expected_interval_minutes": expected_minutes,
        "truncated": truncated,
        "since": start.isoformat(timespec="seconds"),
    }


def minutes_spent(runs: list[dict[str, Any]], window_hours: int = 24,
                  now: datetime | None = None) -> dict[str, Any]:
    """Runner time the checks themselves cost, per day.

    Not the whole Actions bill - the pacemaker's held runner dwarfs it - but
    the part that scales with how often the bot checks, which is the part a
    decision about frequency actually turns on.
    """
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    durations = [float(r.get("duration_s") or 0) for r in runs
                 if (_dt(r.get("at")) or start) >= start]
    total = sum(durations) / 60.0
    return {
        "checks": len(durations),
        "minutes": round(total, 1),
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
        lines.append(f"{summary['new']} new listing(s) appeared.")
    if summary["drops"]:
        total = f"${summary['cut_total']:,}"
        lines.append(f"{summary['drops']} price drop(s), {total} off in total.")
        best = summary.get("biggest_drop")
        if best:
            lines.append(f"  Biggest: {best.get('title') or 'a listing'} "
                         f"${abs(best.get('delta') or 0):,} off, now "
                         f"${(best.get('new_price') or 0):,}.")
    if summary["rises"]:
        lines.append(f"{summary['rises']} price increase(s).")
    if summary["priced"]:
        lines.append(f"{summary['priced']} call-for-price car(s) named a figure.")
    if summary["gone"]:
        lines.append(f"{summary['gone']} left the market.")
    if summary["back"]:
        lines.append(f"{summary['back']} came back.")
    if not any(summary[k] for k in ("new", "drops", "rises", "priced", "gone", "back")):
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
    lines.append(f"Built from {summary['checks']} successful check(s) this week.")
    return "\n".join(lines)
