"""Making the bot work on its first run without anyone configuring anything.

Two problems this solves:

* Every notification channel needs a token from somewhere, so a fresh install
  watches diligently and tells nobody. ntfy is the exception - it needs no
  account at all, just a topic name - so one is generated here and written into
  config.json, and the subscribe link is surfaced everywhere the user might
  look.
* v1 kept its search in a `SEARCH_URL` repository secret. Reading that on every
  run means two sources of truth forever. It is instead consumed exactly once,
  written into config.json, and never consulted again.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import secrets
from pathlib import Path
from typing import Any

from .config import Config
from .urls import describe_search, normalise_search_url

log = logging.getLogger(__name__)

NOTIFY_FILE = Path("NOTIFY.md")

# Unambiguous characters only: this ends up typed into a phone by hand.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_TOPIC_LENGTH = 20


def generate_topic(prefix: str = "autotrader") -> str:
    """A topic nobody can guess.

    ntfy has no accounts: whoever knows the topic receives the messages. 20
    characters from a 31-character alphabet is about 99 bits, which is far
    past guessable, so the only realistic exposure is somebody reading it out
    of the repository - see the warning written into NOTIFY.md.
    """
    tail = "".join(secrets.choice(_ALPHABET) for _ in range(_TOPIC_LENGTH))
    return f"{prefix}-{tail}"


def subscribe_url(cfg: Config) -> str:
    server = str(cfg.get("notifications.channels.ntfy.server")
                 or "https://ntfy.sh").rstrip("/")
    topic = str(cfg.get("notifications.channels.ntfy.topic") or "").strip()
    return f"{server}/{topic}" if topic else ""


def write_notify_file(cfg: Config, path: Path = NOTIFY_FILE) -> Path | None:
    """Leave the subscribe link somewhere impossible to miss."""
    url = subscribe_url(cfg)
    if not url:
        return None
    topic = url.rsplit("/", 1)[-1]
    path.write_text(f"""# Where your alerts go

Your watcher sends new listings and price drops to a private **ntfy** topic.
No account, no token, nothing to sign up for.

## Get them on your phone

1. Install **ntfy** — [iOS](https://apps.apple.com/app/ntfy/id1625396347) ·
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) ·
   [F-Droid](https://f-droid.org/packages/io.heckel.ntfy/)
2. Tap **+** to subscribe to a topic.
3. Enter exactly:

   ```
   {topic}
   ```

That is it. The next time the bot finds something, your phone buzzes.

## Or just open it in a browser

<{url}>

Leave the tab open and messages appear live.

## Worth knowing

ntfy topics are not secret by design — **anyone who knows this topic name can
read your alerts**. The name is long and random, so nobody will guess it, but
it is written in this file, and this repository is public. The alerts only
contain public AutoTrader listings, so there is little to leak; if that still
bothers you, either make the repository private, or switch to a channel with
real authentication:

- **Telegram** — set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as repository
  secrets and it takes over automatically. See [SETUP.md](SETUP.md).
- **Discord / Slack** — set `DISCORD_WEBHOOK_URL` or `SLACK_WEBHOOK_URL`.
- **Email** — set `GMAIL_USER` and `GMAIL_APP_PASSWORD`.

Adding any of those does not switch ntfy off. To stop ntfy, set
`notifications.channels.ntfy.enabled` to `false` in `config.json`, or turn it
off in the dashboard's Settings tab.

## Change the topic

Pick a new one in the dashboard, or:

```bash
python -m autotrader setup --new-topic
```

*This file is written by the bot. Editing it changes nothing — the topic lives
in `config.json`.*
""", encoding="utf-8")
    return path


def ensure_notifications(cfg: Config, env: dict[str, str] | None = None
                         ) -> dict[str, Any]:
    """Guarantee the bot can reach the user somehow.

    Does nothing if any channel is already working, so adding a Telegram token
    later does not disturb this and running it twice is harmless.
    """
    env = env if env is not None else dict(os.environ)
    active = cfg.active_channels(env)
    if active:
        return {"changed": False, "reason": f"already reachable via {', '.join(active)}",
                "channels": active}

    topic = str(cfg.get("notifications.channels.ntfy.topic") or "").strip()
    if not topic:
        topic = generate_topic()
        cfg.set("notifications.channels.ntfy.topic", topic)
    cfg.set("notifications.channels.ntfy.enabled", True)
    return {"changed": True, "reason": "no channel was configured, so ntfy was set up",
            "topic": topic, "url": subscribe_url(cfg), "channels": ["ntfy"]}


def adopt_legacy_search(cfg: Config, env: dict[str, str] | None = None
                        ) -> dict[str, Any]:
    """Move the v1 `SEARCH_URL` secret into config.json, once and for all.

    After this, the secret is dead weight: `Config` no longer reads it, so
    leaving it set cannot cause a surprise later. The caller tells the user
    they may delete it.
    """
    env = env if env is not None else dict(os.environ)
    raw = normalise_search_url(env.get("SEARCH_URL", ""))
    if not raw:
        return {"changed": False, "reason": "no SEARCH_URL secret is set"}
    if cfg.data.get("legacy_search_url_adopted"):
        return {"changed": False, "reason": "SEARCH_URL was already adopted; it is ignored now"}
    if any(s.url == raw for s in cfg.searches):
        cfg.data["legacy_search_url_adopted"] = True
        return {"changed": True, "reason": "SEARCH_URL already matched a configured search",
                "url": raw}

    summary = describe_search(raw)
    search = cfg.add_search(raw, summary.title() if summary.valid else "Imported search",
                            strict=False)
    for entry in cfg.data["searches"]:
        if entry["id"] == search.id:
            entry["notes"] = ("Imported from the SEARCH_URL repository secret. "
                              "That secret is no longer read - you can delete it.")
    cfg.data["legacy_search_url_adopted"] = True
    return {"changed": True, "reason": "adopted the SEARCH_URL secret into config.json",
            "url": raw, "name": search.name}


def find_dashboard_url(env: dict[str, str] | None = None) -> str:
    """Where this repository publishes its dashboard, if it can be worked out.

    An alert that opens the car it is about beats one that opens the site's
    search page, and the address is derivable rather than something anyone
    should have to type. Inside Actions the owner and repo are handed to us;
    outside it, the git remote says the same thing.
    """
    env = env if env is not None else dict(os.environ)
    slug = str(env.get("GITHUB_REPOSITORY") or "").strip()
    if not slug:
        try:
            slug = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    match = re.search(r"(?:github\.com[:/])?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$", slug)
    if not match:
        return ""
    return f"https://{match.group(1).lower()}.github.io/{match.group(2)}"


def ensure_dashboard_url(cfg: Config, env: dict[str, str] | None = None
                         ) -> dict[str, Any]:
    """Fill in where the dashboard lives, once, without overwriting a choice."""
    if str(cfg.get("notifications.dashboard_url") or "").strip():
        return {"changed": False, "detail": "already set"}
    found = find_dashboard_url(env)
    if not found:
        return {"changed": False, "detail": "no git remote to work it out from"}
    cfg.set("notifications.dashboard_url", found)
    return {"changed": True, "detail": found}


def bootstrap(cfg: Config, env: dict[str, str] | None = None,
              *, write_files: bool = True) -> dict[str, Any]:
    """Everything a first run needs, idempotently.

    Safe to call on every run: it only acts when something is genuinely
    missing, and reports what it did so the caller can say so out loud.
    """
    env = env if env is not None else dict(os.environ)
    steps: list[dict[str, Any]] = []

    legacy = adopt_legacy_search(cfg, env)
    legacy["step"] = "search"
    steps.append(legacy)

    channels = ensure_notifications(cfg, env)
    channels["step"] = "notifications"
    steps.append(channels)

    where = ensure_dashboard_url(cfg, env)
    where["step"] = "dashboard"
    steps.append(where)

    changed = any(s.get("changed") for s in steps)
    if changed:
        cfg.save()
    if write_files and cfg.get("notifications.channels.ntfy.enabled") is True:
        write_notify_file(cfg)

    return {"changed": changed, "steps": steps,
            "subscribe_url": subscribe_url(cfg),
            "channels": cfg.active_channels(env)}
