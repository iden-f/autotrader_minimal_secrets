

class TestCoverageMeasuresTheWatchNotTheExitCode:
    """The number claims to answer "could a car have come and gone unseen".

    It was counting runs whose exit code was zero, which is a different
    question. For a day and a half one bookkeeping invariant tripped on every
    run - the run fetched both searches, read two hundred listings and wrote
    them all down, then exited 1 - and this figure read 18.8% while the site
    was in fact being read every time a check happened. It said the market
    was unwatched. It meant the ledger was inconsistent. Those need separate
    numbers, because they need different fixes.
    """

    @staticmethod
    def run_at(minutes_ago, **over):
        from datetime import datetime, timedelta, timezone
        row = {"at": (datetime.now(timezone.utc)
                      - timedelta(minutes=minutes_ago)).isoformat(),
               "ok": True, "searches_run": 2, "searches_failed": 0}
        row.update(over)
        return row

    def test_a_run_that_read_the_site_covers_its_slot(self):
        from autotrader import insight
        runs = [self.run_at(m, ok=False,
                            errors=["the bot's own bookkeeping is inconsistent"])
                for m in range(0, 240, 30)]
        cov = insight.coverage(runs, expected_minutes=30, window_hours=24)
        assert cov["successful"] == len(runs)
        assert cov["clean"] == 0
        assert cov["complained"] == len(runs)

    def test_a_run_whose_searches_all_failed_does_not(self):
        from autotrader import insight
        runs = [self.run_at(m, ok=False, searches_run=2, searches_failed=2)
                for m in range(0, 240, 30)]
        assert insight.coverage(runs, expected_minutes=30)["successful"] == 0

    def test_a_partial_failure_still_counts(self):
        """One search down is a narrower watch, not a blind one."""
        from autotrader import insight
        runs = [self.run_at(0, ok=False, searches_run=2, searches_failed=1)]
        assert insight.coverage(runs, expected_minutes=30)["successful"] == 1

    def test_a_skipped_run_never_looked(self):
        from autotrader import insight
        runs = [self.run_at(m, skipped=True) for m in range(0, 240, 30)]
        assert insight.coverage(runs, expected_minutes=30)["successful"] == 0

    def test_an_old_record_with_no_counters_falls_back_to_the_exit_code(self):
        from autotrader import insight
        runs = [{"at": self.run_at(10)["at"], "ok": True},
                {"at": self.run_at(40)["at"], "ok": False}]
        assert insight.coverage(runs, expected_minutes=30)["successful"] == 1

    def test_the_gap_is_measured_between_checks_that_looked(self):
        from autotrader import insight
        runs = [self.run_at(0), self.run_at(60, ok=False,
                                            errors=["bookkeeping"])]
        cov = insight.coverage(runs, expected_minutes=30, window_hours=24)
        # Not 24 hours: the complaining run an hour ago still read the site.
        assert cov["longest_gap_minutes"] < 24 * 60

    def test_the_complaints_are_reported_rather_than_absorbed(self):
        """Otherwise this change is just a nicer-looking number."""
        from pathlib import Path
        app = Path("docs/app.js").read_text()
        assert "cov.complained" in app, (
            "coverage no longer counts these against the percentage, so the "
            "page has to say how many there were")

    def test_two_checks_in_one_slot_cover_one_slot(self):
        """A burst of manual runs is not coverage the schedule delivered."""
        from autotrader import insight
        runs = [self.run_at(1), self.run_at(2), self.run_at(3), self.run_at(4)]
        cov = insight.coverage(runs, expected_minutes=30, window_hours=24)
        assert cov["checks"] == 4
        assert cov["slots_covered"] == 1
        assert cov["pct"] == round(1 / 48 * 100, 1)

    def test_a_check_every_slot_is_a_hundred_percent(self):
        from autotrader import insight
        runs = [self.run_at(m) for m in range(0, 24 * 60, 30)]
        cov = insight.coverage(runs, expected_minutes=30, window_hours=24)
        assert cov["pct"] == 100.0
