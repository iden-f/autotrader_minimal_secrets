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


def _delivery(entry: dict[str, Any]) -> str:
    """What the bot did about this car, in the words it recorded at the time."""
    if entry.get("pending"):
        return "queued, not yet delivered"
    if entry.get("notified_at"):
        stamp = entry["notified_at"]
        if entry.get("notified_at_backfilled"):
            return f"delivered (time reconstructed, {stamp})"
        return f"delivered at {stamp}"
    if entry.get("quiet_reason"):
        return f"deliberately quiet: {entry['quiet_reason']}"
    return "no record - which is itself a fault the run should have caught"


def _run_at(runs: list[dict[str, Any]], when: str) -> dict[str, Any]:
    """The run that was happening when this was recorded."""
    best: dict[str, Any] = {}
    for run in runs:
        if str(run.get("at") or "") <= str(when or ""):
            if not best or str(run.get("at")) > str(best.get("at")):
                best = run
    keep = ("at", "ok", "listings_seen", "new", "price_drops", "price_rises",
            "removed", "relisted", "priced", "requests_made", "notified")
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
        if kind not in out or event.at < out[kind].get("at", "9999"):
            out[kind] = event.to_dict()
    return {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "first": out,
            "waiting": [k for k in KINDS if k not in out],
            # Which silence has already been reported, so a bot that stays
            # quiet is announced once rather than once an hour.
            "silence_reported": previous.get("silence_reported", "")}


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

    last_ok = ""
    for run in (state.data.get("runs") or []):
        if run.get("ok") and str(run.get("at") or "") > last_ok:
            last_ok = str(run["at"])
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
    return {
        "since": last_ok,
        "hours": round(quiet_for, 1),
        "subject": "AutoTrader watcher has gone quiet",
        "body": (f"The last successful check was {quiet_for:.1f} hours ago "
                 f"({last_ok}), and it is supposed to run every "
                 f"{(cfg.get('health', {}) or {}).get('expected_interval_minutes', 30)} "
                 f"minutes.\n\nNothing is being watched while this is true. "
                 f"Check the Actions tab: GitHub disables scheduled workflows "
                 f"on repositories with 60 days of no activity, and drops "
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
