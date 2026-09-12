"""Surviving a schedule that fires late, early, twice, or not at all.

Measured on this repository: a gap of 4 hours 46 minutes between two runs of
a schedule that asks for one every 30 minutes - nine dropped in a row - and,
earlier the same evening, two runs five minutes apart. Neither is a fault the
bot can fix. Both are faults it can notice.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from autotrader import events, runner as runner_mod
from autotrader.config import Config
from autotrader.runner import run
from autotrader.state import State

from .helpers import Capture, FakeFetcher, use_channels

SCHEDULED = {"AUTOTRADER_SCHEDULED": "1"}


@pytest.fixture
def bench(tmp_path, monkeypatch, fixture_html):
    monkeypatch.chdir(tmp_path)
    cfg = Config.defaults(tmp_path / "config.json")
    cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=25", "BMW M5")
    cfg.set("scraping.delay_ms", 0)
    cfg.set("scraping.enrich_details", False)
    cfg.set("archive.mode", "off")
    cfg.save()
    sink = Capture()
    use_channels(monkeypatch, runner_mod, [sink])
    html = fixture_html("search_next_data")

    def go(env=None, **kw):
        return run(cfg, State.load(tmp_path / "state.json"),
                   fetcher=FakeFetcher(html), env=dict(env or {}), **kw)

    return type("Bench", (), {
        "cfg": cfg, "sink": sink, "run": staticmethod(go), "path": tmp_path,
        "state": staticmethod(lambda: State.load(tmp_path / "state.json")),
    })


def age_last_run(bench, hours):
    """Rewrite history so the last successful run looks that old."""
    state = bench.state()
    when = datetime.now(timezone.utc) - timedelta(hours=hours)
    state.data["runs"][0]["at"] = when.isoformat(timespec="seconds")
    state.save()


class TestTheScheduleFiringTwice:
    def test_a_second_scheduled_run_minutes_later_stands_down(self, bench):
        bench.run()
        report = bench.run(SCHEDULED)

        assert report.skipped
        assert report.requests_made == 0
        assert any("checking the site twice" in w for w in report.warnings)

    def test_standing_down_is_not_recorded_as_a_run(self, bench):
        bench.run()
        before = len(bench.state().data["runs"])
        bench.run(SCHEDULED)
        assert len(bench.state().data["runs"]) == before

    def test_a_run_someone_asked_for_always_happens(self, bench):
        """Only a schedule is deduplicated. If you typed it, you want it."""
        bench.run()
        report = bench.run()
        assert not report.skipped and report.requests_made > 0

    def test_force_overrides_it(self, bench):
        bench.run()
        assert not bench.run(SCHEDULED, force=True).skipped

    def test_a_scheduled_run_after_the_interval_goes_ahead(self, bench):
        bench.run()
        age_last_run(bench, hours=1)
        assert not bench.run(SCHEDULED).skipped

    def test_it_can_be_switched_off(self, bench):
        bench.run()
        bench.cfg.set("health.min_interval_minutes", 0)
        bench.cfg.save()
        assert not bench.run(SCHEDULED).skipped


class TestAMissedWindow:
    def test_a_long_gap_is_noticed_and_measured(self, bench):
        bench.run()
        age_last_run(bench, hours=4.77)             # the real one, to the minute

        report = bench.run(SCHEDULED)
        assert not report.skipped
        assert report.missed_by == pytest.approx(286, abs=2)
        warning = next(w for w in report.warnings if "dropped" in w)
        assert "4.8 hours" in warning and "runs" in warning

    def test_an_ordinary_gap_says_nothing(self, bench):
        bench.run()
        age_last_run(bench, hours=0.5)
        report = bench.run(SCHEDULED)
        assert report.missed_by == 0
        assert not any("dropped" in w for w in report.warnings)

    def test_the_first_run_of_all_is_not_a_missed_window(self, bench):
        report = bench.run(SCHEDULED)
        assert report.missed_by == 0 and not report.skipped


class TestNoticingItsOwnSilence:
    """A watcher cannot report its own absence: the run that would tell you is
    the run that is not happening. A separate job asks, from the state file."""

    def _record(self, bench):
        return events.update(bench.state(), bench.path / "EVENTS.md",
                             bench.path / "events.json")

    def test_a_healthy_bot_raises_nothing(self, bench):
        bench.run()
        assert events.silence(bench.cfg, bench.state(), self._record(bench)) is None

    def test_a_long_silence_is_reported(self, bench):
        bench.run()
        age_last_run(bench, hours=5)

        alarm = events.silence(bench.cfg, bench.state(), self._record(bench))
        assert alarm and alarm["hours"] == pytest.approx(5, abs=0.1)
        assert "gone quiet" in alarm["subject"]
        assert "Nothing is being watched" in alarm["body"]

    def test_it_is_said_once_per_silence_not_once_an_hour(self, bench):
        bench.run()
        age_last_run(bench, hours=5)
        record = self._record(bench)
        alarm = events.silence(bench.cfg, bench.state(), record)

        record["silence_reported"] = alarm["since"]
        assert events.silence(bench.cfg, bench.state(), record) is None

    def test_a_bot_that_has_never_worked_is_a_different_alarm(self, bench, tmp_path):
        state = State(path=tmp_path / "empty.json")
        assert events.silence(bench.cfg, state, {}) is None

    def test_it_can_be_switched_off(self, bench):
        bench.run()
        age_last_run(bench, hours=9)
        bench.cfg.set("health.silent_after_hours", 0)
        assert events.silence(bench.cfg, bench.state(), self._record(bench)) is None

    def test_a_fresh_success_ends_the_silence(self, bench):
        bench.run()
        age_last_run(bench, hours=5)
        record = self._record(bench)
        record["silence_reported"] = events.silence(
            bench.cfg, bench.state(), record)["since"]

        bench.run()                                  # it is back
        assert events.silence(bench.cfg, bench.state(), record) is None
