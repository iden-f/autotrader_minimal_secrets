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
        "window_hours": window_hours,
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
    lines.append(f"Built from {summary['checks']} successful check(s) this week.")
    return "\n".join(lines)


# ---------------------------------------------------------------- the market

# Below this, a "median for the year" is one or two cars wearing a statistic.
MIN_PER_BUCKET = 4
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
    text = " ".join(str(entry.get(k) or "") for k in ("trim", "title")).lower()
    for word in ("competition", "touring", "cs", "m carbon", "lci"):
        if word in text:
            return word.replace("m carbon", "carbon")
    return "base"


def _days(n: int) -> str:
    """"2 day(s)" is a programmer talking to themselves in public."""
    return "1 day" if n == 1 else f"{n} days"


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

    # ---- price by year, and by year and trim ------------------------------
    by_year: dict[str, Any] = {}
    buckets: dict[int, list[int]] = {}
    for entry in live:
        if entry.get("price") and entry.get("year"):
            buckets.setdefault(int(entry["year"]), []).append(int(entry["price"]))
    for year, prices in sorted(buckets.items()):
        if len(prices) < MIN_PER_BUCKET:
            continue
        prices.sort()
        by_year[str(year)] = {
            "n": len(prices),
            "median": int(statistics.median(prices)),
            "low": prices[0], "high": prices[-1],
            "q1": int(statistics.quantiles(prices, n=4)[0]) if len(prices) >= 4 else None,
            "q3": int(statistics.quantiles(prices, n=4)[2]) if len(prices) >= 4 else None,
        }

    by_trim: dict[str, Any] = {}
    trims: dict[str, list[int]] = {}
    for entry in live:
        if entry.get("price"):
            trims.setdefault(_trim_of(entry), []).append(int(entry["price"]))
    for trim, prices in trims.items():
        if len(prices) < MIN_PER_BUCKET:
            continue
        by_trim[trim] = {"n": len(prices),
                         "median": int(statistics.median(sorted(prices)))}

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

    return {
        "at": now.isoformat(timespec="seconds"),
        "window": {
            "ids_span_days": span_days,
            "watching_days": watch_days,
            "cars_with_two_prices": len(tracked),
            "thin": len(tracked) < 20 or watch_days < 14,
            "note": (f"{len(tracked)} of {len(entries)} cars have been priced "
                     f"more than once, over {_days(watch_days)} of watching. "
                     f"Anything below described as a trend is really a "
                     f"snapshot until that number grows."),
        },
        "live": len(live),
        "gone": len(gone),
        "by_year": by_year,
        "by_trim": by_trim,
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
