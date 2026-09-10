"""Reading pasted links - the one thing the user actually has to do."""
import pytest

from autotrader.urls import (canonical_listing_url, describe_search, is_listing_url,
                             listing_id_from_url, normalise_search_url, page_url)


@pytest.mark.parametrize("url,expected", [
    ("https://www.autotrader.ca/a/bmw/m5/winnipeg/manitoba/19_13166607_/", "13166607"),
    ("https://www.autotrader.ca/a/bmw/m5/st.%20catharines/ontario/5_68819631_on2008/", "68819631"),
    ("https://www.autotrader.ca/a/bmw/m5/st. catharines/ontario/5_68819631_on2008/", "68819631"),
    ("/a/bmw/m5/trois-rivi%C3%A8res/quebec/19_13123827_/", "13123827"),
    ("https://www.autohebdo.net/a/bmw/m5/laval/quebec/19_13221555_/", "13221555"),
])
def test_listing_id_is_the_middle_number(url, expected):
    assert listing_id_from_url(url) == expected


def test_tracking_numbers_in_the_query_are_not_mistaken_for_ids():
    url = ("https://www.autotrader.ca/a/bmw/m5/x/on/5_68819631_on2008/"
           "?showcpo=1&ncse=no&ursrc=xpl&urp=3&urm=8&sprx=-2&adtype=99887766")
    assert listing_id_from_url(url) == "68819631"


def test_non_listing_urls_have_no_id():
    assert listing_id_from_url("https://www.autotrader.ca/cars/bmw/m5/") is None
    assert listing_id_from_url("") is None
    assert not is_listing_url("https://www.autotrader.ca/cars/bmw/")


def test_canonical_url_drops_tracking_and_normalises_host():
    messy = "https://www.autohebdo.net/a/bmw/m5/x/on/5_68819631_z/?ursrc=xpl&urp=3"
    assert canonical_listing_url(messy) == "https://www.autotrader.ca/a/bmw/m5/x/on/5_68819631_z/"


def test_the_same_car_always_yields_one_url():
    a = canonical_listing_url("https://www.autotrader.ca/a/bmw/m5/st. catharines/on/5_1234567_z/")
    b = canonical_listing_url("https://www.autotrader.ca/a/bmw/m5/st.%20catharines/on/5_1234567_z/")
    assert a == b


def test_pasted_link_is_read_back_in_plain_english():
    s = describe_search(
        "https://www.autotrader.ca/cars/bmw/m5/on/st%20catharines/?rcp=25&rcs=0&srt=9"
        "&yRng=2022%2c&pRng=%2c105000&prx=25&prv=Ontario&loc=St.+Catharines"
        "&body=Sedan&hprc=True&wcp=True&sts=Used")
    assert s.valid
    assert s.title() == "2022+ BMW M5"
    assert s.price_max == 105000 and s.year_min == 2022
    assert s.radius_km == 25 and s.location == "St. Catharines"
    chips = s.describe()
    assert "under $105,000" in chips and "Sedan" in chips
    assert "near St. Catharines (25 km)" in chips


def test_negative_proximity_means_the_whole_country():
    s = describe_search("https://www.autotrader.ca/cars/bmw/m5/?prx=-2&rcp=15")
    assert s.radius_km is None
    assert "Canada-wide" in s.describe()


@pytest.mark.parametrize("url,fragment", [
    ("https://www.kijiji.ca/b-cars/", "not autotrader.ca"),
    ("https://www.autotrader.ca/", "home page"),
    ("https://www.autotrader.ca/a/bmw/m5/w/mb/19_13166607_/", "single car listing"),
    ("", "Paste an AutoTrader search link"),
])
def test_unusable_links_explain_themselves(url, fragment):
    s = describe_search(url)
    assert not s.valid
    assert fragment in " ".join(s.problems)


def test_pagination_uses_offset_not_page_number():
    base = "https://www.autotrader.ca/cars/bmw/m5/?rcp=15&rcs=0&srt=35"
    assert "rcs=15" in page_url(base, 2)
    assert "rcs=30" in page_url(base, 3)
    assert "rcs=100" in page_url(base, 3, 50)
    assert "rcp=50" in page_url(base, 3, 50)


def test_page_one_starts_at_zero():
    assert "rcs=0" in page_url("https://www.autotrader.ca/cars/bmw/m5/", 1, 25)


def test_bare_hostname_is_upgraded_to_https():
    assert normalise_search_url("www.autotrader.ca/cars/bmw/").startswith("https://")


@pytest.mark.parametrize("url,city,province", [
    ("https://www.autotrader.ca/a/bmw/m5/winnipeg/manitoba/19_13166607_/",
     "Winnipeg", "Manitoba"),
    ("https://www.autotrader.ca/a/bmw/m5/st.%20catharines/ontario/5_68819631_on/",
     "St. Catharines", "Ontario"),
    ("https://www.autotrader.ca/a/bmw/m5/surrey/british%20columbia/19_13187452_/",
     "Surrey", "British Columbia"),
])
def test_the_city_is_recovered_from_the_listing_url(url, city, province):
    """schema.org data has no location field, but the URL path does."""
    from autotrader.urls import location_from_url
    assert location_from_url(url) == (city, province)


def test_hyphenated_place_names_keep_their_hyphen():
    from autotrader.urls import location_from_url
    city, province = location_from_url(
        "https://www.autotrader.ca/a/bmw/m5/trois-rivi%C3%A8res/quebec/19_13123827_/")
    assert city == "Trois-Rivières" and province == "Quebec"


def test_a_search_url_has_no_listing_location():
    from autotrader.urls import location_from_url
    assert location_from_url("https://www.autotrader.ca/cars/bmw/m5/") == ("", "")


def test_page_url_carries_both_pagination_schemes():
    """The 2026 platform paginates with ``page``; the old one with ``rcs``."""
    first = page_url("https://www.autotrader.ca/cars/bmw/m5/?prx=-2", 1, 50)
    assert "page=" not in first
    assert "rcs=0" in first

    second = page_url("https://www.autotrader.ca/cars/bmw/m5/?prx=-2", 2, 50)
    assert "page=2" in second
    assert "rcs=50" in second
    # The original query is preserved either way.
    assert "prx=-2" in second


def test_page_url_replaces_a_page_number_already_in_the_link():
    """A link pasted from page 3 must not pin every request to page 3."""
    pasted = "https://www.autotrader.ca/cars/bmw/m5/?page=3"
    assert "page=" not in page_url(pasted, 1)
    assert "page=2" in page_url(pasted, 2)
