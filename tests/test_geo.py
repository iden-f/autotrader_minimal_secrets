"""Enforcing the location the pasted link asks for and the site ignores.

`prx=-1&loc=V6N+3B5` is in the search link, and the 2026 platform drops it:
that watch was returning cars in Ontario, Alberta and Quebec. Nothing failed;
the constraint was simply not applied by anyone.
"""
import pytest

from autotrader import geo
from autotrader.filters import apply, check
from autotrader.listing import Listing

VAN = geo.locate_reference("V6N 3B5")
NEAR_VAN = {"near": "V6N 3B5", "max_distance_km": 250}


def car(city, province, **kw):
    return Listing(id=kw.pop("id", city or "x"), title="2022 BMW M5",
                   price=99000, location=city, province=province, **kw)


class TestPlacingACar:
    def test_a_postal_code_resolves_to_its_neighbourhood(self):
        assert VAN is not None
        assert geo.distance_km(VAN, geo.locate("Vancouver", "BC")) < 10

    def test_a_postal_code_with_no_entry_falls_back_to_its_region(self):
        assert geo.locate_reference("V9Z 1A1") == geo.POSTAL_ANCHOR["V"]
        assert geo.region_of("V9Z 1A1") == "BC"

    def test_a_city_and_province_resolve(self):
        assert geo.locate_reference("Toronto, ON") == geo.CITIES["ON"]["toronto"]

    def test_accents_and_punctuation_do_not_matter(self):
        assert geo.locate("Montréal", "QC") == geo.locate("MONTREAL", "QC")
        assert geo.locate("St. Catharines", "ON") == geo.locate("St catharines", "ON")
        assert geo.locate("Trois-Rivières", "QC") is not None

    def test_a_place_nobody_has_heard_of_is_simply_unknown(self):
        assert geo.locate("Nowheresville", "BC") is None

    def test_distances_are_the_right_order_of_magnitude(self):
        van, tor = geo.locate("Vancouver", "BC"), geo.locate("Toronto", "ON")
        assert 3200 < geo.distance_km(van, tor) < 3500      # ~3,360 km
        assert geo.distance_km(van, geo.locate("Victoria", "BC")) < 120


class TestTheRadius:
    @pytest.mark.parametrize("city,province", [
        ("Vancouver", "BC"), ("Richmond", "BC"), ("Surrey", "BC"),
        ("Victoria", "BC"), ("Nanaimo", "BC"), ("Whistler", "BC"),
        ("Chilliwack", "BC"),
    ])
    def test_the_lower_mainland_and_the_island_are_in(self, city, province):
        assert check(car(city, province), NEAR_VAN).keep

    @pytest.mark.parametrize("city,province", [
        ("Toronto", "ON"), ("Thornhill", "ON"), ("Calgary", "AB"),
        ("Edmonton", "AB"), ("Laval", "QC"), ("Montréal", "QC"),
        ("Winnipeg", "MB"), ("Saskatoon", "SK"), ("Halifax", "NS"),
        ("Kelowna", "BC"),
    ])
    def test_everything_the_watch_was_wrongly_pulling_is_out(self, city, province):
        verdict = check(car(city, province), NEAR_VAN)
        assert not verdict.keep
        assert "beyond the 250 km" in verdict.reason

    def test_the_reason_names_the_distance(self):
        verdict = check(car("Toronto", "ON"), NEAR_VAN)
        assert "3,365 km" in verdict.reason and "V6N 3B5" in verdict.reason


class TestNotHidingWhatItCannotJudge:
    def test_a_car_with_no_location_is_kept(self):
        assert check(car("", ""), NEAR_VAN).keep

    def test_an_unknown_town_in_range_is_kept(self):
        """The table is the thing most likely to be incomplete, so a missing
        town must never be the reason a match is hidden."""
        assert check(car("Nowheresville", "BC"), NEAR_VAN).keep

    def test_an_unknown_town_in_a_province_nothing_can_reach_is_excluded(self):
        """Nothing in Ontario is within 250 km of Vancouver, whatever it is
        called, so this one can be decided honestly."""
        assert not check(car("Nowheresville", "ON"), NEAR_VAN).keep

    def test_a_reference_that_cannot_be_placed_enforces_nothing(self):
        rule = {"near": "Narnia", "max_distance_km": 50}
        assert check(car("Toronto", "ON"), rule).keep

    def test_no_radius_means_no_rule(self):
        assert check(car("Toronto", "ON"), {"near": "V6N 3B5"}).keep
        assert check(car("Toronto", "ON"), {"near": "V6N 3B5",
                                            "max_distance_km": 0}).keep


class TestProvinceAllowlist:
    def test_it_keeps_only_the_listed_provinces(self):
        kept, _, dropped = apply(
            [car("Vancouver", "BC", id="a"), car("Toronto", "ON", id="b")],
            {"provinces": ["BC"]})
        assert [l.id for l in kept] == ["a"]
        assert "not one of BC" in dropped[0][1]

    def test_a_car_with_no_province_is_kept(self):
        assert check(car("Somewhere", ""), {"provinces": ["BC"]}).keep


class TestWhatTheDashboardIsTold:
    def test_the_area_is_spelled_out(self):
        from autotrader.dashboard import _area_of
        area = _area_of(NEAR_VAN)
        assert area["text"] == "within 250 km of V6N 3B5"
        assert area["resolved"] is True and area["province"] == "BC"

    def test_an_unresolvable_reference_says_so(self):
        from autotrader.dashboard import _area_of
        assert _area_of({"near": "Narnia", "max_distance_km": 50})["resolved"] is False

    def test_no_rule_means_nothing_to_show(self):
        from autotrader.dashboard import _area_of
        assert _area_of({"max_price": 100000}) is None


class TestAWatchThatMatchesNothing:
    """Reading the site fine and keeping nothing is not a failure, but it
    looks exactly like one - and a rule quietly matching nothing for months
    is the reason to say so out loud.

    Enforcing 250 km around Vancouver on the 2021-2023 watch does exactly
    this today: there is no M5 of those years within 600 km of the city.
    """

    def test_it_says_what_turned_everything_away(self, tmp_path, monkeypatch,
                                                 fixture_html):
        from autotrader import runner as runner_mod
        from autotrader.config import Config
        from autotrader.runner import run
        from autotrader.state import State
        from .helpers import Capture, FakeFetcher, use_channels

        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=25", "BMW M5")
        cfg.set("scraping.delay_ms", 0)
        cfg.set("scraping.enrich_details", False)
        cfg.set("archive.mode", "off")
        # Tight enough that even the two Vancouver cars in the payload fall
        # outside it, so every one of the nineteen is turned away by distance.
        cfg.set("filters.near", "V6N 3B5")
        cfg.set("filters.max_distance_km", 3)
        cfg.save()
        use_channels(monkeypatch, runner_mod, [Capture()])

        report = run(cfg, State.load(tmp_path / "state.json"),
                     fetcher=FakeFetcher(fixture_html("search_next_data")), env={})

        assert report.ok, report.errors
        assert report.shut_out == ["BMW M5"]
        warning = next(w for w in report.warnings if "none passed" in w)
        assert "19 too far away" in warning
        assert "It is working" in warning

    def test_the_reason_ranks_the_rules_that_hurt_most(self):
        from autotrader.runner import _why_none_survived
        from autotrader.listing import Listing

        car = Listing(id="x", title="t")
        dropped = [(car, "Toronto, ON is 3,365 km from V6N 3B5, beyond the 250 km you asked for")] * 17
        dropped += [(car, "year 2019 below minimum 2021")] * 2
        assert _why_none_survived(dropped) == "17 too far away, 2 outside the year range"

    def test_a_search_that_finds_something_again_clears_the_marker(
            self, tmp_path, monkeypatch, fixture_html):
        from autotrader import runner as runner_mod
        from autotrader.config import Config
        from autotrader.runner import run
        from autotrader.state import State
        from .helpers import Capture, FakeFetcher, use_channels

        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=25", "BMW M5")
        cfg.set("scraping.delay_ms", 0)
        cfg.set("scraping.enrich_details", False)
        cfg.set("archive.mode", "off")
        cfg.set("filters.provinces", ["NU"])
        cfg.save()
        use_channels(monkeypatch, runner_mod, [Capture()])
        html = fixture_html("search_next_data")

        run(cfg, State.load(tmp_path / "state.json"),
            fetcher=FakeFetcher(html), env={})
        state = State.load(tmp_path / "state.json")
        sid = cfg.searches[0].id
        assert state.search_health(sid).get("shut_out")

        cfg.set("filters.provinces", [])
        cfg.save()
        run(cfg, State.load(tmp_path / "state.json"),
            fetcher=FakeFetcher(html), env={})
        assert not State.load(tmp_path / "state.json").search_health(sid).get("shut_out")

    def test_a_search_that_keeps_something_is_not_flagged(self, tmp_path,
                                                          monkeypatch, fixture_html):
        """Two of these nineteen really are in Vancouver."""
        from autotrader import runner as runner_mod
        from autotrader.config import Config
        from autotrader.runner import run
        from autotrader.state import State
        from .helpers import Capture, FakeFetcher, use_channels

        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=25", "BMW M5")
        cfg.set("scraping.delay_ms", 0)
        cfg.set("scraping.enrich_details", False)
        cfg.set("archive.mode", "off")
        cfg.set("filters.near", "V6N 3B5")
        cfg.set("filters.max_distance_km", 250)
        cfg.save()
        use_channels(monkeypatch, runner_mod, [Capture()])

        report = run(cfg, State.load(tmp_path / "state.json"),
                     fetcher=FakeFetcher(fixture_html("search_next_data")), env={})
        assert report.shut_out == []
        assert report.new == 2
