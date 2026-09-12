"""The control channel: changing the configuration from a phone.

(Named control rather than requests. A module called requests sitting next to
`import requests` in the same package is a bug waiting for somebody tired.)

Changing the configuration from a phone, with no token and no terminal.

The dashboard is a static file on Pages. It cannot write to the repository,
so the Searches editor could only ever show you what a rule *would* do and
then hand you a command to type somewhere else. That is the last thing in
this system that needs a laptop.

A GitHub issue is a write channel that a phone already has: the dashboard
opens a prefilled one, a workflow reads it, and this module decides what it
means. The rules it follows:

* an instruction is applied whole or not at all - a half-applied config is
  worse than a rejected one, and much harder to notice;
* every rejection says what was wrong with the input, not that something was
  wrong;
* nothing here trusts the text. It comes from an issue body, which anyone who
  can open an issue can write, so every value is parsed and bounded rather
  than interpolated anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# The marker that says an issue is meant for the bot rather than for a person.
# Deliberately ugly: nobody types this by accident, and the dashboard writes
# it into the body it prefills.
FENCE = re.compile(r"```autotrader\s*\n(.*?)```", re.S | re.I)

ACTIONS = ("set-rule", "add-search", "remove-search", "mute-listing",
           "unmute-listing", "shortlist", "unshortlist", "dismiss",
           "set-channel", "note")

# Rules a person may change from a phone, and what counts as a value. Anything
# not on this list is refused by name rather than quietly ignored - a silent
# no-op is the worst possible answer to "did my change apply?".
RULE_TYPES: dict[str, type] = {
    "max_price": int, "min_price": int,
    "min_year": int, "max_year": int,
    "max_mileage_km": int, "max_distance_km": int,
    "near": str, "require_price": bool,
    "price_drop_min_pct": float, "price_drop_min_abs": int,
}
RULE_BOUNDS: dict[str, tuple[float, float]] = {
    "max_price": (1, 10_000_000), "min_price": (0, 10_000_000),
    "min_year": (1900, 2100), "max_year": (1900, 2100),
    "max_mileage_km": (0, 2_000_000), "max_distance_km": (1, 20_000),
    "price_drop_min_pct": (0, 100), "price_drop_min_abs": (0, 1_000_000),
}
LISTING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")
SEARCH_URL = re.compile(r"^https://(www\.)?autotrader\.ca/", re.I)


class Rejected(Exception):
    """The instruction was not usable, and this says exactly why."""


@dataclass
class Outcome:
    applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    changed: bool = False

    def comment(self) -> str:
        """What the bot says back on the issue before closing it."""
        lines = []
        if self.applied:
            lines.append("Applied:")
            lines += [f"- {line}" for line in self.applied]
        if self.rejected:
            if lines:
                lines.append("")
            lines.append("Refused, and nothing was changed by these:")
            lines += [f"- {line}" for line in self.rejected]
        if not lines:
            lines.append("Nothing to do: the instruction was empty.")
        if self.rejected and not self.applied:
            lines.append("")
            lines.append("Nothing was written. Edit the issue and reopen it, "
                         "or open a new one from the dashboard.")
        return "\n".join(lines)


def parse(body: str) -> list[dict[str, Any]]:
    """Pull the instructions out of an issue body.

    The body is markdown a person may have typed around, so only what is
    inside the fenced ```autotrader block counts. Everything else is prose.
    """
    match = FENCE.search(body or "")
    if not match:
        raise Rejected(
            "I could not find an ```autotrader block in this issue. The "
            "dashboard writes one for you - open the change from there rather "
            "than writing the issue by hand.")
    raw = match.group(1).strip()
    if not raw:
        raise Rejected("The ```autotrader block is empty.")
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Rejected(
            f"The ```autotrader block is not valid JSON: {exc.msg} at line "
            f"{exc.lineno}, column {exc.colno}.") from exc

    items = loaded if isinstance(loaded, list) else [loaded]
    if not items:
        raise Rejected("The ```autotrader block has no instructions in it.")
    if len(items) > 20:
        raise Rejected(f"{len(items)} instructions in one issue is more than "
                       f"this will apply at once. Split it up.")
    for item in items:
        if not isinstance(item, dict):
            raise Rejected(f"Each instruction has to be an object; got "
                           f"{type(item).__name__}.")
        action = str(item.get("action") or "")
        if action not in ACTIONS:
            raise Rejected(
                f"{action or '(no action)'} is not something I can do. "
                f"I know: {', '.join(ACTIONS)}.")
    return items


def _number(value: Any, name: str, kind: type) -> Any:
    try:
        out = kind(value)
    except (TypeError, ValueError):
        raise Rejected(f"{name} needs a number; got {value!r}.") from None
    low, high = RULE_BOUNDS.get(name, (float("-inf"), float("inf")))
    if not low <= out <= high:
        raise Rejected(f"{name} has to be between {low:g} and {high:g}; "
                       f"got {out:g}.")
    return out


def _rule_value(name: str, value: Any) -> Any:
    kind = RULE_TYPES.get(name)
    if kind is None:
        raise Rejected(
            f"{name} is not a rule I can change from here. I can change: "
            f"{', '.join(sorted(RULE_TYPES))}.")
    if value is None:
        return None                      # clearing a rule is a real request
    if kind is bool:
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "yes", "on", "1"):
            return True
        if str(value).lower() in ("false", "no", "off", "0"):
            return False
        raise Rejected(f"{name} is a yes/no rule; got {value!r}.")
    return _number(value, name, kind)


def _find_search(cfg, wanted: str):
    wanted = str(wanted or "").strip()
    if not wanted:
        return None
    for search in cfg.searches:
        if search.id == wanted or search.name.lower() == wanted.lower():
            return search
    return None


def apply(cfg, state, items: list[dict[str, Any]]) -> Outcome:
    """Carry out a parsed instruction set, or none of it.

    Everything is validated first and only then written, so a list whose
    third instruction is nonsense leaves the first two unapplied rather than
    half-applying a change nobody asked for.
    """
    out = Outcome()
    planned: list[tuple[str, Any]] = []

    for item in items:
        action = item["action"]
        try:
            planned.append((action, _plan(cfg, state, item)))
        except Rejected as exc:
            out.rejected.append(f"`{action}`: {exc}")

    if out.rejected:
        return out                        # whole or nothing

    for action, work in planned:
        out.applied.append(work())
    out.changed = bool(out.applied)
    return out


def _plan(cfg, state, item: dict[str, Any]):
    """Validate one instruction and return something that performs it."""
    action = item["action"]

    if action == "set-rule":
        search = _find_search(cfg, item.get("search"))
        if item.get("search") and not search:
            raise Rejected(f"there is no search called {item['search']!r}.")
        name = str(item.get("rule") or "")
        value = _rule_value(name, item.get("value"))
        where = f"searches.{search.id}.filters.{name}" if search else f"filters.{name}"
        label = f"{name} = {value!r}" + (f" on {search.name}" if search else " everywhere")

        def do():
            if search:
                raw = cfg.data.setdefault("searches", [])
                for row in raw:
                    if row.get("id") == search.id:
                        filters = row.setdefault("filters", {})
                        filters.pop(name, None) if value is None else filters.update({name: value})
                        break
            else:
                if value is None:
                    (cfg.data.get("filters") or {}).pop(name, None)
                else:
                    cfg.set(f"filters.{name}", value)
            return label
        do.__doc__ = where
        return do

    if action == "add-search":
        url = str(item.get("url") or "").strip()
        if not SEARCH_URL.match(url):
            raise Rejected("a search link has to be an https://www.autotrader.ca/ "
                           "address; got " + (url[:80] or "nothing") + ".")
        name = str(item.get("name") or "").strip()[:80]

        def do():
            search = cfg.add_search(url, name, strict=False)
            return f"added the search {search.name!r}"
        return do

    if action == "remove-search":
        search = _find_search(cfg, item.get("search"))
        if not search:
            raise Rejected(f"there is no search called {item.get('search')!r}.")
        if len(cfg.searches) <= 1:
            raise Rejected("that is the only search there is; removing it "
                           "would leave the bot watching nothing.")

        def do():
            cfg.remove_search(search.id)
            return f"removed the search {search.name!r}"
        return do

    if action in ("mute-listing", "unmute-listing", "shortlist",
                  "unshortlist", "dismiss", "note"):
        listing_id = str(item.get("listing") or "").strip()
        if not LISTING_ID.match(listing_id):
            raise Rejected(f"{listing_id[:40]!r} is not a listing id.")
        if listing_id not in state.listings:
            raise Rejected(f"there is no listing {listing_id[:12]}… in state.")
        text = str(item.get("text") or "")[:400]

        def do():
            entry = state.listings[listing_id]
            marks = entry.setdefault("you", {})
            title = (entry.get("title") or listing_id)[:40]
            if action == "mute-listing":
                marks["muted"] = True
                return f"muted {title} - no more alerts about it"
            if action == "unmute-listing":
                marks.pop("muted", None)
                return f"unmuted {title}"
            if action == "shortlist":
                marks["shortlisted"] = True
                marks.pop("dismissed", None)
                return f"shortlisted {title} - its price drops come through louder"
            if action == "unshortlist":
                marks.pop("shortlisted", None)
                return f"took {title} off the shortlist"
            if action == "dismiss":
                marks["dismissed"] = True
                marks.pop("shortlisted", None)
                return f"dismissed {title} - it stays quiet"
            marks["note"] = text
            return f"noted on {title}: {text[:60]}"
        return do

    if action == "set-channel":
        channel = str(item.get("channel") or "").strip().lower()
        known = set((cfg.get("notifications.channels", {}) or {}))
        if channel not in known:
            raise Rejected(f"{channel or '(none)'} is not a channel here. "
                           f"There is: {', '.join(sorted(known)) or 'none'}.")
        on = item.get("enabled")
        if not isinstance(on, bool):
            raise Rejected("enabled has to be true or false.")

        def do():
            cfg.set(f"notifications.channels.{channel}.enabled", on)
            return f"turned {channel} {'on' if on else 'off'}"
        return do

    raise Rejected("I do not know how to do that.")   # unreachable via parse()
