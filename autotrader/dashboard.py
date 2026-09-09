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

from .archive import size_report
from .config import CHANNEL_SECRETS, Config
from .state import State
from .urls import describe_search

DOCS_DIR = Path("docs")
DATA_FILE = DOCS_DIR / "data.json"

# Fields the dashboard renders. Everything else stays out of the published file.
LISTING_FIELDS = (
    "id", "url", "title", "year", "make", "model", "trim", "price", "currency",
    "mileage_km", "location", "province", "seller", "body", "color",
    "transmission", "drivetrain", "fuel", "engine", "images", "search_id",
    "search_name", "first_seen", "last_seen", "status", "price_history",
    "price_source",
)


def build_payload(cfg: Config, state: State, env: dict[str, str] | None = None
                  ) -> dict[str, Any]:
    """Assemble everything the dashboard needs, with nothing secret in it."""
    limit = int(cfg.get("dashboard.max_listings", 500) or 500)

    listings: list[dict[str, Any]] = []
    for entry in state.listings.values():
        if entry.get("imported_from"):
            continue  # a bare id from v1 with no data - nothing to show
        item = {k: entry.get(k) for k in LISTING_FIELDS if k in entry}
        item["price_history"] = (entry.get("price_history") or [])[-20:]
        item["is_new"] = False
        history = item["price_history"]
        if len(history) >= 2 and history[0].get("price") and history[-1].get("price"):
            item["price_change"] = history[-1]["price"] - history[0]["price"]
        listings.append(item)

    listings.sort(key=lambda item: (item.get("first_seen") or "", item.get("id")),
                  reverse=True)
    listings = listings[:limit]

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
            "active_count": sum(1 for l in listings
                                if l.get("search_id") == search.id
                                and l.get("status") == "active"),
        })

    channels = {}
    for name, status in cfg.channel_status(env).items():
        channels[name] = {
            "label": status["label"], "free": status["free"], "help": status["help"],
            "required": status["required"], "setting": status["setting"],
            # Only whether a secret is present, never its value.
            "missing": status["missing"], "active": status["active"],
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": 2,
        "stats": state.stats(),
        "listings": listings,
        "searches": searches,
        "channels": channels,
        "channel_catalog": {k: {"label": v["label"], "free": v["free"],
                                "required": v["required"], "help": v["help"]}
                            for k, v in CHANNEL_SECRETS.items()},
        "runs": (state.data.get("runs") or [])[:30],
        "last_run": state.last_run,
        "archive": size_report(),
        "config": _safe_config(cfg),
    }


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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path
