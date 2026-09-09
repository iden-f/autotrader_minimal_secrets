"""Detail pages carry schema.org data - the richest, most stable source."""
import pytest

from autotrader.enrich import detail_from_html, enrich
from autotrader.listing import Listing


def test_real_archived_pages_all_yield_structured_data(archive_html, archive_ids):
    if not archive_ids:
        pytest.skip("no archived pages available")
    read = 0
    for listing_id in archive_ids:
        detail = detail_from_html(archive_html(listing_id), listing_id)
        assert detail is not None, listing_id
        if detail.price and detail.mileage_km:
            read += 1
    # A handful of real listings genuinely say "please call" instead of a price.
    assert read >= len(archive_ids) - 6


def test_a_known_page_is_read_exactly(archive_html):
    detail = detail_from_html(archive_html("68819631"), "68819631")
    assert detail.year == 2022 and detail.make == "BMW" and detail.model == "M5"
    assert detail.price == 102199 and detail.currency == "CAD"
    assert detail.mileage_km == 30244
    assert detail.drivetrain == "AWD"
    assert detail.transmission == "Automatic"
    assert detail.color == "Black Sapphire Metallic"
    assert detail.price_source == "detail"


def test_photos_come_from_structured_data_not_page_images(archive_html):
    detail = detail_from_html(archive_html("13166607"), "13166607")
    assert detail.images, "expected real photo URLs"
    assert all("vehicleimages" in url for url in detail.images)
    assert not any("manufacturer/logo" in url for url in detail.images)


def test_masked_vins_are_discarded(archive_html, archive_ids):
    for listing_id in archive_ids[:10]:
        detail = detail_from_html(archive_html(listing_id), listing_id)
        assert "XXXX" not in (detail.vin or "")


def test_placeholder_values_are_dropped(archive_html):
    detail = detail_from_html(archive_html("13176580"), "13176580")
    assert detail.color == ""          # the page says "Not Specified"


def test_the_body_style_is_not_repeated_in_the_trim(archive_html):
    detail = detail_from_html(archive_html("68819631"), "68819631")
    if detail.body and detail.trim:
        assert not detail.trim.lower().endswith(detail.body.lower())


def test_enrichment_overrides_a_guessed_card_price(archive_html):
    card = Listing(id="68819631", url="https://www.autotrader.ca/a/x/5_68819631_z/",
                   title="2022 BMW M5", price=99999, price_source="search",
                   mileage_km=1, search_id="s1")
    enrich(card, archive_html("68819631"))
    assert card.price == 102199 and card.price_source == "detail"
    assert card.mileage_km == 30244
    assert card.search_id == "s1"      # search association is preserved
    assert card.enriched


def test_enrichment_of_an_unusable_page_leaves_the_listing_alone():
    card = Listing(id="1", url="u", title="x", price=100, price_source="search")
    enrich(card, "<html><body>nothing useful</body></html>")
    assert card.price == 100 and card.price_source == "search"
