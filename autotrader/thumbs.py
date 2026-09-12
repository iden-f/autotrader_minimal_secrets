"""Keeping our own copy of the photos.

The dashboard used to point <img> straight at the seller's CDN. That is free
and it is wrong three ways: the page does not work offline or in the installed
app, the images vanish the day the car is delisted or the CDN moves, and the
person who built this had never actually seen one - every screenshot taken
while building it showed the fallback, because the network that renders them
cannot reach autoscout24.

So the bot fetches them itself, on the runs that have a real network, and
commits small copies alongside the data. The CDN already serves a 250px
variant, so there is nothing to re-encode and no image library to install -
the work is fetching, checking that what came back is actually an image, and
staying inside a budget.

Nothing here may fail a run. A photo is a nicety; a check is the job.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

THUMB_DIR = Path("docs/thumbs")
INDEX = THUMB_DIR / "index.json"

# The published page is a git repository someone has to clone. A photo that
# does not fit in this is not worth the history it costs.
MAX_BYTES_EACH = 60_000
MAX_TOTAL_BYTES = 12_000_000
# How many new photos one check may fetch. A first run would otherwise pull
# two hundred images in one go, on top of its own requests.
MAX_PER_RUN = 24
# What an image is allowed to claim to be.
OK_TYPES = ("image/webp", "image/jpeg", "image/png", "image/avif")
EXT = {"image/webp": ".webp", "image/jpeg": ".jpg",
       "image/png": ".png", "image/avif": ".avif"}

_ID_OK = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


@dataclass
class Report:
    """What actually happened, in enough detail to be worth reading."""
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    pruned: int = 0
    bytes_added: int = 0
    total_bytes: int = 0
    kept: int = 0
    notes: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)

    def line(self) -> str:
        return (f"photos: {self.kept} kept ({self.total_bytes / 1e6:.1f} MB), "
                f"{self.fetched} new, {self.failed} failed, {self.pruned} pruned")


def _load_index() -> dict[str, Any]:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(index: dict[str, Any]) -> None:
    try:
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(index, indent=1, sort_keys=True),
                         encoding="utf-8")
    except OSError as exc:
        log.warning("could not write the photo index: %s", exc)


def _dimensions(blob: bytes) -> tuple[int, int] | None:
    """Width and height, read from the file's own header.

    Enough of each format to answer "is this the size it says it is", without
    an image library: the whole point of this module is that it adds no
    dependency to a job that runs every half hour.
    """
    try:
        if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
            chunk = blob[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(blob[24:27], "little") + 1
                h = int.from_bytes(blob[27:30], "little") + 1
                return w, h
            if chunk == b"VP8 ":
                return (int.from_bytes(blob[26:28], "little") & 0x3FFF,
                        int.from_bytes(blob[28:30], "little") & 0x3FFF)
            if chunk == b"VP8L":
                bits = int.from_bytes(blob[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            return (int.from_bytes(blob[16:20], "big"),
                    int.from_bytes(blob[20:24], "big"))
        if blob[:2] == b"\xff\xd8":
            i = 2
            while i < len(blob) - 9:
                if blob[i] != 0xFF:
                    i += 1
                    continue
                marker = blob[i + 1]
                # SOI, EOI, TEM and the restart markers carry no length. Adding
                # the two bytes after them as if they did reads the image data
                # as a segment size and jumps off the end of the file.
                if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if marker == 0xFF:          # fill byte
                    i += 1
                    continue
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return (int.from_bytes(blob[i + 7:i + 9], "big"),
                            int.from_bytes(blob[i + 5:i + 7], "big"))
                length = int.from_bytes(blob[i + 2:i + 4], "big")
                if length < 2:
                    return None
                i += 2 + length
    except (IndexError, ValueError):
        return None
    return None


def _safe_name(listing_id: str, content_type: str) -> str | None:
    """A filename that cannot escape the directory it belongs in."""
    if not _ID_OK.match(str(listing_id).replace("-", "")[:64]):
        return None
    return f"{listing_id}{EXT.get(content_type, '.img')}"


def _existing_bytes() -> int:
    if not THUMB_DIR.exists():
        return 0
    return sum(p.stat().st_size for p in THUMB_DIR.glob("*")
               if p.is_file() and p.name != INDEX.name)


def sync(entries: Iterable[dict[str, Any]], fetcher: Any,
         *, limit: int = MAX_PER_RUN, budget: int = MAX_TOTAL_BYTES,
         dry_run: bool = False) -> Report:
    """Fetch what is missing, drop what is no longer watched.

    ``fetcher`` is the bot's own rate-limited, retrying, budgeted HTTP client,
    so photos queue behind the same politeness the searches use rather than
    opening a second uncontrolled connection to somebody's CDN.
    """
    report = Report()
    entries = list(entries)
    index = _load_index()

    # Only cars you can actually see. A hidden car's photos are not published
    # in the data file either, so fetching them would be paying for nothing.
    wanted = {str(e["id"]): e for e in entries
              if e.get("status") == "active" and not e.get("filtered")
              and (e.get("images") or [])}

    # ---- prune first, so a delisted car's photo pays for a live one -------
    for listing_id in list(index):
        if listing_id in wanted:
            continue
        name = index[listing_id].get("file")
        if name:
            try:
                (THUMB_DIR / name).unlink(missing_ok=True)
                report.pruned += 1
            except OSError:
                pass
        del index[listing_id]

    total = _existing_bytes()
    for listing_id, entry in wanted.items():
        if listing_id in index and (THUMB_DIR / index[listing_id]["file"]).exists():
            continue
        if report.fetched >= limit:
            report.skipped += 1
            continue
        if total + MAX_BYTES_EACH > budget:
            report.notes.append(
                f"photo budget of {budget / 1e6:.0f} MB is full - "
                f"{report.skipped + 1} car(s) are showing the placeholder")
            report.skipped += 1
            continue

        url = (entry.get("images") or [None])[0]
        got = _fetch_one(url, fetcher)
        if got is None:
            report.failed += 1
            continue
        blob, content_type, note = got
        report.samples.append(note)
        if blob is None:
            report.failed += 1
            continue

        name = _safe_name(listing_id, content_type)
        if not name:
            report.failed += 1
            report.notes.append(f"{listing_id} is not a safe filename")
            continue
        if not dry_run:
            try:
                THUMB_DIR.mkdir(parents=True, exist_ok=True)
                (THUMB_DIR / name).write_bytes(blob)
            except OSError as exc:
                report.failed += 1
                report.notes.append(f"could not write {name}: {exc}")
                continue
        index[listing_id] = {"file": name, "bytes": len(blob),
                             "w": note.get("w"), "h": note.get("h")}
        report.fetched += 1
        report.bytes_added += len(blob)
        total += len(blob)

    report.kept = len(index)
    report.total_bytes = total
    if not dry_run:
        _save_index(index)
    return report


def _fetch_one(url: str | None, fetcher: Any
               ) -> tuple[bytes | None, str, dict[str, Any]] | None:
    """One photo, with everything worth reporting about it.

    Goes through the fetcher's binary path. The ordinary get() decodes to
    text, which turns a WebP into mojibake and loses the content-type, and
    that is exactly the mistake this made the first time it ran for real:
    sixty-six photos, none fetched, and the report said "not an image".
    """
    if not url:
        return None
    note: dict[str, Any] = {"url": url[:120]}

    if hasattr(fetcher, "get_asset"):
        # Wrapped, like the text path below already was. get_asset swallows
        # the HTTP errors it expects and returns them in the dict, but a CDN
        # can fail in ways no client turns into a return value - a reset
        # connection, a DNS failure, a socket timeout during TLS - and those
        # came straight back out through sync() and ended the run. This module
        # opens by saying a photo may never fail a check; for a whole class of
        # CDN failure that was not true, and no test looked.
        try:
            got = fetcher.get_asset(url)
        except Exception as exc:  # noqa: BLE001 - a photo may never fail a check
            note.update(status=None, error=f"{type(exc).__name__}: {exc}"[:120])
            return None, "", note
    else:
        # A stand-in in a test, or an older fetcher. Read whatever it gives.
        try:
            response = fetcher.get(url)
        except Exception as exc:  # noqa: BLE001 - a photo may never fail a check
            note.update(status=None, error=str(exc)[:120])
            return None, "", note
        headers = getattr(response, "headers", {}) or {}
        got = {
            "status": getattr(response, "status_code", None)
                      or getattr(response, "status", None),
            "type": str(headers.get("Content-Type")
                        or headers.get("content-type") or "").split(";")[0].strip(),
            "content": getattr(response, "content", b"") or b"",
        }
        if got["status"] and int(got["status"]) >= 400:
            got["error"] = f"HTTP {got['status']}"

    content_type = str(got.get("type") or "")
    blob = got.get("content") or b""
    note.update(status=got.get("status"), type=content_type, bytes=len(blob))

    if got.get("error"):
        note["error"] = got["error"]
        return None, content_type, note
    if content_type not in OK_TYPES:
        note["error"] = f"not an image ({content_type or 'no content-type'})"
        return None, content_type, note
    if len(blob) > MAX_BYTES_EACH:
        note["error"] = f"{len(blob)} bytes is over the {MAX_BYTES_EACH} cap"
        return None, content_type, note

    size = _dimensions(blob)
    if size:
        note["w"], note["h"] = size
    else:
        note["error"] = "header does not parse as an image"
        return None, content_type, note
    return blob, content_type, note


def local_for(listing_id: str, index: dict[str, Any] | None = None) -> str | None:
    """The published path for a car's photo, if we kept one."""
    index = _load_index() if index is None else index
    row = index.get(str(listing_id))
    return f"thumbs/{row['file']}" if row and row.get("file") else None
