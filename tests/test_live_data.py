"""Behaviour proven against the real listings the bot pulled from autotrader.ca.

The fixture here is rebuilt from the 19 cars a live run actually returned -
real ids, prices, odometers, dealers and cities - so price drops, removals,
relistings and deduplication are exercised on the shapes the site really
produces rather than on hand-written ones.
"""
import json
import re

import pytest

from autotrader import notifiers, runner as runner_mod
from autotrader.config import Config
from autotrader.parser import parse_search_page
from autotrader.runner import run
from autotrader.state import Change, State

from .helpers import Capture, FakeFetcher, use_channels

BASE = "https://www.autotrader.ca/cars/bmw/m5"


@pytest.fixture
def live_html(fixture_html):
    return fixture_html("search_next_data")


@pytest.fixture
def live(tmp_path, monkeypatch, live_html):
    """A watcher pointed at the real captured results page."""
    monkeypatch.chdir(tmp_path)
    cfg = Config.defaults(tmp_path / "config.json")
    cfg.add_search(f"{BASE}?rcp=25&yRng=2021%2C2023&prx=-1", "2021-2023 BMW M5")
    cfg.set("scraping.delay_ms", 0)
    cfg.set("scraping.retries", 0)
    cfg.set("scraping.enrich_details", False)   # the page already has everything
    cfg.set("archive.mode", "off")
    cfg.save()

    sink = Capture()
    use_channels(monkeypatch, runner_mod, [sink])

    def go(html=None, **kw):
        return run(cfg, State.load(tmp_path / "state.json"),
                   fetcher=FakeFetcher(html or live_html), env={}, **kw)

    return type("Live", (), {"cfg": cfg, "sink": sink, "run": staticmethod(go),
                             "path": tmp_path, "html": live_html})


def drop_price(html, old, new):
    """Change one car's price in the real payload, as the site would."""
    assert f'"priceRaw":{old}' in html
    return (html.replace(f'"priceRaw":{old}', f'"priceRaw":{new}')
                .replace(f'"priceFormatted":"$ {old:,}"', f'"priceFormatted":"$ {new:,}"'))


def publish_price(html, listing_id, price):
    """Give one real call-for-price car a figure, as a dealer eventually does."""
    payload = json.loads(re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                                   html, re.S).group(1))
    listings = payload["props"]["pageProps"]["searchResults"]["listings"]
    target = next(l for l in listings if listing_id in l["url"])
    assert target["price"]["priceRaw"] is None, "that car already had a price"
    target["price"] = {"priceFormatted": f"$ {price:,}", "priceRaw": price}
    return re.sub(r'(id="__NEXT_DATA__"[^>]*>).*?(</script>)',
                  lambda m: m.group(1) + json.dumps(payload, ensure_ascii=False) + m.group(2),
                  html, count=1, flags=re.S)


def unpriced_ids(html):
    payload = json.loads(re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                                   html, re.S).group(1))
    return [re.search(r"([0-9a-f-]{36})$", l["url"]).group(1)
            for l in payload["props"]["pageProps"]["searchResults"]["listings"]
            if l["price"]["priceRaw"] is None]


def remove_car(html, listing_id):
    """Drop one listing object out of the payload."""
    data = json.loads(re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S).group(1))
    listings = data["props"]["pageProps"]["searchResults"]["listings"]
    kept = [l for l in listings if listing_id not in l["url"]]
    assert len(kept) < len(listings), f"{listing_id} was not in the payload"
    data["props"]["pageProps"]["searchResults"]["listings"] = kept
    return re.sub(r'(<script id="__NEXT_DATA__"[^>]*>).*?(</script>)',
                  lambda m: m.group(1) + json.dumps(data, separators=(",", ":")) + m.group(2),
                  html, flags=re.S)


def ids_in(html):
    from autotrader.urls import listing_id_from_url
    return {listing_id_from_url(u) for u in re.findall(r'"url":"(https[^"]+/offers/[^"]+)"', html)}


class TestTheRealPage:
    def test_every_car_is_read_completely(self, live_html):
        result = parse_search_page(live_html, BASE)
        assert len(result.listings) == 19
        assert result.strategy == "embedded_json"
        for listing in result.listings:
            assert listing.year, listing.url
            assert listing.location and listing.province
            assert listing.make == "BMW" and listing.model == "M5"

    def test_ids_are_uuids_from_the_offer_urls(self, live_html):
        result = parse_search_page(live_html, BASE)
        for listing in result.listings:
            assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                                r"[0-9a-f]{4}-[0-9a-f]{12}", listing.id)

    def test_prices_and_odometers_are_plausible(self, live_html):
        result = parse_search_page(live_html, BASE)
        priced = [l for l in result.listings if l.price]
        assert len(priced) >= 10
        for listing in priced:
            assert 20_000 < listing.price < 400_000, listing.price
        for listing in result.listings:
            if listing.mileage_km is not None:
                assert 0 <= listing.mileage_km < 400_000


class TestDedupeAcrossRuns:
    def test_the_same_page_twice_announces_once(self, live):
        first = live.run()
        assert first.new == 19
        assert len(live.sink.digests[0]) == 19

        live.sink.digests.clear()
        second = live.run()
        assert second.new == 0
        assert live.sink.digests == []

    def test_five_identical_runs_stay_quiet(self, live):
        live.run()
        live.sink.digests.clear()
        for _ in range(5):
            live.run()
        assert live.sink.digests == []

    def test_state_does_not_grow_on_repeat_runs(self, live):
        live.run()
        after_first = len(json.loads((live.path / "state.json").read_text())["listings"])
        for _ in range(3):
            live.run()
        assert len(json.loads((live.path / "state.json").read_text())["listings"]) == after_first


class TestPriceDropsOnRealCars:
    def test_a_real_drop_is_announced_once(self, live):
        live.run()
        live.sink.digests.clear()

        cheaper = drop_price(live.html, 134998, 122500)
        report = live.run(cheaper)
        assert report.price_drops == 1

        change = live.sink.digests[0][0]
        assert change.kind == Change.PRICE_DROP
        assert change.old_price == 134998 and change.new_price == 122500
        assert change.delta == -12498

        live.sink.digests.clear()
        assert live.run(cheaper).price_drops == 0
        assert live.sink.digests == []

    def test_the_price_history_records_both_figures(self, live):
        live.run()
        live.run(drop_price(live.html, 134998, 122500))
        state = json.loads((live.path / "state.json").read_text())
        car = next(v for v in state["listings"].values() if v.get("price") == 122500)
        assert [p["price"] for p in car["price_history"]] == [134998, 122500]

    def test_a_trivial_drop_is_ignored(self, live):
        """$100 off a $135,000 car is not news."""
        live.run()
        live.sink.digests.clear()
        report = live.run(drop_price(live.html, 134998, 134898))
        assert report.price_drops == 0
        assert live.sink.digests == []

    def test_a_per_search_threshold_overrides_the_global_one(self, live):
        live.run()
        live.sink.digests.clear()
        live.cfg.data["searches"][0]["price_drop_min_abs"] = 20000
        live.cfg.save()
        report = live.run(drop_price(live.html, 134998, 122500))
        assert report.price_drops == 0, "a $12,498 drop is below this search's floor"

    def test_a_price_rise_is_not_reported_as_a_drop(self, live):
        live.run()
        live.sink.digests.clear()
        report = live.run(drop_price(live.html, 134998, 141000))
        assert report.price_drops == 0
        assert report.price_rises == 1
        assert live.sink.digests == [], "rises are off by default"


class TestRemovalAndRelisting:
    GONE = "727d36cb-6045-44ea-bb97-7e3c022e2674"

    def test_a_car_needs_two_absences_before_it_counts_as_sold(self, live):
        live.run()
        without = remove_car(live.html, self.GONE)
        assert live.run(without).removed == 0, "one absence is within the grace period"
        assert live.run(without).removed == 1

    def test_a_removal_is_announced_when_asked_for(self, live):
        live.cfg.data["searches"][0]["notify_on"] = {"removed": True}
        live.cfg.save()
        live.run()
        without = remove_car(live.html, self.GONE)
        live.run(without)
        live.sink.digests.clear()
        live.run(without)
        kinds = [c.kind for batch in live.sink.digests for c in batch]
        assert Change.REMOVED in kinds

    def test_a_car_that_comes_back_is_not_announced_again(self, live):
        live.run()
        without = remove_car(live.html, self.GONE)
        live.run(without); live.run(without)
        state = json.loads((live.path / "state.json").read_text())
        assert state["listings"][self.GONE]["status"] == "gone"

        live.sink.digests.clear()
        live.run()                                   # it is back on the page
        state = json.loads((live.path / "state.json").read_text())
        assert state["listings"][self.GONE]["status"] == "active"
        assert live.sink.digests == [], "a relisting of a known car is not news"

    def test_a_car_that_returns_cheaper_reports_the_drop(self, live):
        live.run()
        without = remove_car(live.html, self.GONE)
        live.run(without); live.run(without)
        live.sink.digests.clear()
        report = live.run(drop_price(live.html, 134998, 119000))
        assert report.price_drops == 1


class TestQuietHoursOnRealData:
    def test_alerts_are_held_then_delivered_intact(self, live, monkeypatch):
        monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: True)
        held = live.run()
        assert held.quiet and held.new == 19
        assert live.sink.digests == []

        monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: False)
        live.run()
        delivered = {c.listing.id for batch in live.sink.digests for c in batch}
        assert len(delivered) == 19, "every held car must arrive"

    def test_nothing_is_delivered_twice_after_quiet_hours(self, live, monkeypatch):
        monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: True)
        live.run()
        monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: False)
        live.run()
        live.sink.digests.clear()
        live.run()
        assert live.sink.digests == []


class TestPendingQueueOnRealData:
    def test_a_total_outage_loses_nothing(self, live, monkeypatch):
        from autotrader.notifiers import Notifier

        class Down(Notifier):
            name = "down"
            def _send(self, changes, run): raise RuntimeError("HTTP 503")

        use_channels(monkeypatch, runner_mod, [Down({}, {}, {})])
        assert live.run().new == 19

        use_channels(monkeypatch, runner_mod, [live.sink])
        live.run()
        delivered = {c.listing.id for batch in live.sink.digests for c in batch}
        assert len(delivered) == 19

    def test_the_queue_drains_exactly_once(self, live, monkeypatch):
        from autotrader.notifiers import Notifier

        class Down(Notifier):
            name = "down"
            def _send(self, changes, run): raise RuntimeError("HTTP 503")

        use_channels(monkeypatch, runner_mod, [Down({}, {}, {})])
        live.run()
        use_channels(monkeypatch, runner_mod, [live.sink])
        live.run()
        live.sink.digests.clear()
        live.run()
        assert live.sink.digests == []


class TestFiltersOnRealCars:
    def test_a_price_ceiling_keeps_only_the_cheap_ones(self, live):
        live.cfg.set("filters.max_price", 100000)
        live.cfg.save()
        live.run()
        announced = [c.listing for batch in live.sink.digests for c in batch]
        assert announced
        for listing in announced:
            assert listing.price is None or listing.price <= 100000

    def test_a_per_search_ceiling_beats_the_global_one(self, live):
        live.cfg.set("filters.max_price", 200000)
        live.cfg.data["searches"][0]["filters"] = {"max_price": 80000}
        live.cfg.save()
        live.run()
        announced = [c.listing for batch in live.sink.digests for c in batch]
        assert announced
        assert all(l.price is None or l.price <= 80000 for l in announced)

    def test_an_odometer_ceiling_works_on_real_readings(self, live):
        live.cfg.set("filters.max_mileage_km", 50000)
        live.cfg.save()
        live.run()
        announced = [c.listing for batch in live.sink.digests for c in batch]
        assert announced
        assert all(l.mileage_km is None or l.mileage_km <= 50000 for l in announced)

    def test_call_for_price_cars_are_tracked_but_not_announced(self, live):
        """require_price used to make them vanish. Eight of these nineteen
        real cars have no figure on them; they are worth knowing about."""
        live.cfg.set("filters.require_price", True)
        live.cfg.save()
        report = live.run()

        assert report.unpriced >= 1
        announced = [c.listing for batch in live.sink.digests for c in batch]
        assert all(l.price for l in announced), "an unpriced car was announced"

        # Recorded, visible, and flagged - not filtered away.
        entries = json.loads((live.path / "state.json").read_text())["listings"]
        unpriced = [e for e in entries.values() if e.get("unpriced")]
        assert len(unpriced) == report.unpriced
        assert not any(e.get("filtered") for e in unpriced)

    def test_an_unpriced_car_can_be_asked_for(self, live):
        live.cfg.set("filters.require_price", True)
        live.cfg.data["searches"][0]["notify_on"] = {"unpriced": True}
        live.cfg.save()
        live.run()

        announced = [c.listing for batch in live.sink.digests for c in batch]
        assert any(l.price is None for l in announced)

    def test_a_year_floor_uses_the_recovered_model_year(self, live):
        live.cfg.set("filters.min_year", 2023)
        live.cfg.save()
        live.run()
        announced = [c.listing for batch in live.sink.digests for c in batch]
        assert announced
        assert all(l.year >= 2023 for l in announced)

    def test_a_filtered_car_is_never_reported_as_removed(self, live):
        live.cfg.set("filters.max_price", 100000)
        live.cfg.data["searches"][0]["notify_on"] = {"removed": True}
        live.cfg.save()
        for _ in range(4):
            report = live.run()
        assert report.removed == 0


class TestFilteredCarsStayOutOfSight:
    def test_a_filtered_car_is_published_only_as_a_hidden_row(self, live):
        """It has to be countable and explainable, so the dashboard can say
        "44 hidden by your rules" instead of quietly showing a smaller number
        than the site does. It must never look like a car you are watching."""
        from autotrader.dashboard import build_payload
        live.cfg.set("filters.max_price", 100000)
        live.cfg.save()
        live.run()

        state = State.load(live.path / "state.json")
        payload = build_payload(live.cfg, state, {})

        shown = [i for i in payload["listings"] if not i["filtered"]]
        hidden = [i for i in payload["listings"] if i["filtered"]]
        assert shown and hidden
        for item in shown:
            assert item.get("price") is None or item["price"] <= 100000
        for item in hidden:
            assert item["price"] > 100000
            assert "above maximum" in item["filter_reason"]
        assert payload["health"]["counts"]["filtered"] == len(hidden)

    def test_the_live_count_excludes_filtered_cars(self, live):
        live.cfg.set("filters.max_price", 100000)
        live.cfg.save()
        live.run()
        state = State.load(live.path / "state.json")
        active = [v for v in state.listings.values()
                  if v.get("status") == "active" and not v.get("filtered")]
        assert state.stats()["active"] == len(active)
        assert all((v.get("price") or 0) <= 100000 for v in active)

    def test_filtered_cars_are_still_tracked_internally(self, live):
        """They must be, or the next run reports them as removed."""
        live.cfg.set("filters.max_price", 100000)
        live.cfg.data["searches"][0]["notify_on"] = {"removed": True}
        live.cfg.save()
        for _ in range(4):
            report = live.run()
        assert report.removed == 0
        state = State.load(live.path / "state.json")
        assert any(v.get("filtered") for v in state.listings.values())

    def test_a_car_that_stops_being_filtered_becomes_visible(self, live):
        live.cfg.set("filters.max_price", 80000)
        live.cfg.save()
        live.run()
        before = State.load(live.path / "state.json").stats()["active"]

        live.cfg.set("filters.max_price", None)
        live.cfg.save()
        live.run()
        after = State.load(live.path / "state.json").stats()["active"]
        assert after > before


class TestForgettingRemovedSearches:
    def test_a_removed_search_stops_being_reported(self, live):
        live.run()
        state = State.load(live.path / "state.json")
        assert set(state.data["searches"])

        stale = live.cfg.searches[0].id
        live.cfg.remove_search(stale)
        live.cfg.add_search("https://www.autotrader.ca/cars/bmw/m3/?prx=-2", "M3")
        live.cfg.save()
        live.run()

        state = State.load(live.path / "state.json")
        assert stale not in state.data["searches"], \
            "a search you deleted must not keep reporting its last failure"

    def test_configured_searches_are_kept(self, live):
        live.run()
        live.run()
        state = State.load(live.path / "state.json")
        assert {s.id for s in live.cfg.searches} <= set(state.data["searches"])


class TestCallForPriceOnRealCars:
    """Eight of these nineteen real cars publish no figure at all.

    Under the old rule, ``require_price`` deleted them from view: not filtered
    into a bucket, not counted, simply absent. On a search whose whole point is
    finding a bargain, the cars a dealer will not price in public are the last
    ones you want silently dropped.
    """

    def test_the_real_payload_really_does_contain_unpriced_cars(self, live_html):
        assert len(unpriced_ids(live_html)) == 8

    def test_they_are_kept_without_require_price(self, live):
        report = live.run()
        assert report.unpriced == 0        # nothing to bucket: they are kept
        entries = json.loads((live.path / "state.json").read_text())["listings"]
        assert sum(1 for e in entries.values() if e.get("price") is None) == 8

    def test_a_price_that_appears_later_is_announced(self, live):
        live.cfg.set("filters.require_price", True)
        live.cfg.save()
        live.run()
        live.sink.digests.clear()

        target = unpriced_ids(live.html)[0]
        live.run(publish_price(live.html, target, 96500))

        announced = [c for batch in live.sink.digests for c in batch]
        priced = [c for c in announced if c.kind == Change.PRICED]
        assert len(priced) == 1
        assert priced[0].listing.id == target
        assert priced[0].new_price == 96500
        assert "96,500" in priced[0].describe()

    def test_a_published_price_is_not_reported_as_a_price_drop(self, live):
        """There is nothing to compare against, so calling it a drop is a lie."""
        live.run()
        live.sink.digests.clear()
        target = unpriced_ids(live.html)[0]
        report = live.run(publish_price(live.html, target, 96500))

        assert report.price_drops == 0 and report.price_rises == 0
        assert report.priced == 1

    def test_an_unpriced_car_is_never_re_announced_as_new(self, live):
        """It was recorded quietly, so it must not resurface as a discovery."""
        live.cfg.set("filters.require_price", True)
        live.cfg.save()
        live.run()
        live.sink.digests.clear()

        report = live.run()
        assert report.new == 0
        assert not [c for batch in live.sink.digests for c in batch]

    def test_an_unpriced_car_is_not_reported_as_removed(self, live):
        """It is still on the page; it just has no figure on it."""
        live.cfg.set("filters.require_price", True)
        live.cfg.data["searches"][0]["notify_on"] = {"removed": True}
        live.cfg.save()
        for _ in range(4):
            report = live.run()
        assert report.removed == 0

    def test_the_detail_page_is_consulted_a_bounded_number_of_times(
            self, live, monkeypatch):
        """Worth a look; not worth a request every run forever."""
        live.cfg.set("scraping.enrich_details", True)
        live.cfg.set("scraping.unpriced_rechecks", 2)
        live.cfg.save()

        fetched: list[str] = []
        target = unpriced_ids(live.html)[0]

        def go():
            fetcher = FakeFetcher(live.html)
            original = fetcher.get

            def spy(url, **kw):
                if "/offers/" in url:
                    fetched.append(url)
                return original(url, **kw)

            fetcher.get = spy
            return run(live.cfg, State.load(live.path / "state.json"),
                       fetcher=fetcher, env={})

        for _ in range(5):
            go()

        looks = [u for u in fetched if target in u]
        # Once as an unknown car, then twice more on the recheck allowance.
        assert len(looks) == 3, looks
