"""The Listing record shared by the scraper, the notifier and the dashboard."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-5]\d)\b")


@dataclass
class Listing:
    id: str
    url: str
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
    def display_title(self) -> str:
        """A tidy 'YEAR MAKE MODEL TRIM' when we know enough, else the title."""
        parts = [str(self.year) if self.year else "", self.make, self.model, self.trim]
        built = " ".join(p for p in parts if p).strip()
        if len(built.split()) >= 2:
            return built
        return (self.title or f"AutoTrader listing {self.id}").strip()

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
