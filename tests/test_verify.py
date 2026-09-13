"""Asking the site whether the ledger is right.

Four consecutive checks reported nothing changed. That has two explanations -
a quiet market, or a bot that cannot see change - and they look identical from
the inside, because both of them are the bot reporting on itself.

The first version of this command shipped with `Fetcher(cfg)`, which is not
the constructor's signature, and it failed on the runner in four seconds. That
is what these are for: the command is only ever run by hand, so nothing else
would have found it.
"""

from __future__ import annotations

import pytest

from autotrader.cli import main
from autotrader.config import Config
from autotrader.listing import Listing
from autotrader.state import State

PAGE = """<html><head><title>car</title></head><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":["Car","Product"],
 "name":"BMW M4 Competition","offers":{"@type":"Offer","price":%s,
 "priceCurrency":"CAD"},"mileageFromOdometer":{"value":50000,"unitCode":"KMT"}}
</script></body></html>"""


class Site:
    """The live site, as far as this command can tell."""

    # What the bot holds, unless a test says the site has changed its mind.
    HOLDS = {"aaa": 60000, "bbb": 70000}

    def __init__(self, prices=None, missing=(), boom=()):
        self.prices = {**self.HOLDS, **(prices or {})}
        self.missing = set(missing)
        self.boom = set(boom)
        self.asked = []
        self.spent = 0

    def get(self, url, referer=None, allow_block=False):
        self.asked.append(url)
        self.spent += 1
        lid = url.rstrip("/").split("_")[-2] if "_" in url else url
        for key in list(self.prices) + list(self.missing) + list(self.boom):
            if key in url:
                lid = key
                break
        if lid in self.boom:
            from autotrader.http import FetchError
            exc = FetchError("gone")
            exc.status = 404
            raise exc
        if lid in self.missing:
            return "<html><body>This listing is no longer available</body></html>"
        return PAGE % self.prices.get(lid, 60000)

    def close(self):
        pass


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config.defaults(tmp_path / "config.json")
    search = cfg.add_search("https://www.autotrader.ca/cars/bmw/m4/?rcp=25", "M4")
    cfg.set("scraping.delay_ms", 0)
    cfg.save()
    state = State(path=tmp_path / "state.json")
    for lid, price in (("aaa", 60000), ("bbb", 70000)):
        state.record(Listing(
            id=lid, url=f"https://www.autotrader.ca/a/bmw/m4/x/on/19_{lid}_/",
            title="BMW M4", year=2018, make="BMW", model="M4",
            price=price, price_source="detail", search_id=search.id))
    state.save()
    return type("B", (), {"cfg": cfg, "path": tmp_path})


def run(monkeypatch, site, *args):
    from autotrader import cli
    monkeypatch.setattr(cli, "Fetcher", lambda **kw: site)
    return main(["verify", *args])


class TestItActuallyRuns:
    def test_the_fetcher_is_built_the_way_the_fetcher_wants(self, bench, monkeypatch):
        """The bug that shipped: Fetcher(cfg), four seconds into a job."""
        seen = {}
        from autotrader import cli
        from autotrader.http import Fetcher as Real

        def spy(*args, **kwargs):
            seen["args"], seen["kwargs"] = args, kwargs
            return Site()

        monkeypatch.setattr(cli, "Fetcher", spy)
        main(["verify"])
        assert not seen["args"], "Fetcher takes keyword arguments only"
        import inspect
        allowed = set(inspect.signature(Real.__init__).parameters) - {"self"}
        assert set(seen["kwargs"]) <= allowed, set(seen["kwargs"]) - allowed

    def test_it_asks_once_per_car(self, bench, monkeypatch):
        site = Site()
        assert run(monkeypatch, site) == 0
        assert len(site.asked) == 2

    def test_the_budget_is_sized_to_the_question(self, bench, monkeypatch):
        """Not the whole run's budget: this is a question, not a check."""
        seen = {}
        from autotrader import cli
        monkeypatch.setattr(cli, "Fetcher",
                            lambda **kw: (seen.update(kw), Site())[1])
        main(["verify"])
        assert seen["budget"] < 100


class TestWhatItReports:
    def test_agreement_is_reported_as_agreement(self, bench, monkeypatch, capsys):
        assert run(monkeypatch, Site(), "--verbose") == 0
        out = capsys.readouterr().out
        assert "2 cars agree" in out

    def test_a_price_that_moved_is_named_with_both_figures(self, bench, monkeypatch, capsys):
        run(monkeypatch, Site(prices={"aaa": 57000}))
        out = capsys.readouterr().out
        assert "$60,000" in out and "$57,000" in out
        assert "1 differ" in out or "differ" in out

    def test_a_car_the_site_has_dropped_is_named(self, bench, monkeypatch, capsys):
        run(monkeypatch, Site(boom=["aaa"]))
        out = capsys.readouterr().out
        assert "gone from the site" in out

    def test_a_page_that_says_it_is_gone_counts_as_gone(self, bench, monkeypatch, capsys):
        run(monkeypatch, Site(missing=["bbb"]))
        assert "gone" in capsys.readouterr().out

    def test_disagreement_is_information_not_failure(self, bench, monkeypatch):
        """The command answers a question. An answer is not an error."""
        assert run(monkeypatch, Site(prices={"aaa": 1})) == 0

    def test_limit_stops_early(self, bench, monkeypatch):
        site = Site()
        run(monkeypatch, site, "--limit", "1")
        assert len(site.asked) == 1
