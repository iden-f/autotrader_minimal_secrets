"""What the bot costs, and refusing to cost more than it may.

Written after a week in which this bot held a GitHub runner for up to sixteen
hours a day and justified it with "the repository is public, so the minutes
are free". The minutes were free - GitHub reports zero billable milliseconds
against every one of those runs - and the reasoning was still wrong: the
exemption is a repository setting, not a property of the code, and nothing in
the repository would have noticed the day it changed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autotrader import budget
from autotrader.config import Config
from autotrader.state import State

SEPT = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config.defaults(tmp_path / "config.json")
    cfg.save()
    state = State(path=tmp_path / "state.json")
    # Default the tests to a repository that IS charged, so the arithmetic
    # below is about the arithmetic. The exemption has its own class.
    state.data["repo"] = {"visibility": "private"}
    return cfg, state


def month(days: dict[str, float], runs: int = 0) -> dict:
    return {"month": "2026-09", "days": days, "runs": runs}


class TestWhatGitHubCharges:
    """Every job is rounded up to a whole minute. Getting this wrong in the
    optimistic direction is how a guard reports fine while the bill says
    otherwise."""

    @pytest.mark.parametrize("seconds,expected", [
        (0, 1), (1, 1), (34, 1), (59, 1), (60, 1), (61, 2), (119, 2), (121, 3),
    ])
    def test_a_job_is_charged_by_the_whole_minute(self, seconds, expected):
        assert budget.minutes_for(seconds) == expected

    def test_two_jobs_cost_two_minutes_even_if_both_are_short(self):
        """The reason publishing was folded back into the check job."""
        assert budget.minutes_for(34, jobs=2) == 2
        assert budget.minutes_for(34, jobs=1) == 1

    def test_a_job_that_ran_at_all_is_never_free(self):
        assert budget.minutes_for(0.001) == 1


class TestTheLedger:
    def test_a_run_is_added_to_today(self, bench):
        cfg, state = bench
        budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert state.data["actions"]["days"]["2026-09-13"] == 1.0
        assert state.data["actions"]["runs"] == 1

    def test_runs_accumulate(self, bench):
        cfg, state = bench
        for _ in range(5):
            budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert state.data["actions"]["days"]["2026-09-13"] == 5.0

    def test_a_new_month_starts_from_zero(self, bench):
        cfg, state = bench
        state.data["actions"] = month({"2026-09-30": 2500.0}, runs=900)
        out = budget.record(state, 1.0, cfg=cfg,
                            now=datetime(2026, 10, 1, 0, 5, tzinfo=timezone.utc))
        assert out["used"] == 1.0 and out["month"] == "2026-10"

    def test_the_rate_is_per_day_of_data_not_per_day_of_month(self, bench):
        """A bot installed on the 20th has not used 19 days of allowance."""
        cfg, state = bench
        state.data["actions"] = month({"2026-09-12": 10.0, "2026-09-13": 10.0})
        out = budget.record(state, 10.0, cfg=cfg, now=SEPT)
        assert out["days_elapsed"] == 2
        assert out["per_day"] == 15.0


class TestTheProjection:
    def test_one_day_is_not_a_month(self, bench):
        cfg, state = bench
        out = budget.record(state, 400.0, cfg=cfg, now=SEPT)
        assert out["projected"] is None and out["state"] == "early"
        assert "too little to project" in out["text"]

    def test_the_real_situation_this_was_written_for(self, bench):
        """2,338 minutes, 12 days in, 3,000 allowed: over, and it says so."""
        cfg, state = bench
        state.data["actions"] = month(
            {f"2026-09-{d:02d}": 195.0 for d in range(1, 13)}, runs=500)
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["state"] == "over"
        assert out["projected"] > 3000
        assert "will stop before it gets there" in out["text"]

    def test_a_cheap_schedule_reads_as_fine(self, bench):
        cfg, state = bench
        state.data["actions"] = month(
            {f"2026-09-{d:02d}": 28.0 for d in range(1, 13)}, runs=150)
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["state"] == "ok"
        assert out["projected"] < 1000

    def test_spent_is_a_harder_state_than_projected_over(self, bench):
        cfg, state = bench
        state.data["actions"] = month(
            {f"2026-09-{d:02d}": 300.0 for d in range(1, 13)}, runs=900)
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["state"] == "stop" and out["should_stop"] is True

    def test_the_ceiling_is_below_the_allowance(self, bench):
        cfg, state = bench
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["ceiling"] < out["allowance"]

    def test_the_allowance_comes_from_config_not_from_code(self, bench):
        """GitHub Free is 2,000 a month, not 3,000."""
        cfg, state = bench
        cfg.set("budget.included_minutes", 2000)
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["allowance"] == 2000


class TestStoppingRatherThanSpending:
    """The guard writes a file the workflow reads before it installs anything.

    A file, not an API call to disable the workflow: it is visible in the
    repository, it survives a token with no actions: write, and deleting it is
    how a person says "I have looked at this and it is fine".
    """

    @pytest.fixture
    def watcher(self, tmp_path, monkeypatch, fixture_html):
        from autotrader import runner as runner_mod
        from autotrader.runner import run
        from .helpers import Capture, FakeFetcher, use_channels

        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        cfg.add_search("https://www.autotrader.ca/cars/bmw/m3/?rcp=25", "M3")
        cfg.set("scraping.delay_ms", 0)
        cfg.set("scraping.enrich_details", False)
        cfg.set("archive.mode", "off")
        cfg.set("dashboard.photos", False)
        cfg.save()
        sink = Capture()
        use_channels(monkeypatch, runner_mod, [sink])
        html = fixture_html("search_next_data")

        def go():
            return run(cfg, State.load(tmp_path / "state.json"),
                       fetcher=FakeFetcher(html), env={})

        return type("W", (), {"cfg": cfg, "sink": sink, "run": staticmethod(go),
                              "path": tmp_path,
                              "state": staticmethod(
                                  lambda: State.load(tmp_path / "state.json"))})

    def spend_the_month(self, watcher, per_day: float):
        state = watcher.state()
        now = datetime.now(timezone.utc)
        state.data["actions"] = {
            "month": f"{now.year:04d}-{now.month:02d}",
            "days": {f"{now.year:04d}-{now.month:02d}-{d:02d}": per_day
                     for d in range(1, max(2, now.day) + 1)},
            "runs": 400}
        state.save()

    def test_an_ordinary_run_leaves_no_stop_file(self, watcher):
        report = watcher.run()
        assert report.ok
        assert not (watcher.path / "BUDGET-STOP").exists()
        assert report.budget["charged"] >= 1

    def test_every_run_is_charged_to_the_month(self, watcher):
        watcher.run()
        first = watcher.state().data["actions"]["runs"]
        watcher.run()
        assert watcher.state().data["actions"]["runs"] == first + 1

    def test_a_spent_month_writes_the_stop_file(self, watcher):
        self.spend_the_month(watcher, 400.0)
        report = watcher.run()
        stop = watcher.path / "BUDGET-STOP"
        assert stop.exists(), "nothing stopped the bot spending past its allowance"
        text = stop.read_text()
        assert "stopped checking" in text
        assert "Delete this file" in text, "a guard with no way out is a trap"

    def test_it_says_so_out_loud_once(self, watcher):
        """Silently stopping is the same failure as silently spending."""
        self.spend_the_month(watcher, 400.0)
        watcher.run()
        subjects = [s for s, _ in watcher.sink.alerts]
        assert any("minutes are spent" in s for s in subjects), subjects
        before = len(watcher.sink.alerts)
        watcher.run()
        assert len(watcher.sink.alerts) == before, "told twice for the same month"

    def test_projected_over_warns_without_stopping(self, watcher):
        """Heading over is a warning; being over is a stop. Different things."""
        self.spend_the_month(watcher, 195.0)
        report = watcher.run()
        assert not (watcher.path / "BUDGET-STOP").exists()
        assert any("month ends at about" in w for w in report.warnings), report.warnings

    def test_the_stop_clears_itself_when_the_month_does(self, watcher):
        self.spend_the_month(watcher, 400.0)
        watcher.run()
        assert (watcher.path / "BUDGET-STOP").exists()
        state = watcher.state()
        state.data["actions"]["days"] = {"2026-09-01": 1.0}
        state.save()
        report = watcher.run()
        assert not (watcher.path / "BUDGET-STOP").exists()
        assert any("cleared" in w for w in report.warnings), report.warnings

    def test_accounting_never_fails_a_check(self, watcher, monkeypatch):
        """A photo may not fail a check and neither may a spreadsheet."""
        from autotrader import budget as budget_mod
        monkeypatch.setattr(budget_mod, "record",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        report = watcher.run()
        assert report.ok, report.errors


class TestTheWorkflowReadsIt:
    """The file is only a guard if something acts on it before spending."""

    def test_the_watcher_checks_for_the_stop_file_first(self):
        source = Path(".github/workflows/watch.yml").read_text()
        body = source[source.index("steps:"):]
        assert "BUDGET-STOP" in body
        assert body.index("BUDGET-STOP") < body.index("pip install"), \
            "the guard must run before anything is installed"

    def test_every_working_step_is_gated_on_the_guard(self):
        import yaml
        steps = yaml.safe_load(Path(".github/workflows/watch.yml").read_text()
                               )["jobs"]["check"]["steps"]
        for name in ("Install dependencies", "Check for new listings"):
            step = next(s for s in steps if s.get("name") == name)
            assert "guard.outputs.stop" in str(step.get("if", "")), name

    def test_the_stop_file_is_committed_so_it_survives_the_runner(self):
        """State is in git. A guard written to a runner's disk and thrown away
        with it would stop exactly one run."""
        source = Path(".github/workflows/watch.yml").read_text()
        save = source[source.index("- name: Save results"):]
        assert "BUDGET-STOP" in save[:save.index("git commit")]



class TestExemptIsNotTheSameAsFree:
    """Public repositories are not charged for Actions. This counted every
    minute as billable "to be conservative", which meant a guard that would
    shout about an allowance nothing was drawing on - and a warning that
    fires when nothing is wrong is a warning that gets muted."""

    def spent_month(self, state, per_day=195.0):
        state.data["actions"] = {
            "month": "2026-09",
            "days": {f"2026-09-{d:02d}": per_day for d in range(1, 13)},
            "runs": 500}

    def test_a_public_repo_is_counted_but_never_alarming(self, bench):
        cfg, state = bench
        state.data["repo"] = {"visibility": "public"}
        self.spent_month(state)
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["state"] == "exempt"
        assert out["should_stop"] is False
        assert out["charged"] is False
        assert out["used"] > 2000, "it still counts what it spends"
        assert "does not charge" in out["text"]

    def test_the_same_month_on_a_private_repo_is_a_problem(self, bench):
        cfg, state = bench
        state.data["repo"] = {"visibility": "private"}
        self.spent_month(state)
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["state"] == "over" and out["charged"] is True

    def test_not_knowing_means_assuming_you_are_charged(self, bench):
        """Being wrong that way costs a sentence. The other way costs money."""
        cfg, state = bench
        state.data.pop("repo", None)
        self.spent_month(state)
        assert budget.record(state, 1.0, cfg=cfg, now=SEPT)["charged"] is True

    def test_config_can_override_what_github_says(self, bench):
        cfg, state = bench
        state.data["repo"] = {"visibility": "public"}
        cfg.set("budget.charged", True)
        self.spent_month(state)
        assert budget.record(state, 1.0, cfg=cfg, now=SEPT)["state"] == "over"

    def test_an_exempt_repo_never_writes_the_stop_file(self, bench):
        cfg, state = bench
        state.data["repo"] = {"visibility": "public"}
        self.spent_month(state, per_day=400.0)
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert out["should_stop"] is False, "it would stop a bot costing nothing"

    def test_one_minute_reads_as_one_minute(self, bench):
        cfg, state = bench
        state.data["repo"] = {"visibility": "public"}
        out = budget.record(state, 1.0, cfg=cfg, now=SEPT)
        assert "1 runner minute this month" in out["text"], out["text"]
