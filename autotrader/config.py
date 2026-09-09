"""Configuration: non-secret preferences live in config.json, secrets in env.

The split matters.  ``config.json`` is committed to the repository so the
dashboard can read it and the settings UI can edit it; anything that would be
embarrassing in a public repo (tokens, passwords) is read from the environment
instead, which on GitHub Actions means repository secrets.
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .urls import describe_search, normalise_search_url

CONFIG_PATH = Path(os.getenv("AUTOTRADER_CONFIG", "config.json"))

# Every channel and the environment variables it needs.  ``optional`` entries
# have sane defaults, so a channel counts as "configured" once all of its
# required variables are set.
CHANNEL_SECRETS: dict[str, dict[str, Any]] = {
    "telegram": {
        "label": "Telegram",
        "free": True,
        "required": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
        "help": "Message @BotFather -> /newbot to get a token, then message "
                "your new bot once and open "
                "https://api.telegram.org/bot<TOKEN>/getUpdates to read your chat id.",
    },
    "discord": {
        "label": "Discord",
        "free": True,
        "required": ["DISCORD_WEBHOOK_URL"],
        "help": "Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL.",
    },
    "ntfy": {
        "label": "ntfy",
        "free": True,
        "required": [],
        "help": "Install the ntfy app, subscribe to a topic nobody can guess, "
                "and put that topic name in Settings. No account needed.",
    },
    "slack": {
        "label": "Slack",
        "free": True,
        "required": ["SLACK_WEBHOOK_URL"],
        "help": "Create a Slack app -> Incoming Webhooks -> Add New Webhook.",
    },
    "email": {
        "label": "Email (Gmail)",
        "free": True,
        "required": ["GMAIL_USER", "GMAIL_APP_PASSWORD"],
        "help": "Turn on 2-Step Verification, then create an App Password at "
                "https://myaccount.google.com/apppasswords.",
    },
    "webhook": {
        "label": "Custom webhook",
        "free": True,
        "required": [],
        "help": "Any URL that accepts a JSON POST.",
    },
    "twilio": {
        "label": "SMS (Twilio)",
        "free": False,
        "required": ["TWILIO_SID", "TWILIO_TOKEN", "TWILIO_FROM", "TWILIO_TO"],
        "help": "Costs money per message and per phone number. Telegram or "
                "ntfy give you the same phone alert for free.",
    },
}

DEFAULTS: dict[str, Any] = {
    "version": 2,
    # Set once the v1 SEARCH_URL secret has been copied in; after that the
    # secret is never read again.
    "legacy_search_url_adopted": False,
    "searches": [],
    "filters": {
        "min_price": None,
        "max_price": None,
        "min_year": None,
        "max_year": None,
        "max_mileage_km": None,
        "include_keywords": [],
        "exclude_keywords": [],
        "exclude_sellers": [],
        "require_price": False,
    },
    "notifications": {
        "digest": True,
        "max_listings_per_message": 12,
        "timezone": "America/Toronto",
        "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"},
        "notify_on": {
            "new": True,
            "price_drop": True,
            "price_rise": False,
            "removed": False,
            "errors": True,
        },
        "price_drop_min_pct": 1.0,
        "price_drop_min_abs": 250,
        "channels": {
            "telegram": {"enabled": "auto", "photos": True},
            "discord": {"enabled": "auto"},
            "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": "", "priority": "default"},
            "slack": {"enabled": "auto"},
            "email": {"enabled": "auto", "to": "", "html": True},
            "webhook": {"enabled": False, "url": ""},
            "twilio": {"enabled": False},
        },
    },
    "scraping": {
        "max_pages": 3,
        "results_per_page": 50,
        "timeout_seconds": 30,
        "retries": 3,
        "delay_ms": 1200,
        "enrich_details": True,
        "enrich_limit": 25,
        # A ceiling on all HTTP requests in one run (search pages, detail pages
        # and photos), so a misconfigured crawl cannot hammer the site.
        "request_budget": 250,
        "user_agent": "auto",
    },
    "archive": {
        # "off" keeps the repository small; "metadata" adds a small JSON file
        # per car; "full" also stores the raw HTML (this is what bloated the
        # old repo to 9.6 MB for 50 cars).
        "mode": "metadata",
        "images": 0,
        "keep_last": 400,
        "keep_days": 730,
    },
    "health": {
        "alert_after_failures": 3,
        "heartbeat_hours": 0,
    },
    "dashboard": {"enabled": True, "max_listings": 500},
}


class ConfigError(ValueError):
    """Raised when a config file cannot be used as-is."""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "search"


def _merge(base: Any, override: Any) -> Any:
    """Recursively overlay ``override`` on ``base`` without dropping defaults."""
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = _merge(base[key], value) if key in base else value
        return out
    return override


@dataclass
class Search:
    id: str
    name: str
    url: str
    enabled: bool = True
    max_pages: int | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any], index: int = 0) -> "Search":
        url = normalise_search_url(str(raw.get("url", "")))
        name = str(raw.get("name") or "").strip()
        if not name:
            name = describe_search(url).title() if url else f"Search {index + 1}"
        sid = str(raw.get("id") or "").strip() or f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        max_pages = raw.get("max_pages")
        return cls(
            id=sid,
            name=name,
            url=url,
            enabled=bool(raw.get("enabled", True)),
            max_pages=int(max_pages) if max_pages else None,
            notes=str(raw.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "name": self.name, "url": self.url, "enabled": self.enabled,
        }
        if self.max_pages:
            out["max_pages"] = self.max_pages
        if self.notes:
            out["notes"] = self.notes
        return out


@dataclass
class Config:
    data: dict[str, Any]
    path: Path = CONFIG_PATH

    # ---------------- loading / saving ----------------

    @classmethod
    def defaults(cls, path: Path = CONFIG_PATH) -> "Config":
        return cls(copy.deepcopy(DEFAULTS), path)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        path = Path(path) if path else CONFIG_PATH
        if not path.exists():
            cfg = cls.defaults(path)
        else:
            try:
                raw = json.loads(path.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise ConfigError(f"{path} must contain a JSON object.")
            cfg = cls(_merge(copy.deepcopy(DEFAULTS), raw), path)
        cfg.apply_env_overrides()
        cfg.normalise()
        return cfg

    def apply_env_overrides(self) -> None:
        """Deliberately does nothing to the search list.

        v1 read its search from a `SEARCH_URL` repository secret. Reading that
        on every run would leave two sources of truth forever: an invisible
        secret and a visible config file, disagreeing silently.

        It is instead adopted exactly once, by `provision.adopt_legacy_search`,
        which copies it into config.json and sets `legacy_search_url_adopted`.
        From then on the secret is inert and may be deleted. Nothing here reads
        the environment, so a stale secret cannot resurrect a search the user
        removed on purpose.
        """
        return

    def normalise(self) -> None:
        seen: set[str] = set()
        searches: list[dict[str, Any]] = []
        for i, raw in enumerate(self.data.get("searches") or []):
            if not isinstance(raw, dict):
                continue
            search = Search.from_dict(raw, i)
            if not search.url:
                continue
            if search.id in seen:
                search.id = f"{search.id}-{uuid.uuid4().hex[:4]}"
            seen.add(search.id)
            searches.append(search.to_dict())
        self.data["searches"] = searches
        self.data["version"] = 2

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path else self.path
        self.normalise()
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        tmp.replace(target)
        return target

    # ---------------- accessors ----------------

    @property
    def searches(self) -> list[Search]:
        return [Search.from_dict(s, i) for i, s in enumerate(self.data.get("searches", []))]

    @property
    def active_searches(self) -> list[Search]:
        return [s for s in self.searches if s.enabled and s.url]

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    # ---------------- searches ----------------

    def add_search(self, url: str, name: str = "", *, strict: bool = True) -> Search:
        """Add a pasted search link.  Returns the stored Search."""
        url = normalise_search_url(url)
        summary = describe_search(url)
        if strict and not summary.valid:
            raise ConfigError(summary.problems[0] if summary.problems
                              else "That does not look like an AutoTrader search link.")
        for existing in self.searches:
            if existing.url == url:
                raise ConfigError(f"That search is already being watched as "
                                  f"\"{existing.name}\".")
        search = Search.from_dict({"name": name or summary.title(), "url": url})
        self.data.setdefault("searches", []).append(search.to_dict())
        return search

    def remove_search(self, search_id: str) -> bool:
        before = len(self.data.get("searches", []))
        self.data["searches"] = [s for s in self.data.get("searches", [])
                                 if s.get("id") != search_id]
        return len(self.data["searches"]) < before

    # ---------------- channels ----------------

    def channel_status(self, env: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
        """Report, per channel, whether it is on and what is still missing."""
        env = env if env is not None else dict(os.environ)
        out: dict[str, dict[str, Any]] = {}
        for name, spec in CHANNEL_SECRETS.items():
            conf = self.get(f"notifications.channels.{name}", {}) or {}
            missing = [k for k in spec["required"] if not (env.get(k) or "").strip()]
            if name == "ntfy" and not str(conf.get("topic", "")).strip():
                missing = missing + ["topic (set it in Settings)"]
            if name == "webhook" and not str(conf.get("url", "")).strip():
                missing = missing + ["url (set it in Settings)"]
            setting = conf.get("enabled", "auto")
            if setting == "auto":
                # "auto" means: switch on as soon as the secrets exist.
                active = not missing
            else:
                active = bool(setting) and not missing
            out[name] = {
                "label": spec["label"],
                "free": spec["free"],
                "help": spec["help"],
                "required": spec["required"],
                "setting": setting,
                "missing": missing,
                "active": active,
                "config": conf,
            }
        return out

    def active_channels(self, env: dict[str, str] | None = None) -> list[str]:
        return [n for n, s in self.channel_status(env).items() if s["active"]]
