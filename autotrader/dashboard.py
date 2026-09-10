"""Publishing the data the dashboard reads.

The dashboard is a single static HTML file with no build step and no server.
It loads ``data.json`` from alongside itself, which this module writes after
every run.  That keeps the UI free to host (GitHub Pages, or just opening the
file) and keeps secrets out of it - only non-sensitive fields are exported.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import re

from .archive import size_report
from .config import CHANNEL_SECRETS, Config
from .parser import STRATEGIES
from .state import State
from .urls import describe_search

# The ladder, in order, so the dashboard can show which rungs are missing
# rather than only which one happened to win.
STRATEGY_ORDER = tuple(name for name, _ in STRATEGIES)

DOCS_DIR = Path("docs")
DATA_FILE = DOCS_DIR / "data.json"

# Fields the dashboard renders. Everything else stays out of the published file.
LISTING_FIELDS = (
    "id", "url", "title", "year", "make", "model", "trim", "price", "currency",
    "mileage_km", "location", "province", "seller", "body", "color",
    "transmission", "drivetrain", "fuel", "engine", "images", "search_id",
    "search_name", "first_seen", "last_seen", "status", "price_history",
    "price_source", "filtered", "filter_reason", "unpriced", "enriched",
    # Why you did or did not hear about this car. The whole point of keeping
    # them is that "we never told you" is always a decision you can read back.
    "notified_at", "quiet_reason",
)


def build_payload(cfg: Config, state: State, env: dict[str, str] | None = None
                  ) -> dict[str, Any]:
    """Assemble everything the dashboard needs, with nothing secret in it."""
    limit = int(cfg.get("dashboard.max_listings", 500) or 500)

    listings: list[dict[str, Any]] = []
    for entry in state.listings.values():
        if entry.get("imported_from"):
            continue  # a bare id from v1 with no data - nothing to show
        # Filtered cars are published too, flagged, so the dashboard can say
        # "44 hidden by your rules" and show which ones and why. They are
        # still kept out of every count and list by default: the point is
        # that a number you can click on beats a number that silently omits.
        item = {k: entry.get(k) for k in LISTING_FIELDS if k in entry}
        item["filtered"] = bool(entry.get("filtered"))
        item["unpriced"] = entry.get("price") is None
        item["price_history"] = (entry.get("price_history") or [])[-20:]
        item["is_new"] = False
        if item["filtered"]:
            # A hidden car needs to be listable and explainable, not browsable.
            # Carrying its photos and price history triples the size of the
            # published file for rows nobody scrolls through.
            item.pop("images", None)
            item["price_history"] = item["price_history"][-2:]
        history = item["price_history"]
        if len(history) >= 2 and history[0].get("price") and history[-1].get("price"):
            item["price_change"] = history[-1]["price"] - history[0]["price"]
        listings.append(item)

    listings.sort(key=lambda item: (not item.get("filtered"),
                                    item.get("first_seen") or "", item.get("id")),
                  reverse=True)
    listings = listings[:limit]

    def counts_for(search_id: str | None = None) -> dict[str, int]:
        rows = [l for l in listings
                if search_id is None or l.get("search_id") == search_id]
        live = [l for l in rows if l.get("status") == "active"]
        return {
            "active": sum(1 for l in live if not l.get("filtered")),
            "filtered": sum(1 for l in live if l.get("filtered")),
            "unpriced": sum(1 for l in live if l.get("unpriced")
                            and not l.get("filtered")),
            "gone": sum(1 for l in rows if l.get("status") == "gone"),
            "total": len(rows),
        }

    searches = []
    for search in cfg.searches:
        health = (state.data.get("searches") or {}).get(search.id, {})
        summary = describe_search(search.url)
        searches.append({
            "id": search.id, "name": search.name, "url": search.url,
            "enabled": search.enabled, "notes": search.notes,
            "summary": summary.to_dict(),
            "health": {
                "last_ok": health.get("last_ok"),
                "last_error": health.get("last_error"),
                "last_count": health.get("last_count", 0),
                "last_strategy": health.get("last_strategy"),
                "consecutive_failures": health.get("consecutive_failures", 0),
            },
            "active_count": counts_for(search.id)["active"],
            "counts": counts_for(search.id),
            "rules": {
                "filters": search.filters,
                "notify_on": search.notify_on,
                "price_drop_min_pct": search.price_drop_min_pct,
                "price_drop_min_abs": search.price_drop_min_abs,
            },
            "shape": health.get("shape"),
        })

    channels = {}
    for name, status in cfg.channel_status(env).items():
        channels[name] = {
            "label": status["label"], "free": status["free"], "help": status["help"],
            "required": status["required"], "setting": status["setting"],
            # Only whether a secret is present, never its value.
            "missing": status["missing"], "active": status["active"],
        }

    # What the bot knows about itself. The dashboard used to show cars and
    # nothing else, which meant every question about whether it was still
    # working - is a strategy failing? did a channel die? is a search being
    # read at all? - had to be answered by reading state.json by hand.
    runs = (state.data.get("runs") or [])
    strategies: dict[str, Any] = {}
    for search in cfg.searches:
        shape = ((state.data.get("searches") or {}).get(search.id, {}) or {}).get("shape") or {}
        scores = shape.get("scores") or {}
        strategies[search.id] = {
            "name": search.name,
            "winner": shape.get("strategy"),
            "scores": scores,
            "working": shape.get("working") or [],
            "of": len(STRATEGY_ORDER),
            "order": list(STRATEGY_ORDER),
            "markers": shape.get("markers") or {},
        }

    last = state.last_run or {}
    ok_streak = 0
    for run in runs:
        if not run.get("ok"):
            break
        ok_streak += 1

    # Every car is delivered, owed, or deliberately quiet. Anything else is a
    # car that mattered and was never mentioned, which is the failure the
    # whole notification path is built to make impossible.
    accounted = {"delivered": 0, "queued": 0, "quiet": 0, "unexplained": 0}
    for entry in state.listings.values():
        if entry.get("imported_from") or entry.get("migrated_from"):
            continue
        if entry.get("pending"):
            accounted["queued"] += 1
        elif entry.get("notified_at"):
            accounted["delivered"] += 1
        elif entry.get("quiet_reason"):
            accounted["quiet"] += 1
        else:
            accounted["unexplained"] += 1

    health = {
        "counts": counts_for(),
        "accounted": accounted,
        "strategies": strategies,
        "drift": last.get("shape_drift") or [],
        "ok_streak": ok_streak,
        "runs_kept": len(runs),
        "budget": {"used": last.get("requests_made", 0),
                   "limit": int(cfg.get("scraping.request_budget", 0) or 0),
                   "exhausted": bool(last.get("budget_exhausted"))},
        "pending": sum(1 for e in state.listings.values() if e.get("pending")),
        "diagnostics": last.get("diagnostics") or [],
    }

    ntfy = cfg.get("notifications.channels.ntfy", {}) or {}
    topic = str(ntfy.get("topic") or "").strip()
    server = str(ntfy.get("server") or "https://ntfy.sh").rstrip("/")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Where alerts actually go, so the dashboard can show it. An ntfy topic
        # is a destination, not a credential - anyone with the link can
        # subscribe, which NOTIFY.md says plainly.
        "notify": {
            "ntfy_topic": topic,
            "ntfy_url": f"{server}/{topic}" if topic else "",
            "active": [n for n, c in channels.items() if c["active"]],
        },
        "version": 2,
        "stats": state.stats(),
        "listings": listings,
        "searches": searches,
        "channels": channels,
        "channel_catalog": {k: {"label": v["label"], "free": v["free"],
                                "required": v["required"], "help": v["help"]}
                            for k, v in CHANNEL_SECRETS.items()},
        "runs": (state.data.get("runs") or [])[:30],
        "channel_health": {
            name: {"last_ok": h.get("last_ok"),
                   "last_error": h.get("last_error"),
                   "disabled_at": h.get("disabled_at"),
                   "consecutive_failures": h.get("consecutive_failures", 0)}
            for name, h in (state.data.get("channels") or {}).items()
        },
        "last_run": state.last_run,
        "health": health,
        "archive": size_report(),
        "config": _safe_config(cfg),
    }


# Keys whose *value* would be a credential. Matching on the key name alone is
# not enough and matching on the whole document is far too eager: data.json
# legitimately contains the strings "TELEGRAM_BOT_TOKEN" (as the name of a
# secret to set) and "...bot<TOKEN>/getUpdates" (as help text).
_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|api[_-]?key|credential|auth)", re.I)

# Shapes of real credentials, in case one arrives under an innocent key.
_SECRET_VALUE_RES = (
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{30,}\b"),                 # Telegram bot token
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/"),  # Discord webhook
    re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/"),  # Slack webhook
    re.compile(r"\bSK[0-9a-f]{32}\b"),                             # Twilio key
    re.compile(r"\bAC[0-9a-f]{32}\b"),                             # Twilio account SID
)


def find_secrets(node: Any, path: str = "") -> list[str]:
    """Locate anything credential-shaped in a payload bound for a public page.

    Returns human-readable locations, empty when the payload is clean.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            where = f"{path}.{key}" if path else str(key)
            if (_SECRET_KEY_RE.search(str(key)) and isinstance(value, str)
                    and value.strip()):
                found.append(f"{where} holds a non-empty value")
            found.extend(find_secrets(value, where))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(find_secrets(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        for pattern in _SECRET_VALUE_RES:
            if pattern.search(node):
                found.append(f"{path} looks like a credential")
                break
    return found


def _safe_config(cfg: Config) -> dict[str, Any]:
    """The editable settings, minus anything that could hold a secret."""
    data = json.loads(json.dumps(cfg.data))
    for name, channel in (data.get("notifications", {}).get("channels", {}) or {}).items():
        if isinstance(channel, dict):
            channel.pop("token", None)
            channel.pop("password", None)
    return data


def write(cfg: Config, state: State, env: dict[str, str] | None = None,
          path: Path = DATA_FILE) -> Path | None:
    """Write data.json for the dashboard.  Returns the path, or None if off."""
    if not cfg.get("dashboard.enabled", True):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(cfg, state, env)
    # This file is published to a public URL. Nothing that looks like a
    # credential may leave here, whatever ended up in config.json.
    leaks = find_secrets(payload)
    if leaks:
        raise ValueError(
            "refusing to write the dashboard: credential-shaped data at "
            + "; ".join(leaks[:5])
            + ". Move it to an environment variable or GitHub secret.")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path
