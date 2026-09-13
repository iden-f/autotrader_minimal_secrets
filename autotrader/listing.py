"""The Listing record shared by the scraper, the notifier and the dashboard."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-5]\d)\b")

_EDGE_SEPARATOR = re.compile(r"^[\s|,/·\-]+|[\s|,/·\-]+$")

# What the parser writes when a results card carries no readable name.
PLACEHOLDER_TITLE = "AutoTrader listing "


@dataclass
class Listing:
    id: str
    # Defaulted so a state entry that lost a field - hand-edited, or written by
    # an interrupted upgrade - still loads instead of raising.
    url: str = ""
    title: str = ""
    year: int | None = None
    make: str = ""
    model: str = ""
    trim: str = ""
    price: int | None = None
    # Where the price came from: "search" (a results card) or "detail" (the
    # listing page's JSON-LD). Card and detail prices routinely disagree, so
    # they must never be compared with each other - see State.record.
    price_source: str = ""
    # The figure the results card showed, kept even after the detail page
    # overwrites `price`, so we can tell a real change from a permanent
    # card/detail disagreement without re-fetching the car every run.
    card_price: int | None = None
    currency: str = "CAD"
    mileage_km: int | None = None
    location: str = ""
    province: str = ""
    seller: str = ""
    seller_type: str = ""
    body: str = ""
    color: str = ""
    transmission: str = ""
    drivetrain: str = ""
    fuel: str = ""
    engine: str = ""
    vin: str = ""
    images: list[str] = field(default_factory=list)
    # Your mark on this car, carried onto the Listing so the notifier can see
    # it without reaching back into state.
    shortlisted: bool = False
    search_id: str = ""
    search_name: str = ""
    source: str = ""          # which parser strategy produced this
    enriched: bool = False    # True once detail-page JSON-LD has been merged

    def __post_init__(self) -> None:
        if self.year is None and self.title:
            m = _YEAR_RE.search(self.title)
            if m:
                self.year = int(m.group(1))

    @property
    def short_trim(self) -> str:
        """The first meaningful part of a trim.

        Dealers on the current platform stuff the trim field with a pipe- or
        comma-separated feature list ("Competition | Premium | Aide a la
        conduite avance"), which is unreadable in a notification.
        """
        trim = (self.trim or "").strip()
        if not trim:
            return ""
        # A trim can arrive already separated from a name that was stripped
        # off it, so it starts on the separator: "I Premium PKG I M Carbon".
        trim = _EDGE_SEPARATOR.sub("", trim).strip()
        if trim[:2].upper() == "I " and trim[2:3].isupper():
            trim = trim[2:].strip()
        # " I " is in there because dealers on this platform type a capital I
        # where they mean a pipe: "M4 I Premium PKG I M Carbon Exterior PKG".
        for separator in ("|", ",", "/", " I "):
            if separator in trim:
                trim = trim.split(separator)[0].strip()
                break
        # Dealers often repeat the model inside the trim, which reads as
        # "BMW M5 M5 Competition" once the name is assembled.
        for prefix in (self.model, self.make):
            if not prefix:
                continue
            if trim.lower() == prefix.lower():
                # The trim IS the model - a real row: make BMW, model X3,
                # trim X3, which named the car "2010 BMW X3 X3".
                return ""
            if trim.lower().startswith(prefix.lower() + " "):
                trim = trim[len(prefix):].strip()
        if len(trim) <= 40:
            return trim.strip()
        # Cut at a word, not mid-word: "M Carbon Exterior PKG Ca" was a real
        # card title, and the two dangling letters read as a rendering fault.
        cut = trim[:40]
        head, space, _ = cut.rpartition(" ")
        return (head if space and len(head) >= 12 else cut).strip()

    @property
    def display_title(self) -> str:
        """What to call this car. One definition, shared with ``name_of``.

        These were two rules for a while and they disagreed: a car titled
        "BMW M4 Competition | One owner" was "BMW M4 Competition" on the page
        and "2019 BMW M4" in the notification about it, because the page read
        the stored title and this composed a fresh one and threw the dealer's
        away. Nobody compares a push notification against a web page, so the
        fork was invisible until a test put them side by side.
        """
        return name_of(self.to_dict())

    @property
    def composed_title(self) -> str:
        """'YEAR MAKE MODEL TRIM' out of the fields, ignoring any title."""
        parts = [str(self.year) if self.year else "", self.make, self.model,
                 self.short_trim]
        return " ".join(p for p in parts if p).strip()

    @property
    def price_text(self) -> str:
        if self.price is None:
            return "Price not listed"
        return f"${self.price:,}"

    @property
    def mileage_text(self) -> str:
        if self.mileage_km is None:
            return "km not listed"
        return f"{self.mileage_km:,} km"

    @property
    def thumbnail(self) -> str:
        return self.images[0] if self.images else ""

    def summary_line(self) -> str:
        bits = [self.display_title, self.price_text]
        if self.mileage_km is not None:
            bits.append(self.mileage_text)
        if self.location:
            bits.append(self.location)
        return " - ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Listing":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def merge(self, other: "Listing") -> None:
        """Fill blanks from ``other`` (a richer record) without losing data."""
        for name in self.__dataclass_fields__:
            if name in {"id", "search_id", "search_name", "source"}:
                continue
            new = getattr(other, name)
            if new in (None, "", [], False):
                continue
            current = getattr(self, name)
            if current in (None, "", [], False):
                setattr(self, name, new)
            elif name == "images" and len(new) > len(current):
                setattr(self, name, new)


def name_of(entry: dict[str, Any]) -> str:
    """What to call a car, from a state row rather than a Listing object.

    The parser falls back to "AutoTrader listing <uuid>" when a card has no
    name on it, and enrichment then fills in year, make, model and trim
    without ever going back to fix the title. Nothing failed, and the Feed
    filled up with rows reading "2025 AutoTrader listing 01108a1a-2812-475d-
    b800-be530145cf6f" - a placeholder shown to a person, on the one view
    whose whole job is to be readable at a glance.

    Hidden cars are where it shows: they are deliberately not enriched from
    their own detail page, so the card is all there is. They still have a
    make, a model and a year, which is a name.
    """
    lone = Listing(id=str(entry.get("id") or ""), title="",
                   year=entry.get("year"), make=entry.get("make") or "",
                   model=entry.get("model") or "",
                   trim=entry.get("trim") or "")
    built = lone.composed_title

    title = str(entry.get("title") or "").strip()
    if title and not title.startswith(PLACEHOLDER_TITLE):
        # The dealer's own words, which carry detail no reconstruction has -
        # but only the part before the pipe, because the rest is a feature
        # list. The year goes in front when the title does not already open
        # with one, so "BMW M4 Competition" becomes "2019 BMW M4 Competition"
        # and "2019 BMW M4" is not made into "2019 2019 BMW M4".
        head = title.split("|")[0].strip() or title
        year = str(entry.get("year") or "")
        if year and not head.startswith(year):
            head = f"{year} {head}"
        return head
    if len(built.split()) >= 2:
        return built
    return title or f"{PLACEHOLDER_TITLE}{entry.get('id')}"
