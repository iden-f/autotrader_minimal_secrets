"""A standing record of the first time each kind of market event really happens.

Price drops, removals, relistings and a call-for-price car finally naming a
figure are all proven against replayed payloads. Proving them against the
market means waiting for the market, and the market does not perform on
request: six hours of live watching produced none of them.

So this waits instead. It reads what the watcher has already written - it
never scrapes, never notifies, never touches the bot's state - and the first
time each kind actually occurs it writes down what happened: the car, the
before and after, and what was delivered about it. Being outside the run path
is the point; nothing here can hold up or wedge a check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_PATH = Path("EVENTS.md")
DATA_PATH = Path("docs/events.json")

KINDS = {
    "price_drop": "a price came down",
    "price_rise": "a price went up",
    "removed": "a car left the market",
    "relisted": "a car came back",
    "qualified": "a car came back inside your rules",
    "priced": "a call-for-price car named a figure",
}


@dataclass
class Event:
    kind: str
    at: str
    listing_id: str
    title: str = ""
    url: str = ""
    before: Any = None
    after: Any = None
    delivered: str = ""
    detail: str = ""
    run: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "at": self.at, "listing_id": self.listing_id,
                "title": self.title, "url": self.url, "before": self.before,
                "after": self.after, "delivered": self.delivered,
                "detail": self.detail, "run": self.run}


# The four things that can have happened about a car, and the order they are
# asked in. THE ORDER IS THE RULE, and it was written down twice - here and
# in insight.py - each with its own wording. A car can carry both a delivery
# time and a quiet reason: delivered once when it arrived and visible, then
# hidden by a rule added later. Reading the delivery time first reported a
# relisting that was correctly suppressed as "delivered at 02:15" - a real
# timestamp, belonging to a different event, describing a message never sent
# about this one. That was fixed in one copy first.
#
# The wording differs by design: this file writes a ledger line and the
# dashboard writes a sentence on a card. The decision does not.
DELIVERY_STATES = ("queued", "quiet", "sent", "none")


def delivery_state(entry: dict[str, Any]) -> str:
    """Which of the four happened, asked in the only order that is correct."""
    if entry.get("pending"):
        return "queued"
    if entry.get("quiet_reason"):
        return "quiet"
    if entry.get("notified_at"):
        return "sent"
    return "none"


def _delivery(entry: dict[str, Any]) -> str:
    """What the bot did about this car, in the words it recorded at the time."""
    state = delivery_state(entry)
    if state == "queued":
        return "queued, not yet delivered"
    if state == "quiet":
        text = f"deliberately quiet: {entry['quiet_reason']}"
        if entry.get("notified_at"):
            text += (f" (this car was announced at {entry['notified_at']}, "
                     f"before that rule applied)")
        return text
    if state == "sent":
        stamp = entry["notified_at"]
        if entry.get("notified_at_backfilled"):
            return f"delivered (time reconstructed, {stamp})"
        return f"delivered at {stamp}"
    return "no record - which is itself a fault the run should have caught"


def _run_at(runs: list[dict[str, Any]], when: str) -> dict[str, Any]:
    """The run that was happening when this was recorded."""
    best: dict[str, Any] = {}
    for run in runs:
        if str(run.get("at") or "") <= str(when or ""):
            if not best or str(run.get("at")) > str(best.get("at")):
                best = run
    keep = ("at", "ok", "listings_seen", "new", "price_drops", "price_rises",
            "removed", "relisted", "qualified", "priced", "requests_made",
            "notified")
    return {k: best[k] for k in keep if k in best}


def scan(state) -> dict[str, Event]:
    """The earliest genuine instance of each kind, from what is already stored."""
    found: dict[str, Event] = {}
    runs = list(state.data.get("runs") or [])

    def offer(event: Event) -> None:
        current = found.get(event.kind)
        if current is None or event.at < current.at:
            found[event.kind] = event

    for lid, entry in state.listings.items():
        # History from v1 is a snapshot, not something this bot watched happen.
        if entry.get("imported_from") or entry.get("migrated_from"):
            continue
        title = entry.get("title") or entry.get("display_title") or lid
        url = entry.get("url") or ""

        history = [p for p in (entry.get("price_history") or [])
                   if p.get("price") and p.get("at")]
        for older, newer in zip(history, history[1:]):
            kind = "price_drop" if newer["price"] < older["price"] else "price_rise"
            if newer["price"] == older["price"]:
                continue
            delta = newer["price"] - older["price"]
            offer(Event(
                kind=kind, at=newer["at"], listing_id=lid, title=title, url=url,
                before=older["price"], after=newer["price"],
                detail=f"${abs(delta):,} {'off' if delta < 0 else 'more'} "
                       f"(${older['price']:,} to ${newer['price']:,})",
                delivered=_delivery(entry), run=_run_at(runs, newer["at"])))
            break

        if entry.get("status") == "gone" and entry.get("removed_at"):
            offer(Event(
                kind="removed", at=entry["removed_at"], listing_id=lid,
                title=title, url=url, before="active", after="gone",
                detail=f"last seen {entry.get('last_seen') or 'unknown'}",
                delivered=_delivery(entry), run=_run_at(runs, entry["removed_at"])))

        if entry.get("relisted_at"):
            offer(Event(
                kind="relisted", at=entry["relisted_at"], listing_id=lid,
                title=title, url=url, before="gone", after="active",
                detail=f"written off, then listed again",
                delivered=_delivery(entry), run=_run_at(runs, entry["relisted_at"])))

        # A call-for-price car naming a figure. The run stamps the moment it
        # happens; the timestamp comparison is the fallback for cars recorded
        # before that stamp existed - their price history simply starts later
        # than the car did, because there was nothing to record until then.
        priced_at = entry.get("priced_at") or (
            history[0]["at"] if history and entry.get("first_seen")
            and history[0]["at"] > entry["first_seen"] else "")
        if priced_at and history and not entry.get("unpriced"):
            offer(Event(
                kind="priced", at=priced_at, listing_id=lid, title=title,
                url=url, before=None, after=entry.get("price") or history[-1]["price"],
                detail=f"listed with no price on {str(entry.get('first_seen'))[:10]}, "
                       f"then asked ${entry.get('price') or history[-1]['price']:,}",
                delivered=_delivery(entry), run=_run_at(runs, priced_at)))

    return found


def merge(found: dict[str, Event], previous: dict[str, Any]) -> dict[str, Any]:
    """Keep the first sighting of each kind, once recorded, forever."""
    out = dict(previous.get("first", {}) or {})
    for kind, event in found.items():
        fresh = event.to_dict()
        stored = out.get(kind)
        if stored is None or fresh["at"] < stored.get("at", "9999"):
            out[kind] = fresh
        elif (stored.get("listing_id") == fresh["listing_id"]
                and stored.get("at") == fresh["at"]):
            # The same event, read again. When it happened is settled; what
            # the bot did about it is not. A queued alert gets delivered, and
            # a delivery whose time had to be reconstructed gets recorded
            # properly the next time the car is announced. Freezing the first
            # answer means the ledger goes on describing a real alert as a
            # guess, or a sent one as still waiting.
            stored["delivered"] = fresh["delivered"]
    return {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "first": out,
            "waiting": [k for k in KINDS if k not in out],
            # Which silence has already been reported, so a bot that stays
            # quiet is announced once rather than once an hour.
            "silence_reported": previous.get("silence_reported", ""),
            "coverage_reported": previous.get("coverage_reported", "")}


def render(record: dict[str, Any]) -> str:
    """The ledger, as something worth reading rather than a JSON dump."""
    first = record.get("first", {}) or {}
    lines = [
        "# Real market events",
        "",
        "The first time each of these actually happened on autotrader.ca, as",
        "opposed to in a replayed payload. Written by a job that only reads what",
        "the watcher has already stored - it never scrapes and never notifies,",
        "so nothing here can hold up a check.",
        "",
        f"_Last looked: {record.get('updated_at', 'never')}_",
        "",
        "| Event | First seen | Car | Before | After | What was delivered |",
        "|---|---|---|---|---|---|",
    ]
    for kind, label in KINDS.items():
        event = first.get(kind)
        if not event:
            lines.append(f"| {label} | _still waiting_ | | | | |")
            continue
        car = event.get("title") or event.get("listing_id")
        if event.get("url"):
            car = f"[{car}]({event['url']})"
        before, after = event.get("before"), event.get("after")
        fmt = lambda v: (f"${v:,}" if isinstance(v, int) else (v or "—"))
        lines.append(
            f"| {label} | {str(event.get('at'))[:19]} | {car} | "
            f"{fmt(before)} | {fmt(after)} | {event.get('delivered', '')} |")

    for kind, label in KINDS.items():
        event = first.get(kind)
        if not event:
            continue
        lines += ["", f"## {label.capitalize()}", "",
                  f"- **When:** {event.get('at')}",
                  f"- **Car:** {event.get('title')} (`{event.get('listing_id')}`)",
                  f"- **What changed:** {event.get('detail')}",
                  f"- **Delivered:** {event.get('delivered')}"]
        run = event.get("run") or {}
        if run:
            lines.append(
                f"- **The run that saw it:** {run.get('at')} — "
                + ", ".join(f"{k} {v}" for k, v in run.items()
                            if k not in ("at", "notified") and v)
                + (f"; sent via {', '.join(run.get('notified') or []) or 'nothing'}"
                   if "notified" in run else ""))
    return "\n".join(lines) + "\n"


def silence(cfg, state, record: dict[str, Any],
            now: datetime | None = None) -> dict[str, Any] | None:
    """Has the bot stopped checking?  Returns what to say, or None.

    A watcher cannot report its own absence: the run that would tell you is
    the run that is not happening. This is asked by a separate job, on its own
    schedule, from the state file rather than from anything the watcher does -
    so a watcher that has silently stopped is still noticed.
    """
    hours = float((cfg.get("health", {}) or {}).get("silent_after_hours", 3) or 0)
    if hours <= 0:
        return None
    now = now or datetime.now(timezone.utc)

    runs = state.data.get("runs") or []
    last_ok = ""
    last_any = ""
    last_error = ""
    for run in runs:
        at = str(run.get("at") or "")
        if run.get("ok") and at > last_ok:
            last_ok = at
        if at > last_any:
            last_any = at
            errors = run.get("errors") or []
            last_error = str(errors[0]) if errors else ""
    if not last_ok:
        return None            # it has never worked; that is a different alarm

    try:
        when = datetime.fromisoformat(last_ok.replace("Z", "+00:00"))
    except ValueError:
        return None
    quiet_for = (now - when).total_seconds() / 3600.0
    if quiet_for < hours:
        return None

    # Say it once per silence, not once an hour for the length of it.
    if str(record.get("silence_reported") or "") == last_ok:
        return None
    every = (cfg.get("health", {}) or {}).get("expected_interval_minutes", 30)

    # "Gone quiet" and "running and failing every time" need different things
    # done about them, and telling them apart is just a matter of asking when
    # a run last happened at all. Said the wrong one for thirty-two hours: the
    # watcher was being started every couple of hours and failing a
    # bookkeeping check, and the alert reported it as silence.
    running = bool(last_any) and last_any > last_ok
    if running:
        detail = f"\n\nThe most recent one said: {last_error}" if last_error else ""
        return {
            "since": last_ok,
            "hours": round(quiet_for, 1),
            "failing": True,
            "subject": "AutoTrader watcher is running and failing",
            "body": (f"No check has succeeded for {quiet_for:.1f} hours "
                     f"(since {last_ok}), but the bot is still being started - "
                     f"the most recent attempt was at {last_any}.\n\nSo this is "
                     f"not a schedule problem. Something is wrong with the run "
                     f"itself, and the Actions log for the last one will say "
                     f"what.{detail}"),
        }

    return {
        "since": last_ok,
        "hours": round(quiet_for, 1),
        "failing": False,
        "subject": "AutoTrader watcher has gone quiet",
        "body": (f"The last successful check was {quiet_for:.1f} hours ago "
                 f"({last_ok}), and it is supposed to run every "
                 f"{every} minutes. Nothing has been started since, either.\n\n"
                 f"Nothing is being watched while this is true. Check the "
                 f"Actions tab: GitHub disables scheduled workflows on "
                 f"repositories with 60 days of no activity, and drops "
                 f"scheduled runs under load."),
    }


def save(record: dict[str, Any], data_path: Path = DATA_PATH,
         ledger_path: Path = LEDGER_PATH) -> None:
    """Write the ledger back, after something was added to it."""
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(record, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    ledger_path.write_text(render(record), encoding="utf-8")


def update(state, ledger_path: Path = LEDGER_PATH,
           data_path: Path = DATA_PATH) -> dict[str, Any]:
    """Look once, and write down anything seen for the first time."""
    previous: dict[str, Any] = {}
    if data_path.exists():
        try:
            previous = json.loads(data_path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            previous = {}
    record = merge(scan(state), previous)
    # Written into the file, not just returned: the job that runs this reads
    # the file back to decide whether anything happened for the first time,
    # and a field that only exists in memory is a field it never sees.
    record["new_kinds"] = [k for k in record["first"]
                           if k not in (previous.get("first") or {})]
    save(record, data_path, ledger_path)
    return record


def _slot_word(minutes: int) -> str:
    """"half-hours" was written into five strings while the schedule happened
    to be half-hourly, and stayed there when it stopped being."""
    if minutes == 30:
        return "half-hours"
    if minutes == 60:
        return "hours"
    if minutes % 60 == 0:
        return f"{minutes // 60}-hour slots"
    return f"{minutes}-minute slots"


# Below this share of the expected checks, the bot is not really watching -
# a car can be listed and sold inside a gap this size. Said once per day at
# most, because it is a condition rather than an event.
COVERAGE_FLOOR_PCT = 50.0


def thin_coverage(cfg, state, record: dict[str, Any],
                  now: datetime | None = None) -> dict[str, Any] | None:
    """Is the schedule delivering enough checks to be worth trusting?

    Separate from the silence alarm, and it has to be: a watcher that runs
    reliably every four hours is never silent and is still missing most of
    what happens. "The last check worked" has never been the question.
    """
    from . import insight

    health = cfg.get("health", {}) or {}
    floor = float(health.get("coverage_floor_pct", COVERAGE_FLOOR_PCT) or 0)
    if floor <= 0:
        return None
    expected = int(health.get("expected_interval_minutes", 30) or 30)
    now = now or datetime.now(timezone.utc)
    cover = insight.coverage(state.data.get("runs") or [], expected, now=now,
                             since_change=state.schedule_changed_at)
    if cover.get("checks", 0) < 2 or cover["pct"] >= floor:
        return None

    # Once a day. The condition persists for hours by its nature, and an
    # hourly reminder that the schedule is thin is itself noise.
    said = str(record.get("coverage_reported") or "")
    if said and said[:10] == now.isoformat()[:10]:
        return None
    # A schedule that changed two hours ago has not had time to be thin. The
    # first version of this alerted on its own reconfiguration.
    if cover.get("partial") and cover.get("window_hours", 0) < 6:
        return None

    longest = cover.get("longest_gap_minutes") or 0
    return {
        "pct": cover["pct"],
        "at": now.isoformat(timespec="seconds"),
        "subject": f"AutoTrader watcher covered only {cover['pct']}% of yesterday",
        # slots_covered, not successful. cover["pct"] is the share of SLOTS
        # that had a check; pairing it with the number of RUNS produced
        # "79.2% - 51 of 48 expected checks" on the dashboard, and this line
        # is the same sentence in the alert - the copy that nobody
        # screenshots, so it outlived the fix to the page by a day.
        "body": (f"{cover.get('slots_covered', cover['successful'])} of "
                 f"{cover['expected']} {_slot_word(expected)} in the last "
                 f"{cover['window_hours']} hours had a check, at one asked "
                 f"for every {expected} minutes.\n\n"
                 f"The longest gap was {longest / 60:.1f} hours. A car can be "
                 f"listed and sold inside a gap that size, so treat anything "
                 f"the dashboard says as a sample rather than the market.\n\n"
                 f"This is GitHub's scheduler rather than the bot: check the "
                 f"Actions tab to see how many runs were actually served."),
    }
