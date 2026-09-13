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
        """Relative to the configured floor, not to a number typed in 2026.

        This said hours=1, which was comfortably past a 24-minute floor and is
        inside a 90-minute one. The test was measuring the schedule the bot
        had when it was written rather than the one it has.
        """
        bench.run()
        floor = int(bench.cfg.get("health.min_interval_minutes"))
        age_last_run(bench, hours=(floor + 10) / 60)
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
        assert "4.8 hours" in warning
        # The count itself is the runner's arithmetic, not this test's: a
        # second copy of that formula here would drift from it silently. What
        # is asserted is that it is a real number of whole runs, and reads as
        # English for one of them.
        import re
        expected = int(bench.cfg.get("health.expected_interval_minutes"))
        dropped = re.search(r"dropped (\d+) runs?\b", warning)
        assert dropped, warning
        assert 1 <= int(dropped.group(1)) <= 286 // expected, warning
        assert ("dropped 1 run." in warning) == (int(dropped.group(1)) == 1)

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


class TestTheScrapingRateIsWhatWasAskedFor:
    """The floor exists so a schedule cannot become load on somebody's site.

    It used to guard against three pacemakers dispatching on nine offset
    minutes; they are gone, and the floor still matters for the same reason -
    a dispatch, a push and a cron can all land within a minute of each other.
    """

    @pytest.mark.parametrize("where", ["defaults", "live"])
    def test_the_floor_is_most_of_the_interval(self, where):
        """Both the default and the config.json this repository actually runs.

        Checking only the default is how the first version of this passed
        while the bot hammered the site every twelve minutes. config.json is
        materialised from the defaults once, at setup, and then owns its own
        copy - so changing a default changes nothing for an installed bot.
        """
        from pathlib import Path
        from autotrader.config import Config
        if where == "live":
            if not Path("config.json").exists():
                pytest.skip("no live config in this checkout")
            cfg = Config.load("config.json")
        else:
            cfg = Config.defaults()
        floor = int(cfg.get("health.min_interval_minutes"))
        expected = int(cfg.get("health.expected_interval_minutes"))
        assert floor >= expected * 0.6, (
            f"a {floor}-minute floor under a {expected}-minute schedule lets "
            f"a dispatch and a cron check {expected // floor}x as often as "
            f"asked - that is somebody else's site")
        assert floor < expected, (
            "a floor at or above the interval would skip the checks the "
            "schedule actually asked for")


class TestAnOutsideTimerCannotDoubleScrape:
    """The recommended setup is an external cron BESIDE GitHub's own, so the
    bot is asked for a check more often than it should make one. That is the
    design - two timers means one can fail - and it only works while the
    floor holds.

    An hourly external timer plus '11,41 */2 * * *' asks for a check up to
    four times per two-hour window. The site must see one.
    """

    def test_the_floor_is_below_the_interval_and_above_a_burst(self):
        from pathlib import Path
        from autotrader.config import Config
        cfg = (Config.load("config.json") if Path("config.json").exists()
               else Config.defaults())
        interval = int(cfg.get("health.expected_interval_minutes"))
        floor = float(cfg.get("health.min_interval_minutes") or interval / 3.0)
        assert floor < interval, "a floor at or above the interval skips real checks"
        assert floor >= 60, (
            f"a {floor:.0f}-minute floor lets an hourly outside timer scrape "
            f"every hour against a {interval}-minute schedule")

    @pytest.mark.parametrize("how", ["schedule", "repository_dispatch",
                                     "repository_dispatch:cron-job.org"])
    def test_every_timer_is_deduplicated(self, how):
        """A dispatch that is not treated as scheduled is not deduplicated,
        and an external timer would scrape on every firing."""
        from autotrader.runner import _trigger_of
        env = {"RUN_TRIGGER": how.split(":")[0]}
        if ":" in how:
            env["RUN_TRIGGER_FROM"] = how.split(":", 1)[1]
        assert _trigger_of(env) == how

    def test_the_workflow_marks_an_outside_timer_as_scheduled(self):
        """AUTOTRADER_SCHEDULED is what turns the floor on. A repository
        dispatch missing from that expression is an external timer that
        scrapes every single firing."""
        import yaml
        from pathlib import Path
        doc = yaml.safe_load(Path(".github/workflows/watch.yml").read_text())
        step = [s for s in doc["jobs"]["check"]["steps"] if s.get("id") == "bot"][0]
        marker = step["env"]["AUTOTRADER_SCHEDULED"]
        assert "repository_dispatch" in marker
        assert "schedule" in marker

    def test_a_named_outside_timer_still_counts_as_a_schedule(self):
        """The name must not stop it counting. Matching the whole string
        would have silently reclassified every external timer the moment it
        started saying who it was."""
        from autotrader.insight import _is_a_schedule
        assert _is_a_schedule("repository_dispatch:cron-job.org")
        assert _is_a_schedule("repository_dispatch")
        assert _is_a_schedule("schedule")
        assert not _is_a_schedule("push")
        assert not _is_a_schedule("workflow_dispatch")
