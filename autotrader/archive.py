"""Optional archiving of listings, with a retention policy.

v1 archived every listing as ``page.html`` (about 197 KB each) plus every
``<img src>`` on the page, and committed all of it.  Fifty cars cost 9.6 MB,
and none of the 400 saved "photos" were of a car - they were the manufacturer
logo strip in the page footer.

The default here is ``metadata``: a small JSON file per car with the real facts
and the real photo URLs.  ``full`` (HTML) and image downloads are opt-in, and
everything is pruned on a schedule so the repository cannot grow without limit.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .listing import Listing

log = logging.getLogger(__name__)

ARCHIVE_DIR = Path("archives")
VALID_MODES = ("off", "metadata", "full")


def archive_listing(listing: Listing, config: dict[str, Any], fetcher=None,
                    html: str | None = None, root: Path = ARCHIVE_DIR) -> Path | None:
    """Write an archive entry for ``listing``.  Never raises."""
    mode = str(config.get("mode", "metadata")).lower()
    if mode not in VALID_MODES or mode == "off":
        return None
    try:
        folder = root / listing.id
        folder.mkdir(parents=True, exist_ok=True)

        payload = listing.to_dict()
        payload["archived_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (folder / "metadata.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        if mode == "full" and html:
            (folder / "page.html").write_text(html, encoding="utf-8")

        wanted = int(config.get("images", 0) or 0)
        if wanted > 0 and fetcher is not None and listing.images:
            for index, url in enumerate(listing.images[:wanted], start=1):
                # One unreachable photo must not throw away the metadata we
                # have already written for this car.
                try:
                    blob = fetcher.get_bytes(url, referer=listing.url)
                except Exception as exc:  # noqa: BLE001
                    log.debug("photo %s failed for %s: %s", index, listing.id, exc)
                    continue
                if not blob:
                    continue
                suffix = ".jpg"
                for candidate in (".jpg", ".jpeg", ".png", ".webp", ".avif"):
                    if candidate in url.lower():
                        suffix = candidate
                        break
                (folder / f"photo_{index}{suffix}").write_bytes(blob)
        return folder
    except Exception as exc:  # noqa: BLE001 - archiving is a nicety, never fatal
        log.warning("could not archive %s: %s", listing.id, exc)
        return None


def prune(config: dict[str, Any], root: Path = ARCHIVE_DIR,
          *, dry_run: bool = False) -> list[str]:
    """Delete archive folders past the retention policy.  Returns their ids."""
    keep_last = int(config.get("keep_last", 400) or 0)
    keep_days = int(config.get("keep_days", 730) or 0)
    if not root.exists() or (keep_last <= 0 and keep_days <= 0):
        return []

    folders = [p for p in root.iterdir() if p.is_dir()]

    def when(folder: Path) -> str:
        meta = folder / "metadata.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                stamp = data.get("archived_at") or data.get("saved") or ""
                if stamp:
                    return str(stamp)
            except (json.JSONDecodeError, OSError):
                pass
        return datetime.fromtimestamp(folder.stat().st_mtime,
                                      timezone.utc).isoformat(timespec="seconds")

    dated = sorted(((when(f), f) for f in folders), reverse=True)
    doomed: list[Path] = []

    if keep_last > 0 and len(dated) > keep_last:
        doomed.extend(f for _, f in dated[keep_last:])
    if keep_days > 0:
        # "saved" timestamps from v1 are naive local time; compare on the date
        # prefix only so both formats sort correctly against the cutoff.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        doomed.extend(f for stamp, f in dated if stamp[:10] < cutoff and f not in doomed)

    removed: list[str] = []
    for folder in doomed:
        removed.append(folder.name)
        if not dry_run:
            shutil.rmtree(folder, ignore_errors=True)
    return removed


def compact(root: Path = ARCHIVE_DIR, *, dry_run: bool = False,
             keep_images: int = 0) -> dict[str, Any]:
    """Rewrite v1 archive folders into the metadata-only shape.

    v1 folders hold a ~197 KB ``page.html`` and eight ``image_N.jpg`` files.
    The images are the same eight pieces of page furniture in all fifty
    folders - the manufacturer logo strip, not the car. The HTML is worth
    something, but only for the schema.org data inside it, and once that has
    been read out into ``metadata.json`` there is no reason to keep 197 KB of
    markup for a car that sold last year.

    So: read each page one final time, write everything it knows into the v2
    metadata file, and only then delete it. Nothing is discarded before it has
    been extracted, and re-running ``migrate`` afterwards rebuilds the same
    listings from the metadata instead of the HTML.
    """
    from .migrate import read_archive     # local: migrate imports this module

    result: dict[str, Any] = {"folders": 0, "upgraded": 0, "failed": [],
                              "bytes_freed": 0, "removed": []}
    if not root.exists():
        return result

    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        doomed = [p for p in folder.glob("page.html")]
        doomed += sorted(folder.glob("image_*"))[keep_images:]
        if not doomed:
            continue
        result["folders"] += 1

        try:
            listing, saved_at = read_archive(folder)
        except Exception as exc:  # noqa: BLE001 - a bad archive is not fatal
            log.warning("could not read %s: %s", folder, exc)
            listing, saved_at = None, None
        if listing is None:
            # Nothing could be extracted, so there is nothing safe to delete.
            result["failed"].append(folder.name)
            continue

        payload = listing.to_dict()
        payload["archived_at"] = saved_at or datetime.now(
            timezone.utc).isoformat(timespec="seconds")
        payload["compacted_from"] = "v1-archive"
        if not dry_run:
            try:
                (folder / "metadata.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
            except OSError as exc:
                log.warning("could not rewrite %s: %s", folder, exc)
                result["failed"].append(folder.name)
                continue
        result["upgraded"] += 1

        for item in doomed:
            try:
                result["bytes_freed"] += item.stat().st_size
            except OSError:
                continue
            result["removed"].append(str(item))
            if not dry_run:
                item.unlink(missing_ok=True)
    return result


def size_report(root: Path = ARCHIVE_DIR) -> dict[str, Any]:
    """What the archive currently costs, for the dashboard's Status tab."""
    if not root.exists():
        return {"folders": 0, "bytes": 0, "html_bytes": 0, "image_bytes": 0}
    total = html = images = 0
    folders = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        folders += 1
        for item in folder.rglob("*"):
            if not item.is_file():
                continue
            size = item.stat().st_size
            total += size
            if item.suffix == ".html":
                html += size
            elif item.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".avif"}:
                images += size
    return {"folders": folders, "bytes": total, "html_bytes": html, "image_bytes": images}
