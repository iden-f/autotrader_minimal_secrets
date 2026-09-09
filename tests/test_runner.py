"""End-to-end behaviour, including the failure modes that killed version 1."""
import json

import pytest

from autotrader import notifiers, runner as runner_mod
from autotrader.config import Config
from autotrader.http import BlockedError, FetchError, Response
from autotrader.notifiers import Notifier, Result
from autotrader.runner import run
from autotrader.state import Change, State

SEARCH = "https://www.autotrader.ca/cars/bmw/m5/?rcp=15&srt=35&prx=-2&loc=M5V"


class FakeFetcher:
    """Serves the search page, and a detail page for any listing we know."""

    def __init__(self, search_html, details=None, fail=None):
        self.search_html = search_html
        self.details = details or {}
        self.fail = fail
        self.urls = []

    def get(self, url, referer=None, allow_block=False):
        self.urls.append(url)
        if self.fail:
            raise self.fail
        for listing_id, html in self.details.items():
            if f"_{listing_id}_" in url:
                return Response(url=url, status=200, text=html, elapsed_ms=1)
        return Response(url=url, status=200, text=self.search_html, elapsed_ms=1)

    def get_bytes(self, *a, **k):
        return None

    def close(self):
        pass


class Capture(Notifier):
    name = "capture"

    def __init__(self):
        super().__init__({}, {}, {})
        self.digests = []
        self.alerts = []

    def _send(self, changes, run):
        self.digests.append(list(changes))
        return Result("capture", True)

    def _send_text(self, subject, body):
        self.alerts.append((subject, body))
        return Result("capture", True)


@pytest.fixture
def bench(tmp_path, monkeypatch, fixture_html, archive_html):
    """A working directory with a config, a fake site and a captured channel."""
    monkeypatch.chdir(tmp_path)
    cfg = Config.defaults(tmp_path / "config.json")
    cfg.add_search(SEARCH, "BMW M5")
    cfg.set("scraping.delay_ms", 0)
    cfg.set("scraping.retries", 0)
    cfg.set("archive.mode", "off")
    cfg.save()

    sink = Capture()
    real_dispatch, real_alert = notifiers.dispatch, notifiers.alert
    monkeypatch.setattr(runner_mod.notifiers, "dispatch",
                        lambda c, ch, r=None, e=None, n=None: real_dispatch(c, ch, r, e, [sink]))
    monkeypatch.setattr(runner_mod.notifiers, "alert",
                        lambda c, s, b, e=None, n=None: real_alert(c, s, b, e, [sink]))

    details = {i: archive_html(i) for i in ("13166607", "68819631", "13221555")}

    def go(search_html=None, fail=None, config=None):
        return run(config or cfg, State.load(tmp_path / "state.json"),
                   fetcher=FakeFetcher(search_html or fixture_html("search_cards"),
                                       details, fail))

    return type("Bench", (), {"cfg": cfg, "sink": sink, "run": staticmethod(go),
                              "path": tmp_path, "cards": fixture_html("search_cards")})


def test_a_first_run_finds_and_announces_every_car(bench):
    report = bench.run()
    assert report.ok and report.searches_run == 1
    assert report.listings_seen == 3 and report.new == 3
    assert len(bench.sink.digests) == 1
    assert len(bench.sink.digests[0]) == 3


def test_everything_arrives_as_one_digest_not_one_message_per_car(bench):
    """v1 sent an email and an SMS per listing, which is how you get your Gmail
    account rate-limited and your Twilio bill run up."""
    bench.run()
    assert len(bench.sink.digests) == 1


def test_running_again_on_an_unchanged_page_says_nothing(bench):
    bench.run()
    bench.sink.digests.clear()
    report = bench.run()
    assert report.new == 0 and report.price_drops == 0
    assert bench.sink.digests == []


def test_a_card_price_that_disagrees_with_the_listing_page_is_not_a_price_drop(bench):
    """The detail page is authoritative; a card that quotes a different number
    must not raise a false alarm."""
    bench.run()
    bench.sink.digests.clear()
    report = bench.run(search_html=bench.cards.replace("$102,199", "$94,000"))
    assert report.price_drops == 0
    assert bench.sink.digests == []


def test_a_real_price_drop_is_announced_once(bench, archive_html):
    bench.run()
    bench.sink.digests.clear()
    cheaper = {i: archive_html(i) for i in ("13166607", "13221555")}
    cheaper["68819631"] = (archive_html("68819631")
                           .replace('"price":"102199"', '"price":"94000"')
                           .replace('"price": "102199"', '"price": "94000"'))

    def go(cards):
        return run(bench.cfg, State.load(bench.path / "state.json"),
                   fetcher=FakeFetcher(cards, cheaper))

    report = go(bench.cards.replace("$102,199", "$94,000"))
    assert report.price_drops == 1
    change = bench.sink.digests[0][0]
    assert change.kind == Change.PRICE_DROP
    assert change.old_price == 102199 and change.new_price == 94000

    bench.sink.digests.clear()
    assert go(bench.cards.replace("$102,199", "$94,000")).price_drops == 0
    assert bench.sink.digests == []


def test_a_blocked_site_does_not_destroy_what_we_already_knew(bench):
    """This is the bug that broke v1: an exception before the single save at the
    end of the run threw away every id, so the next run announced them all."""
    bench.run()
    before = json.loads((bench.path / "state.json").read_text())["listings"]

    report = bench.run(fail=BlockedError("anti-bot page"))
    assert not report.ok and report.searches_failed == 1

    after = json.loads((bench.path / "state.json").read_text())["listings"]
    assert set(after) == set(before)

    # And the recovery run stays quiet rather than re-announcing everything.
    bench.sink.digests.clear()
    assert bench.run().new == 0
    assert bench.sink.digests == []


def test_a_network_failure_is_reported_not_raised(bench):
    report = bench.run(fail=FetchError("connection reset"))
    assert not report.ok
    assert "connection reset" in " ".join(report.errors)


def test_repeated_failures_raise_a_health_alert(bench):
    for _ in range(3):
        bench.run(fail=BlockedError("anti-bot page"))
    assert bench.sink.alerts, "the bot must say when it has stopped working"
    subject, body = bench.sink.alerts[-1]
    assert "needs attention" in subject.lower()
    assert "BMW M5" in body


def test_a_single_healthy_run_raises_no_alarm(bench):
    bench.run()
    assert bench.sink.alerts == []


def test_state_is_written_even_when_the_run_fails(bench):
    bench.run(fail=BlockedError("blocked"))
    saved = json.loads((bench.path / "state.json").read_text())
    assert saved["runs"], "the run must be recorded even after a failure"
    assert saved["runs"][0]["ok"] is False


def test_filtered_cars_are_never_announced(bench):
    bench.cfg.set("filters.max_price", 100000)
    report = bench.run()
    assert report.filtered_out >= 1
    for change in bench.sink.digests[0]:
        assert change.listing.price is None or change.listing.price <= 100000


def test_a_filtered_car_is_not_later_reported_as_removed(bench):
    """A car hidden by a filter is still present on the site."""
    bench.cfg.set("filters.max_price", 100000)
    bench.cfg.set("notifications.notify_on.removed", True)
    bench.run()
    bench.sink.digests.clear()
    for _ in range(3):
        report = bench.run()
    assert report.removed == 0


def test_quiet_hours_hold_alerts_instead_of_dropping_them(bench, monkeypatch):
    monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: True)
    report = bench.run()
    assert report.quiet and bench.sink.digests == []
    assert report.new == 3

    monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: False)
    bench.run()
    assert bench.sink.digests, "held changes must arrive after quiet hours end"


def test_dry_run_changes_nothing_on_disk(bench):
    state_file = bench.path / "state.json"
    report = run(bench.cfg, State.load(state_file),
                 fetcher=FakeFetcher(bench.cards), dry_run=True)
    assert report.new == 3
    assert not state_file.exists()
    assert bench.sink.digests == []


def test_a_run_with_no_searches_explains_itself(bench):
    cfg = Config.defaults(bench.path / "empty.json")
    report = run(cfg, State.load(bench.path / "s2.json"), fetcher=FakeFetcher(""))
    assert "No searches configured" in " ".join(report.warnings)


def test_pagination_stops_once_a_page_adds_nothing(bench):
    bench.cfg.set("scraping.max_pages", 5)
    fetcher = FakeFetcher(bench.cards)
    run(bench.cfg, State.load(bench.path / "state.json"), fetcher=fetcher)
    search_pages = [u for u in fetcher.urls if "/cars/" in u]
    # Page 2 repeats page 1's cars, so the crawl stops there rather than
    # fetching all five pages.
    assert len(search_pages) == 2


def test_detail_pages_are_only_fetched_for_cars_we_have_not_seen(bench):
    bench.run()
    fetcher = FakeFetcher(bench.cards, {})
    run(bench.cfg, State.load(bench.path / "state.json"), fetcher=fetcher)
    assert not [u for u in fetcher.urls if "/a/" in u]


def test_an_alert_survives_every_channel_being_down(bench, monkeypatch):
    """If Telegram is down when a car appears, the car must still be announced
    once Telegram comes back - not silently skipped."""
    class Dead(Notifier):
        name = "dead"
        def _send(self, changes, run): raise RuntimeError("service unavailable")

    real = notifiers.dispatch
    monkeypatch.setattr(runner_mod.notifiers, "dispatch",
                        lambda c, ch, r=None, e=None, n=None: real(c, ch, r, e, [Dead({}, {}, {})]))
    report = bench.run()
    assert report.new == 3

    monkeypatch.setattr(runner_mod.notifiers, "dispatch",
                        lambda c, ch, r=None, e=None, n=None: real(c, ch, r, e, [bench.sink]))
    bench.run()
    assert bench.sink.digests, "the held alert must be retried"
    assert len(bench.sink.digests[0]) == 3


def test_a_held_alert_is_only_delivered_once(bench, monkeypatch):
    monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: True)
    bench.run()
    monkeypatch.setattr(runner_mod.notifiers, "in_quiet_hours", lambda *a, **k: False)
    bench.run()
    bench.sink.digests.clear()
    bench.run()
    assert bench.sink.digests == []


def test_a_stable_card_price_never_triggers_a_second_detail_fetch(bench):
    """A card and its listing page may disagree permanently; that must not cost
    one extra request per car per run."""
    bench.run()
    for _ in range(3):
        fetcher = FakeFetcher(bench.cards, {})
        run(bench.cfg, State.load(bench.path / "state.json"), fetcher=fetcher)
        assert not [u for u in fetcher.urls if "/a/" in u]


def test_a_changed_card_price_does_trigger_a_re_check(bench):
    bench.run()
    fetcher = FakeFetcher(bench.cards.replace("$98,995", "$91,000"), {})
    run(bench.cfg, State.load(bench.path / "state.json"), fetcher=fetcher)
    assert [u for u in fetcher.urls if "_13166607_" in u]
