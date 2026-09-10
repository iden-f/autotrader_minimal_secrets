"""Parsing and normalising autotrader.ca URLs.

The whole point of this project is that the user configures a watch by
*pasting a search link*.  Everything we need to know about what they want to
watch is already encoded in that URL, so this module's job is to:

* recognise a pasted link and tell the user what it means in plain English,
* pull the canonical listing id out of a listing link,
* build page-2, page-3, ... variants of a search link so we can paginate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

# autotrader.ca and its French sister site share the same markup and ids.
KNOWN_HOSTS = {"www.autotrader.ca", "autotrader.ca", "www.autohebdo.net", "autohebdo.net"}

# Listing detail links look like:
#   /a/bmw/m5/winnipeg/manitoba/19_13166607_/
#   /a/bmw/m5/st.%20catharines/ontario/5_68819631_on20080114122939242/
# The middle group is the stable listing id; the first is a dealer/source code
# and the third an opaque reference that changes between renders.
LISTING_PATH_RE = re.compile(r"/a/(?:[^/]+/)*?(\d+)_(\d{5,})_([^/]*)/?", re.I)

# In 2026 autotrader.ca moved onto the AutoScout24 platform and listing URLs
# became /offers/<descriptive-slug>-<uuid>. The trailing UUID is the listing's
# identity; everything before it is SEO text that changes when the seller edits
# the ad, and the "cat_..." segment in the middle is a search token shared by
# every result on a page.
OFFER_PATH_RE = re.compile(
    r"/offers?/[^/?#]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I)

# Fallback for links we cannot fully structure but that still carry an id.
LOOSE_ID_RE = re.compile(r"[_/-](\d{5,})(?:[_/?#]|$)")

_TRUE = {"1", "true", "yes", "on"}


def is_autotrader_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in KNOWN_HOSTS


def is_listing_url(url: str) -> bool:
    return bool(listing_id_from_url(url))


def listing_id_from_url(url: str) -> str | None:
    """Return the canonical numeric listing id, or None.

    Only the path is considered: query strings on autotrader.ca carry tracking
    numbers (``urp=3``, ``sprx=-2``) that a looser regex would happily mistake
    for an id.
    """
    if not url:
        return None
    try:
        path = urlparse(url).path
    except ValueError:
        return None
    m = LISTING_PATH_RE.search(path)
    if m:
        return m.group(2)
    m = OFFER_PATH_RE.search(path)
    if m:
        return m.group(1).lower()
    m = LOOSE_ID_RE.search(path)
    return m.group(1) if m else None


def _place(slug: str) -> str:
    """Title-case a place name, keeping hyphens (Trois-Rivieres, Saint-Hubert)."""
    text = unquote(slug).replace("+", " ")
    return "-".join(_titlecase(part) for part in text.split("-") if part)


def is_offer_url(url: str) -> bool:
    """True for the post-2026 /offers/<slug>-<uuid> listing links."""
    try:
        return bool(OFFER_PATH_RE.search(urlparse(url).path))
    except ValueError:
        return False


def location_from_url(url: str) -> tuple[str, str]:
    """Pull ``(city, province)`` out of a listing path.

    Listing URLs are ``/a/<make>/<model>/<city>/<province>/<ids>/`` and the
    schema.org block on the page does not carry a location, so this is the only
    place the city is available without extra parsing.
    """
    try:
        path = urlparse(url).path
    except ValueError:
        return "", ""
    segments = [s for s in path.split("/") if s]
    if not segments or segments[0].lower() != "a":
        # /offers/<slug>-<uuid> has no location in the path; the page's
        # structured data carries the seller's address instead.
        return "", ""
    # The id segment looks like "19_13166607_"; city and province are the two
    # segments immediately before it.
    for index, segment in enumerate(segments):
        if re.fullmatch(r"\d+_\d{5,}_.*", segment):
            if index < 3:
                return "", ""
            return (_place(segments[index - 2]), _place(segments[index - 1]))
    return "", ""


def canonical_listing_url(url: str) -> str:
    """Strip tracking query parameters so the same car always has one URL."""
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    host = (parts.hostname or "www.autotrader.ca").lower()
    if host in {"autotrader.ca", "www.autohebdo.net", "autohebdo.net"}:
        host = "www.autotrader.ca"
    # Some pages emit hrefs with a literal space in the city segment.
    path = quote(unquote(parts.path), safe="/%")
    return urlunparse(("https", host, path, "", "", ""))


def normalise_search_url(url: str) -> str:
    """Clean a pasted search URL without changing what it searches for."""
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if host in {"autotrader.ca"}:
        host = "www.autotrader.ca"
    path = parts.path or "/"
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    # Drop analytics-only parameters so two pastes of the same search match.
    drop = {"gclid", "fbclid", "msclkid", "utm_source", "utm_medium",
            "utm_campaign", "utm_term", "utm_content", "ursrc", "urp", "urm"}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in drop]
    return urlunparse(("https", host, path, "", urlencode(query), ""))


def page_url(url: str, page: int, per_page: int | None = None) -> str:
    """Return ``url`` for a 1-indexed results page.

    Two schemes are written at once because the site changed under us and both
    are cheap to carry:

    * the original autotrader.ca paginated with ``rcs`` (the zero-based index
      of the first result) and ``rcp`` (results per page);
    * the AutoScout24 platform it moved to in 2026 paginates with ``page``,
      fixes the page size at 20, and ignores ``rcs`` entirely.

    Sending only ``rcs`` meant every page after the first came back identical
    to page 1, the scraper saw nothing new and stopped - so a 186-result search
    was only ever read 20 cars deep. The unrecognised parameter is ignored by
    whichever platform is actually serving, so writing both is safe.
    """
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if per_page:
        query["rcp"] = str(per_page)
    rcp = _as_int(query.get("rcp")) or per_page or 100
    query["rcp"] = str(rcp)
    query["rcs"] = str(max(0, (page - 1) * rcp))
    if page > 1:
        query["page"] = str(page)
    else:
        query.pop("page", None)
    return urlunparse((parts.scheme or "https", parts.netloc, parts.path, "",
                       urlencode(query), ""))


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _range(value: str | None) -> tuple[int | None, int | None]:
    """Parse autotrader's ``low,high`` range syntax (either side may be blank)."""
    if not value:
        return None, None
    raw = unquote(value)
    low, _, high = raw.partition(",")
    return _as_int(low), _as_int(high)


@dataclass
class SearchSummary:
    """A plain-English reading of a pasted search link, for the UI."""

    make: str | None = None
    model: str | None = None
    year_min: int | None = None
    year_max: int | None = None
    price_min: int | None = None
    price_max: int | None = None
    mileage_max: int | None = None
    location: str | None = None
    radius_km: int | None = None   # None means "no distance limit"
    province: str | None = None
    body: str | None = None
    condition: str | None = None
    keywords: str | None = None
    per_page: int | None = None
    valid: bool = True
    problems: list[str] = field(default_factory=list)

    def title(self) -> str:
        """A short default name for the watch, e.g. '2021+ BMW M5'."""
        bits: list[str] = []
        if self.year_min and self.year_max:
            bits.append(f"{self.year_min}-{self.year_max}")
        elif self.year_min:
            bits.append(f"{self.year_min}+")
        elif self.year_max:
            bits.append(f"up to {self.year_max}")
        if self.make:
            bits.append(self.make)
        if self.model:
            bits.append(self.model)
        if not bits:
            bits.append("AutoTrader search")
        return " ".join(bits)

    def describe(self) -> list[str]:
        """Human-readable chips describing the search, for the settings UI."""
        out: list[str] = []
        if self.price_min and self.price_max:
            out.append(f"${self.price_min:,}-${self.price_max:,}")
        elif self.price_max:
            out.append(f"under ${self.price_max:,}")
        elif self.price_min:
            out.append(f"over ${self.price_min:,}")
        if self.mileage_max:
            out.append(f"under {self.mileage_max:,} km")
        if self.body:
            out.append(self.body)
        if self.condition:
            out.append(self.condition)
        if self.location:
            out.append(f"near {self.location}"
                       + (f" ({self.radius_km} km)" if self.radius_km else ""))
        elif self.province:
            out.append(self.province)
        elif self.radius_km is None:
            out.append("Canada-wide")
        if self.keywords:
            out.append(f'"{self.keywords}"')
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "make": self.make, "model": self.model,
            "year_min": self.year_min, "year_max": self.year_max,
            "price_min": self.price_min, "price_max": self.price_max,
            "mileage_max": self.mileage_max, "location": self.location,
            "radius_km": self.radius_km, "province": self.province,
            "body": self.body, "condition": self.condition,
            "keywords": self.keywords, "per_page": self.per_page,
            "valid": self.valid, "problems": self.problems,
            "title": self.title(), "chips": self.describe(),
        }


# Makes and trims that are acronyms, so ".title()" would mangle them.
_ACRONYMS = {"bmw", "gmc", "ram", "mg", "vw", "amg", "srt", "gti", "sti",
             "gt", "rs", "ev", "suv", "gls", "glc", "gle", "cx"}


def _titlecase(slug: str) -> str:
    words = [w for w in re.split(r"[-_+]", unquote(slug)) if w]
    out = []
    for w in words:
        low = w.lower()
        # Model designations like "m5", "xc90", "f-150" read better upper-cased.
        if low in _ACRONYMS or (len(w) <= 3 and any(c.isdigit() for c in w)):
            out.append(w.upper())
        else:
            out.append(w.title())
    return " ".join(out)


def describe_search(url: str) -> SearchSummary:
    """Read a pasted search URL and explain what it will watch."""
    s = SearchSummary()
    url = normalise_search_url(url)
    if not url:
        s.valid = False
        s.problems.append("Paste an AutoTrader search link first.")
        return s
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host not in KNOWN_HOSTS:
        s.valid = False
        s.problems.append(
            f"{host or 'That link'} is not autotrader.ca - paste a link from "
            "autotrader.ca (or autohebdo.net)."
        )
        return s
    if is_listing_url(url) or is_offer_url(url):
        s.valid = False
        s.problems.append(
            "That is a single car listing, not a search. Run the search on "
            "autotrader.ca first, then copy the address bar."
        )
        return s

    segments = [p for p in parts.path.split("/") if p]
    q = dict(parse_qsl(parts.query, keep_blank_values=True))

    # Path shape: /cars/<make>/<model>/<province>/<city>/
    if segments and segments[0].lower() in {"cars", "autos"}:
        rest = segments[1:]
        provinces = {"ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu",
                     "on", "pe", "qc", "sk", "yt"}
        if rest and rest[0].lower() not in provinces:
            s.make = _titlecase(rest[0])
            rest = rest[1:]
            if rest and rest[0].lower() not in provinces:
                s.model = _titlecase(rest[0])
                rest = rest[1:]
        if rest and rest[0].lower() in provinces:
            s.province = rest[0].upper()
    elif not segments:
        s.problems.append(
            "That looks like the autotrader.ca home page. Run a search first, "
            "then copy the address bar."
        )
        s.valid = False
        return s

    s.year_min, s.year_max = _range(q.get("yRng"))
    s.price_min, s.price_max = _range(q.get("pRng"))
    _, s.mileage_max = _range(q.get("odRng"))
    s.location = unquote(q["loc"]).replace("+", " ").strip() if q.get("loc") else None
    s.province = s.province or (unquote(q["prv"]).replace("+", " ") if q.get("prv") else None)
    s.body = unquote(q["body"]).replace("+", " ") if q.get("body") else None
    s.keywords = unquote(q["kwd"]).replace("+", " ") if q.get("kwd") else None
    s.per_page = _as_int(q.get("rcp"))

    prx = _as_int(q.get("prx"))
    # Negative proximity means "no radius limit" (province-wide or national).
    s.radius_km = prx if prx is not None and prx > 0 else None

    sts = unquote(q.get("sts", "")).strip()
    if sts and sts.lower() not in {"new-used", "used-new"}:
        s.condition = sts.replace("-", " & ")

    if not q:
        s.problems.append(
            "This link has no search filters on it, so it will match a very "
            "broad set of listings."
        )
    return s
