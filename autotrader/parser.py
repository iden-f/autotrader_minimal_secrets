"""Turning an AutoTrader.ca search page into Listing records.

The old bot had exactly one way to read a page - ``a[href*='/a/']`` - so the
day AutoTrader changed its markup, the bot silently found zero cars and the run
failed.  This module tries several independent strategies and keeps whichever
found the most listings, then reports which one won so the dashboard can warn
when the reliable strategies stop working.
"""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
from typing import Any, Iterable

from bs4 import BeautifulSoup

from .listing import Listing
from .urls import canonical_listing_url, listing_id_from_url, location_from_url

log = logging.getLogger(__name__)

# A number is 1-3 digits then comma-separated groups of exactly three. The
# lookbehind stops a model designation running into the figure beside it:
# without it "M5 1,200 km" parsed as 51,200 km and "X5 500 km" as 5,500 km.
#
# Space is deliberately NOT accepted as a thousands separator. autotrader.ca
# writes "$132,500"; allowing "132 500" made "X5 500" ambiguous, and guessing
# wrong is worse than not guessing - card figures are provisional anyway, and
# the listing page's schema.org data corrects them.
_NUMBER = r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)"
PRICE_RE = re.compile(r"\$\s*" + _NUMBER + r"(?:\.\d{2})?")
KM_RE = re.compile(_NUMBER + r"\s*km\b", re.I)
YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-5]\d)\b")
LISTING_HREF_RE = re.compile(r"""["'(]((?:https?://[^"'()\s]*)?/a/[^"'()\s]*?/\d+_\d{5,}_[^"'()\s]*?/)""")

# JSON keys AutoTrader-ish payloads use for the same concepts.
_JSON_KEYS = {
    "price": ("price", "listingPrice", "askingPrice", "displayPrice", "priceValue"),
    "mileage": ("odometer", "mileage", "kilometres", "kilometers", "odometerValue", "km"),
    "title": ("title", "name", "displayName", "heading", "adTitle"),
    "year": ("year", "modelYear", "vehicleYear"),
    "make": ("make", "makeName", "brand"),
    "model": ("model", "modelName"),
    "trim": ("trim", "trimName", "series"),
    "location": ("location", "city", "proximityCity", "sellerCity"),
    "province": ("province", "provinceCode", "region"),
    "seller": ("dealerName", "sellerName", "companyName", "dealer"),
    "image": ("image", "imageUrl", "thumbnail", "photoUrl", "mainPhoto", "images"),
    "url": ("url", "link", "detailUrl", "vdpUrl", "href"),
    "id": ("id", "listingId", "adId", "vehicleId"),
}


# Phrases AutoTrader shows when a search legitimately matches nothing. Only
# consulted once zero listings were parsed, so an ad containing "0 results"
# cannot mislead us.
NO_RESULTS_MARKERS = (
    "0 results", "no results", "returned 0", "did not match", "no matches",
    "no vehicles", "no listings", "aucun r", "0 r\u00e9sultat",
    "try broadening", "try widening", "widen your search", "broaden your search",
    "sorry, we couldn't find", "we could not find any",
)


def looks_like_no_results(html: str) -> bool:
    """True if the page says, in so many words, that the search matched nothing.

    This is what separates "your filters are too narrow" from "the parser has
    fallen behind the site" - both of which otherwise look like zero listings.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", (html or "")[:200000],
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text).lower()
    return any(marker in text for marker in NO_RESULTS_MARKERS)


class ParseResult:
    """Listings plus a note about how they were found."""

    def __init__(self, listings: list[Listing], strategy: str,
                 candidates: dict[str, int] | None = None) -> None:
        self.listings = listings
        self.strategy = strategy
        self.candidates = candidates or {}

    def __len__(self) -> int:
        return len(self.listings)


# ---------------------------------------------------------------- helpers


def _to_int(value: Any) -> int | None:
    """Parse '102,199', '$102 199', 102199.0 -> 102199.  Returns None on junk."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = re.sub(r"[^\d.]", "", str(value))
    if not text:
        return None
    try:
        number = int(float(text))
    except ValueError:
        return None
    return number if number > 0 else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(str(text or ""))).strip()


def _titlecase_place(text: str) -> str:
    """Dealer addresses arrive shouted ("MONTREAL", "TORONTO")."""
    text = _clean(text)
    if text and text == text.upper():
        return "-".join(part.capitalize() for part in text.split("-"))
    return text


def _first_key(node: dict, names: Iterable[str]) -> Any:
    for name in names:
        for key in (name, name[0].upper() + name[1:]):
            if key in node and node[key] not in (None, "", []):
                return node[key]
    return None


def _price_from_text(text: str) -> int | None:
    """Largest dollar figure in a card - AutoTrader shows the asking price
    biggest, but cards can also carry small figures like '$0 down'."""
    best: int | None = None
    for match in PRICE_RE.finditer(text):
        value = _to_int(match.group(1))
        if value and 500 <= value <= 5_000_000 and (best is None or value > best):
            best = value
    return best


def _mileage_from_text(text: str) -> int | None:
    """Odometer reading from card text.

    A card can show two ``km`` figures: how far the car is from you, then the
    odometer.  In every real card we have, the odometer is the last one, so we
    take that and let detail-page enrichment correct it when it runs.
    """
    values = [v for v in (_to_int(m.group(1)) for m in KM_RE.finditer(text))
              if v is not None and v <= 2_000_000]
    return values[-1] if values else None


def _title_from_text(text: str) -> str:
    """Trim card noise down to something that reads like a car name."""
    text = _clean(text)
    # Cards start with the photo count, e.g. "14 Trois-Rivieres - 3,726 km ...".
    match = YEAR_RE.search(text)
    if match:
        text = text[match.start():]
    return text[:120].strip(" -|,")


def _images_from(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out = [value]
    elif isinstance(value, list):
        out = [v for v in value if isinstance(v, str)]
    elif isinstance(value, dict):
        for key in ("url", "src", "contentUrl", "large", "medium"):
            if isinstance(value.get(key), str):
                out = [value[key]]
                break
    return [u for u in out if u.startswith("http")][:12]


def _types_of(node: dict) -> set[str]:
    """schema.org @type, which may be a string or a list of strings."""
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {str(t).lower() for t in raw}
    return set()


def _iter_json_objects(node: Any, depth: int = 0) -> Iterable[dict]:
    if depth > 12:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_json_objects(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_json_objects(value, depth + 1)


def _listing_from_json(node: dict, base_url: str) -> Listing | None:
    """Build a Listing from an arbitrary JSON object, if it looks like one."""
    # The link can live in several places depending on the vintage of the
    # markup: a plain "url", the node's "@id" (with a #vehicle fragment), or
    # inside the offer. Try all of them before giving up.
    candidates: list[Any] = [_first_key(node, _JSON_KEYS["url"]), node.get("@id")]
    offer = node.get("offers")
    if isinstance(offer, dict):
        candidates.append(offer.get("url"))
    elif isinstance(offer, list) and offer and isinstance(offer[0], dict):
        candidates.append(offer[0].get("url"))

    url = None
    lid = None
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("url") or candidate.get("href")
        if not isinstance(candidate, str) or not candidate:
            continue
        cleaned = candidate.split("#", 1)[0]
        found_id = listing_id_from_url(cleaned)
        if found_id:
            url, lid = cleaned, found_id
            break
        if url is None:
            url = cleaned

    if not lid:
        raw_id = _first_key(node, _JSON_KEYS["id"])
        if raw_id is not None and re.fullmatch(r"\d{5,}", str(raw_id)):
            lid = str(raw_id)
    if not lid or not isinstance(url, str):
        return None

    offers = node.get("offers")
    price = None
    currency = "CAD"
    seller_name = ""
    city = ""
    region = ""
    if isinstance(offers, dict):
        price = _to_int(offers.get("price"))
        currency = offers.get("priceCurrency") or "CAD"
        seller = offers.get("seller")
        if isinstance(seller, dict):
            seller_name = str(seller.get("name") or "")
            address = seller.get("address")
            if isinstance(address, dict):
                city = str(address.get("addressLocality") or "")
                region = str(address.get("addressRegion") or "")
    if price is None:
        price = _to_int(_first_key(node, _JSON_KEYS["price"]))

    odo = node.get("mileageFromOdometer")
    mileage = _to_int(odo.get("value")) if isinstance(odo, dict) else _to_int(odo)
    if mileage is None:
        mileage = _to_int(_first_key(node, _JSON_KEYS["mileage"]))

    make = _first_key(node, _JSON_KEYS["make"])
    if isinstance(make, dict):
        make = make.get("name")

    return Listing(
        id=lid,
        url=canonical_listing_url(url),
        title=_clean(_first_key(node, _JSON_KEYS["title"]) or ""),
        year=_to_int(node.get("vehicleModelDate")) or _to_int(_first_key(node, _JSON_KEYS["year"])),
        make=_clean(make or ""),
        model=_clean(_first_key(node, _JSON_KEYS["model"]) or ""),
        trim=_clean(node.get("vehicleConfiguration") or _first_key(node, _JSON_KEYS["trim"]) or ""),
        price=price,
        price_source="search" if price is not None else "",
        card_price=price,
        currency=currency if isinstance(currency, str) else "CAD",
        mileage_km=mileage,
        location=_titlecase_place(city) or _clean(_first_key(node, _JSON_KEYS["location"]) or ""),
        province=_clean(region) or _clean(_first_key(node, _JSON_KEYS["province"]) or ""),
        seller=_clean(seller_name) or _clean(_first_key(node, _JSON_KEYS["seller"]) or ""),
        body=_clean(node.get("bodyType") or node.get("body") or ""),
        color=_clean(node.get("color") or ""),
        transmission=_clean(node.get("vehicleTransmission") or ""),
        fuel=_clean(node.get("fuelType") or ""),
        images=_images_from(_first_key(node, _JSON_KEYS["image"])),
    )


# ---------------------------------------------------------------- strategies


def _strategy_jsonld(soup: BeautifulSoup, html: str, base_url: str) -> list[Listing]:
    """schema.org markup.  The most stable thing on the page when present."""
    found: dict[str, Listing] = {}
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _iter_json_objects(data):
            # Since the 2026 platform change @type is often a list, e.g.
            # ["Car","Product"]. str() on that yields "['car', 'product']",
            # which matched nothing and silently zeroed this whole strategy.
            types = _types_of(node)
            if not types & {"vehicle", "car", "product", "offer", "listitem", "itemlist"}:
                continue
            target = node.get("item") if "listitem" in types else node
            if not isinstance(target, dict):
                continue
            listing = _listing_from_json(target, base_url)
            if listing:
                found.setdefault(listing.id, listing)
    return list(found.values())


def _strategy_embedded_json(soup: BeautifulSoup, html: str, base_url: str) -> list[Listing]:
    """Front-end state blobs (__NEXT_DATA__, window.__INITIAL_STATE__, ...)."""
    found: dict[str, Listing] = {}
    blobs: list[str] = []

    for tag in soup.find_all("script", attrs={"id": re.compile("NEXT_DATA|__STATE__", re.I)}):
        blobs.append(tag.string or tag.get_text() or "")
    for match in re.finditer(
        r"(?:window\.)?(?:__INITIAL_STATE__|__NUXT__|__APOLLO_STATE__|__PRELOADED_STATE__|"
        r"searchResults|listingsData)\s*=\s*(\{.*?\})\s*[;<]",
        html, re.S,
    ):
        blobs.append(match.group(1))

    for blob in blobs:
        blob = blob.strip()
        if not blob.startswith(("{", "[")):
            continue
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _iter_json_objects(data):
            listing = _listing_from_json(node, base_url)
            if listing:
                found.setdefault(listing.id, listing)
    return list(found.values())


def _listing_ids_under(node) -> set[str]:
    ids = set()
    for anchor in node.find_all("a", href=True):
        href = anchor["href"]
        if "/a/" not in href:
            continue
        lid = listing_id_from_url(href)
        if lid:
            ids.add(lid)
    return ids


def _card_for(anchor) -> Any:
    """The card element wrapping a listing link.

    Climb as far as possible while the ancestor still covers exactly one
    listing.  Text length is a bad boundary (a terse card and a whole results
    container can be the same size); "does this ancestor swallow a second car"
    is precise and does not depend on class names.
    """
    node = anchor
    for _ in range(8):
        parent = getattr(node, "parent", None)
        if parent is None or getattr(parent, "name", None) in (None, "body", "html", "[document]"):
            break
        if len(_listing_ids_under(parent)) > 1:
            break
        node = parent
    return node


def _strategy_anchors(soup: BeautifulSoup, html: str, base_url: str) -> list[Listing]:
    """Listing links plus whatever the surrounding card says.

    This is the strategy the original bot used; it is kept because it is proven
    to work against the real site, but it is now the third choice because card
    markup is the part most likely to change.
    """
    found: dict[str, Listing] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/a/" not in href:
            continue
        url = href if href.startswith("http") else _join(base_url, href)
        lid = listing_id_from_url(url)
        if not lid or lid in found:
            continue
        card = _card_for(anchor)
        card_text = _clean(card.get_text(" ", strip=True)) if card else ""
        heading = card.find(["h1", "h2", "h3", "h4"]) if hasattr(card, "find") else None
        title = _clean(anchor.get("title") or "")
        if not title and heading is not None:
            title = _clean(heading.get_text(" ", strip=True))
        if not title:
            title = _title_from_text(_clean(anchor.get_text(" ", strip=True)) or card_text)

        image = ""
        if hasattr(card, "find"):
            for img in card.find_all("img"):
                src = (img.get("src") or img.get("data-src") or
                       img.get("data-original") or "")
                # Real car photos live on the vehicle-image CDN; the static CDN
                # only serves manufacturer logos and UI chrome.
                if "vehicleimages" in src or "/vehicles/" in src:
                    image = src
                    break

        found[lid] = Listing(
            id=lid,
            url=canonical_listing_url(url),
            title=title or f"AutoTrader listing {lid}",
            price=_price_from_text(card_text),
            price_source="search" if _price_from_text(card_text) is not None else "",
            card_price=_price_from_text(card_text),
            mileage_km=_mileage_from_text(card_text),
            images=[image] if image.startswith("http") else [],
        )
    return list(found.values())


def _strategy_regex(soup: BeautifulSoup, html: str, base_url: str) -> list[Listing]:
    """Last resort: pull listing URLs straight out of the raw HTML.

    This works even if the page is rendered entirely by JavaScript, as long as
    the links appear somewhere in the payload.
    """
    found: dict[str, Listing] = {}
    for match in LISTING_HREF_RE.finditer(html):
        href = htmllib.unescape(match.group(1))
        url = href if href.startswith("http") else _join(base_url, href)
        lid = listing_id_from_url(url)
        if lid and lid not in found:
            found[lid] = Listing(id=lid, url=canonical_listing_url(url),
                                 title=f"AutoTrader listing {lid}")
    return list(found.values())


def _join(base: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base or "https://www.autotrader.ca/", href)


# Order matters: earlier strategies produce richer, more stable records, so a
# tie is broken in their favour.
STRATEGIES = (
    ("jsonld", _strategy_jsonld),
    ("embedded_json", _strategy_embedded_json),
    ("anchors", _strategy_anchors),
    ("regex", _strategy_regex),
)


def parse_search_page(html: str, base_url: str = "https://www.autotrader.ca/") -> ParseResult:
    """Extract listings from a search results page using every strategy."""
    soup = BeautifulSoup(html or "", "html.parser")
    results: dict[str, list[Listing]] = {}
    counts: dict[str, int] = {}

    for name, strategy in STRATEGIES:
        try:
            listings = strategy(soup, html or "", base_url)
        except Exception as exc:  # noqa: BLE001 - one broken strategy must not
            log.warning("parser strategy %s failed: %s", name, exc)  # kill the run
            listings = []
        results[name] = listings
        counts[name] = len(listings)

    best_name = max(STRATEGIES, key=lambda s: counts.get(s[0], 0))[0]
    best = results.get(best_name, [])
    if not best:
        return ParseResult([], "none", counts)

    # Merge the runners-up in: a weaker strategy may still know a price the
    # winner missed, and it costs nothing to fold that in.
    merged: dict[str, Listing] = {}
    for listing in best:
        listing.source = best_name
        if not listing.location:
            listing.location, listing.province = location_from_url(listing.url)
        merged[listing.id] = listing
    for name, _ in STRATEGIES:
        if name == best_name:
            continue
        for listing in results.get(name, []):
            if listing.id in merged:
                merged[listing.id].merge(listing)
    return ParseResult(list(merged.values()), best_name, counts)
