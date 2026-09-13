"""Publishing the data the dashboard reads.

The dashboard is a single static HTML file with no build step and no server.
It loads ``data.json`` from alongside itself, which this module writes after
every run.  That keeps the UI free to host (GitHub Pages, or just opening the
file) and keeps secrets out of it - only non-sensitive fields are exported.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import re

from .archive import size_report
from . import geo, insight, thumbs
from .config import CHANNEL_SECRETS, Config
from .listing import name_of
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
    # When each thing happened to it, so the feed can be derived rather than
    # stored twice and drift apart.
    "removed_at", "relisted_at", "priced_at", "qualified_at", "qualified_from",
    "photos_at", "seller_changed_at", "seller_was", "misses", "seller_type",
    # Why you did or did not hear about this car. The whole point of keeping
    # them is that "we never told you" is always a decision you can read back.
    "notified_at", "quiet_reason",
    # Your marks on a car: shortlisted, dismissed, muted, a note. Written
    # through the issue channel, because a static page cannot write.
    "you",
)


def _area_of(filters: dict[str, Any]) -> dict[str, Any] | None:
    """The distance rule, resolved, so the page can say what is enforced."""
    near = str((filters or {}).get("near") or "").strip()
    try:
        radius = int((filters or {}).get("max_distance_km") or 0)
    except (TypeError, ValueError):
        radius = 0
    provinces = [str(p).strip().upper()
                 for p in ((filters or {}).get("provinces") or []) if str(p).strip()]
    if not near and not radius and not provinces:
        return None
    out: dict[str, Any] = {"provinces": provinces}
    if near and radius:
        out.update(geo.summary(near, radius))
    elif near:
        out["reference"] = near
        out["text"] = f"near {near}"
    elif radius:
        out["radius_km"] = radius
        out["text"] = f"within {radius:,} km"
    if provinces and "text" not in out:
        out["text"] = "in " + ", ".join(provinces)
    return out


def build_payload(cfg: Config, state: State, env: dict[str, str] | None = None
                  ) -> dict[str, Any]:
    """Assemble everything the dashboard needs, with nothing secret in it."""
    # Normalised once, here. Two callers pass None - the local `ui` server and
    # `python -m autotrader dashboard` - and every use of env below has to
    # remember that. One of them did not, and both commands crashed with an
    # AttributeError until a render caught it.
    env = env or {}
    limit = int(cfg.get("dashboard.max_listings", 500) or 500)

    # Where "how far away is it" is measured from, per search. A search with
    # no distance rule has no reference and its cars simply do not carry one,
    # rather than quietly being measured from somewhere arbitrary.
    references: dict[str, Any] = {}
    reference_names: dict[str, str] = {}
    for search in cfg.searches:
        near = str((cfg.rules_for(search)["filters"] or {}).get("near") or "").strip()
        if not near:
            continue
        point = geo.locate_reference(near)
        if point:
            references[search.id] = point
            reference_names[search.id] = near

    photo_index = thumbs._load_index()

    listings: list[dict[str, Any]] = []
    for entry in state.listings.values():
        if entry.get("imported_from"):
            continue  # a bare id from v1 with no data - nothing to show
        # Filtered cars are published too, flagged, so the dashboard can say
        # "44 hidden by your rules" and show which ones and why. They are
        # still kept out of every count and list by default: the point is
        # that a number you can click on beats a number that silently omits.
        item = {k: entry.get(k) for k in LISTING_FIELDS if k in entry}
        # The published name, which is not always the recorded one - see
        # listing.name_of.
        item["title"] = name_of(entry)
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
        # The signals a person actually compares on, computed once here rather
        # than in the browser from data the browser does not have.
        item["photo_count"] = len(entry.get("images") or [])
        # Our own copy, when we have one. The remote URL stays as a fallback:
        # a photo we failed to fetch is still better than a grey box, and the
        # page tries local first so it works with no network at all.
        local = thumbs.local_for(entry.get("id"), photo_index)
        if local:
            item["thumb"] = local
        item["per_1000km"] = insight.per_1000km(entry.get("price"),
                                                entry.get("mileage_km"))
        first = entry.get("first_seen")
        if first:
            try:
                seen = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
                item["days_listed"] = max(
                    0, (datetime.now(timezone.utc) - seen).days)
            except ValueError:
                pass
        reference = references.get(entry.get("search_id") or "")
        here = (geo.locate(entry.get("location"), entry.get("province"))
                if reference else None)
        # A car the table cannot place carries no distance at all. Showing a
        # guessed one would be worse than showing none: the whole point of the
        # radius rule is that an unplaceable car is never excluded by it.
        if reference and here:
            item["distance_km"] = round(geo.distance_km(here, reference))
            item["distance_from"] = reference_names.get(
                entry.get("search_id") or "")
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
                # Reading the site fine and keeping nothing is not a failure,
                # but it looks exactly like one from the outside.
                "shut_out": health.get("shut_out"),
            },
            "active_count": counts_for(search.id)["active"],
            "counts": counts_for(search.id),
            "rules": {
                "filters": search.filters,
                "notify_on": search.notify_on,
                "price_drop_min_pct": search.price_drop_min_pct,
                "price_drop_min_abs": search.price_drop_min_abs,
            },
            # Spelled out separately, because a distance rule the bot enforces
            # itself is not visible anywhere in the search link - the link
            # still says "near V6N 3B5" and the site still ignores it.
            "area": _area_of(cfg.rules_for(search)["filters"]),
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
        "version": 3,
        # Where a change from the dashboard goes. Issues are the only write
        # channel a static page on Pages has, and the only one a phone can
        # use without carrying a token.
        "repo": _repo_slug(env),
        # Whether this repository has the Issues feature switched on, which
        # decides which of the two write channels the change button uses. Not
        # a guess: the workflow reads it out of the event payload and hands it
        # over. Absent means "assume not", which picks the channel that always
        # works rather than the one that may 404.
        "repo_issues": str(env.get("REPO_HAS_ISSUES", "")).lower() == "true",
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
        # Derived, not stored: the run counters only ever counted changes on
        # cars that passed the filters, so a price drop on a hidden car was
        # real, recorded, and missing from every number the bot printed.
        "events": insight.events(state.listings.values()),
        "comparables": insight.comparables(state.listings.values()),
        "coverage": insight.coverage(
            runs, int(cfg.get("health.expected_interval_minutes", 30) or 30)),
        "cost": insight.minutes_spent(runs),
        # What two hundred cars say together, rather than what one says. The
        # window block travels with it: most of this is two days old.
        "market": insight.market(state.listings.values(), runs=state.runs,
                                 since=state.watch_started),
        "score_check": insight.backtest(state.listings.values()),
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
# The first group is what this bot itself handles. The second is what GitHub's
# own push protection rejects - which matters even though the bot never
# creates one, because the bot commits config.json and docs/data.json on every
# run. A token pasted into a search's notes field by someone doing the obvious
# thing would be published to a public page *and* wedge every future push
# behind a rejection, which is a scheduled job that stops working with no
# error anyone reads. Cheaper to refuse it here, in words, with the field
# named.
_SECRET_VALUE_RES = (
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{30,}\b"),                 # Telegram bot token
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/"),  # Discord webhook
    re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/"),  # Slack webhook
    re.compile(r"\bSK[0-9a-f]{32}\b"),                             # Twilio key
    re.compile(r"\bAC[0-9a-f]{32}\b"),                             # Twilio account SID

    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),                 # GitHub token
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),               # GitHub fine-grained
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),              # Slack token
    re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"),        # AWS access key
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),                      # Google API key
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),            # OpenAI-style key
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{24,}\b"),          # Stripe
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


def _repo_slug(env: dict[str, str] | None) -> str:
    """owner/name for this repository, if it can be worked out."""
    env = env or {}
    slug = str(env.get("GITHUB_REPOSITORY") or "").strip()
    if slug:
        return slug
    try:
        import subprocess
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, Exception):  # noqa: BLE001 - absence is an answer
        return ""
    match = re.search(r"github\.com[:/]([^/]+/[^/.\s]+)", url or "")
    return match.group(1) if match else ""


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
    stamp_worker(path.parent)
    return path


SW_FILE = "sw.js"
SW_WATCHES = ("index.html", "app.js")
_BUILD_LINE = re.compile(r"^const BUILD = '([^']*)';$", re.M)


def stamp_worker(docs: Path) -> str | None:
    """Write the build id the service worker keys its caches on.

    A browser installs a new service worker when the bytes of sw.js change,
    and a new worker re-fetches everything it caches. That is the entire
    update mechanism for an installed app, and nothing else reliably is: the
    version this replaced tried to notice a changed app.js by re-fetching it
    behind the cached response and comparing the text, which - measured
    against a real browser and a real file change - picked up nothing on the
    next load or the one after. An installed app would have run whatever
    app.js it first saw forever.

    So the stamp is derived here, from the files it is meant to track, on
    every publish. Nobody has to remember to bump it, which is the only kind
    of version discipline that survives six months.
    """
    worker = docs / SW_FILE
    try:
        source = worker.read_text(encoding="utf-8")
    except OSError:
        return None

    digest = hashlib.sha256()
    for name in SW_WATCHES:
        try:
            digest.update((docs / name).read_bytes())
        except OSError:
            digest.update(b"missing")
    # The worker's own source, with the stamp line removed so hashing it does
    # not depend on the last stamp and change on every single publish.
    digest.update(_BUILD_LINE.sub("", source).encode("utf-8"))
    build = digest.hexdigest()[:12]

    updated = _BUILD_LINE.sub(f"const BUILD = '{build}';", source, count=1)
    if updated == source:
        return build          # already stamped with this build
    try:
        worker.write_text(updated, encoding="utf-8")
    except OSError as exc:
        log.warning("could not stamp %s: %s", worker, exc)
        return None
    return build
