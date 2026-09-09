"""autotrader.ca moved onto the AutoScout24 platform in 2026.

Listing URLs changed from /a/<make>/<model>/<city>/<province>/<n>_<id>_<ref>/
to /offers/<slug>-<uuid>, the page became a Next.js app, and schema.org @type
started arriving as a list. Every one of those broke the scraper silently; the
fixture here is built from a real captured page.
"""
import pytest

from autotrader.enrich import detail_from_html
from autotrader.listing import Listing
from autotrader.parser import parse_search_page
from autotrader.urls import (canonical_listing_url, describe_search, is_offer_url,
                             listing_id_from_url)
from autotrader.validate import assess

BASE = "https://www.autotrader.ca/cars/bmw/m5"
OFFER = ("https://www.autotrader.ca/offers/bmw-m5-competition-premium-aide-a-la-"
         "conduite-avance-gasoline-blue-cat_ma13gr202609tr17957-"
         "f3b59e36-c73b-4e58-88c4-746af0d18a45")
UUID = "f3b59e36-c73b-4e58-88c4-746af0d18a45"


class TestNewUrlScheme:
    def test_the_trailing_uuid_is_the_listing_id(self):
        assert listing_id_from_url(OFFER) == UUID

    def test_the_shared_campaign_token_is_not_mistaken_for_an_id(self):
        """Every result on a page carries the same cat_ma13gr... segment."""
        other = OFFER.replace(UUID, "36418c13-b328-4d73-9621-643fe01969fa")
        assert listing_id_from_url(other) != listing_id_from_url(OFFER)

    def test_the_fragment_is_ignored(self):
        assert listing_id_from_url(OFFER + "#vehicle") == UUID

    def test_uuids_are_normalised_to_lower_case(self):
        assert listing_id_from_url(OFFER.replace(UUID, UUID.upper())) == UUID

    def test_the_old_scheme_still_resolves(self):
        """The 50 archived listings must keep their identity."""
        old = "https://www.autotrader.ca/a/bmw/m5/winnipeg/manitoba/19_13166607_/"
        assert listing_id_from_url(old) == "13166607"
        assert not is_offer_url(old)

    def test_an_offer_link_is_recognised_as_a_listing_not_a_search(self):
        assert is_offer_url(OFFER)
        summary = describe_search(OFFER)
        assert not summary.valid
        assert "single car listing" in " ".join(summary.problems)

    def test_tracking_parameters_are_stripped(self):
        assert canonical_listing_url(OFFER + "?utm_source=email") == OFFER

    def test_a_slug_without_a_uuid_is_not_a_listing(self):
        assert listing_id_from_url("https://www.autotrader.ca/offers/") is None


class TestNewSearchPage:
    def test_the_search_page_is_read_from_its_structured_data(self, fixture_html):
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        assert result.strategy == "jsonld"
        assert len(result) == 3

    def test_a_type_given_as_a_list_still_matches(self, fixture_html):
        """@type is ["Car","Product"] now; str() on that matched nothing."""
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        assert result.candidates["jsonld"] == 3

    def test_every_field_that_matters_is_recovered(self, fixture_html):
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        car = next(l for l in result.listings if l.id == UUID)
        assert car.price == 125669 and car.currency == "CAD"
        assert car.mileage_km == 27314
        assert car.make == "BMW" and car.model == "M5"
        assert car.transmission == "Automatic"
        assert car.fuel == "Gasoline"
        assert car.url == OFFER

    def test_the_location_comes_from_the_seller_address(self, fixture_html):
        """The new URLs have no city segment; the offer carries an address."""
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        car = next(l for l in result.listings if l.id == UUID)
        assert car.location == "Montréal"
        assert car.province == "QC"
        assert car.seller == "BMW MINI Montréal Centre"

    def test_shouted_dealer_cities_are_tidied(self, fixture_html):
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        assert all(l.location != l.location.upper() or len(l.location) <= 2
                   for l in result.listings if l.location)

    def test_photos_are_recovered(self, fixture_html):
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        for listing in result.listings:
            assert listing.images
            assert all(u.startswith("http") for u in listing.images)

    def test_prices_are_plausible(self, fixture_html):
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        for listing in result.listings:
            assert 10_000 < listing.price < 500_000

    def test_the_breadcrumb_block_does_not_produce_phantom_listings(self, fixture_html):
        """The page also carries a BreadcrumbList of ListItems."""
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        assert all(l.id != "" and len(l.id) > 8 for l in result.listings)

    def test_this_parse_passes_the_first_run_check(self, fixture_html):
        result = parse_search_page(fixture_html("search_2026_platform"), BASE)
        verdict = assess(result.listings, result.strategy, "BMW M5",
                         candidates=result.candidates)
        assert verdict.trustworthy, verdict.concerns


class TestReadableOutput:
    def test_a_pipe_stuffed_trim_is_cut_down(self):
        listing = Listing(id="1", url="u", make="BMW", model="M5",
                          trim="Competition | Premium | Aide a la conduite avance")
        assert listing.short_trim == "Competition"
        assert listing.display_title == "BMW M5 Competition"

    def test_a_comma_stuffed_trim_is_cut_down(self):
        listing = Listing(id="1", url="u", make="BMW", model="M5",
                          trim="Premium Package, M Drivers Package!")
        assert listing.display_title == "BMW M5 Premium Package"

    def test_a_plain_trim_is_left_alone(self):
        listing = Listing(id="1", url="u", year=2022, make="BMW", model="M5",
                          trim="Competition")
        assert listing.display_title == "2022 BMW M5 Competition"

    def test_an_absurdly_long_trim_is_bounded(self):
        listing = Listing(id="1", url="u", make="BMW", model="M5", trim="x" * 300)
        assert len(listing.short_trim) <= 40


class TestOldArchivesStillParse:
    def test_the_previous_platform_detail_pages_still_read(self, archive_html):
        """The 50 archived pages predate the migration and must keep working."""
        detail = detail_from_html(archive_html("68819631"), "68819631")
        assert detail.price == 102199 and detail.mileage_km == 30244
        assert detail.year == 2022
