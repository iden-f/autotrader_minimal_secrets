"""The market layer, and how honest it is about how little it knows.

Two hundred listings over a few days is a snapshot, not a time series. The
danger in this whole feature is a number that reads like a trend - "median
asking up $1,200" - computed from ten cars seen twice. Every test here is
about the numbers being right, or about them refusing to be stated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autotrader import insight

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


def runs_since(days: float) -> list[dict]:
    """A run log whose oldest entry is where the watch began."""
    return [{"at": ago(days)}, {"at": ago(days / 2)}, {"at": ago(0)}]


def car(i, *, price=90000, year=2019, trim="", status="active",
        first=30, removed=None, history=None, title=""):
    entry = {
        "id": f"{i:08d}-0000-0000-0000-000000000000",
        "price": price, "year": year, "trim": trim, "title": title,
        "status": status, "first_seen": ago(first),
        "price_history": history if history is not None
        else [{"at": ago(first), "price": price}],
    }
    if removed is not None:
        entry["removed_at"] = ago(removed)
    return entry


class TestPriceByYear:
    def test_a_year_with_enough_cars_gets_a_median(self):
        cars = [car(i, year=2019, price=p) for i, p in
                enumerate([60000, 70000, 80000, 90000, 100000])]
        out = insight.market(cars, now=NOW)
        assert out["by_year"]["2019"]["n"] == 5
        assert out["by_year"]["2019"]["median"] == 80000
        assert out["by_year"]["2019"]["low"] == 60000

    def test_a_year_with_two_cars_does_not(self):
        """Two cars is not a median, it is two cars."""
        out = insight.market([car(1, year=2021), car(2, year=2021)], now=NOW)
        assert "2021" not in out["by_year"]

    def test_trims_are_grouped_from_the_words_that_move_the_price(self):
        cars = ([car(i, title="BMW M5 Competition xDrive", price=95000)
                 for i in range(4)]
                + [car(10 + i, title="BMW M5 Touring", price=170000)
                   for i in range(4)])
        out = insight.market(cars, now=NOW)
        assert out["by_trim"]["competition"]["median"] == 95000
        assert out["by_trim"]["touring"]["median"] == 170000


class TestHowLongThingsLast:
    def test_it_measures_time_listed_not_time_to_sell(self):
        cars = [car(i, status="gone", first=40, removed=10) for i in range(6)]
        out = insight.market(cars, now=NOW)
        assert out["listed_days"]["median"] == 30
        assert "not how long it took to sell" in out["listed_days"]["note"]

    def test_cars_still_up_are_counted_separately(self):
        out = insight.market([car(i, first=12) for i in range(5)], now=NOW)
        assert out["still_listed_days"]["median"] == 12
        assert out["listed_days"]["n"] == 0


class TestSayingHowLittleItKnows:
    def test_a_thin_window_is_flagged_and_explained(self):
        out = insight.market([car(i, first=2) for i in range(50)], now=NOW)
        assert out["window"]["thin"] is True
        assert out["window"]["cars_with_two_prices"] == 0
        assert "snapshot" in out["window"]["note"]

    def test_a_long_window_with_real_history_is_not_flagged(self):
        cars = [car(i, first=90, history=[{"at": ago(90), "price": 90000},
                                          {"at": ago(10), "price": 88000}])
                for i in range(25)]
        out = insight.market(cars, now=NOW)
        assert out["window"]["thin"] is False
        assert out["window"]["cars_with_two_prices"] == 25


class TestCompaction:
    def test_recent_points_are_all_kept(self):
        history = [{"at": ago(d), "price": 90000 - d} for d in range(20, 0, -1)]
        out = insight.compact_history(history, now=NOW)
        assert out == history

    def test_old_points_are_thinned_to_one_a_week(self):
        """Thirty days kept daily, then one a week. Roughly a quarter."""
        history = [{"at": ago(d), "price": 90000} for d in range(300, 0, -1)]
        out = insight.compact_history(history, now=NOW)
        daily = insight.KEEP_DAILY_DAYS
        weekly = (300 - daily) / 7
        assert daily <= len(out) <= daily + weekly + 4, len(out)
        assert len(out) < len(history) / 3

    def test_the_endpoints_always_survive(self):
        """Losing one moves the numbers the history exists to give you."""
        history = [{"at": ago(d), "price": 100000 + d} for d in range(400, 0, -1)]
        out = insight.compact_history(history, now=NOW)
        assert out[0] == history[0]
        assert out[-1] == history[-1]

    def test_a_short_history_is_left_alone(self):
        history = [{"at": ago(300), "price": 1}, {"at": ago(1), "price": 2}]
        assert insight.compact_history(history, now=NOW) == history


class TestTheScoreIsTested:
    def test_it_refuses_a_verdict_on_too_few_cars(self):
        out = insight.backtest([car(i) for i in range(4)])
        assert "not enough scored cars" in out["verdict"]

    def test_it_says_plainly_when_the_score_predicts_nothing(self):
        """A score nobody has checked is decoration."""
        cars = []
        # Ten cheap ones that all cut, ten dear ones that never do: the
        # opposite of what the score claims.
        for i in range(10):
            cars.append(car(i, price=60000, year=2019, title="BMW M5",
                            history=[{"at": ago(20), "price": 70000},
                                     {"at": ago(1), "price": 60000}]))
        for i in range(10):
            cars.append(car(100 + i, price=140000, year=2019, title="BMW M5",
                            history=[{"at": ago(20), "price": 140000}]))
        for entry in cars:
            entry["make"], entry["model"] = "BMW", "M5"
        out = insight.backtest(cars)
        assert out["called_cheap"] and out["called_dear"]
        assert "not predicting anything" in out["verdict"], out


class TestNotConfusingTheWatchWithTheMarket:
    """The failure this class exists to prevent, in one sentence.

    A bot that started watching on Tuesday reports that every car on the
    market arrived this week, that the median car has been listed two days,
    and that listings come down within a day of going up. All three are
    arithmetically correct and all three are statements about the bot. The
    market layer has to hand the page enough to say so.
    """

    def test_every_car_still_here_arrived_when_the_watch_did(self):
        """Right-censored: the figure is a floor, not a measurement."""
        fleet = [car(i, first=2) for i in range(30)]
        out = insight.market(fleet, now=NOW, runs=runs_since(2))
        assert out["still_listed_days"]["median"] == 2
        assert out["still_listed_days"]["censored"] is True
        assert out["still_listed_days"]["watching_days"] == 2

    def test_a_watch_older_than_its_oldest_car_is_measuring_the_market(self):
        """The bug this replaced: comparing a number against itself.

        Deriving "how long have we been watching" from the oldest car makes
        the censoring test read ``longest >= longest``, which is true for
        every dataset ever collected. The watch's own start comes from the
        run log, which is the only record of it.
        """
        fleet = [car(i, first=3) for i in range(10)]
        out = insight.market(fleet, now=NOW, runs=runs_since(200))
        assert out["still_listed_days"]["censored"] is False
        assert out["still_listed_days"]["watching_days"] == 200

    def test_with_no_run_log_it_falls_back_rather_than_crashing(self):
        fleet = [car(i, first=2) for i in range(5)]
        out = insight.market(fleet, now=NOW)
        assert out["still_listed_days"]["censored"] is True

    def test_a_young_watch_says_its_arrivals_are_not_a_week(self):
        out = insight.market([car(i, first=2) for i in range(5)], now=NOW,
                             runs=runs_since(2))
        assert out["velocity"]["window_is_the_watch"] is True

    def test_a_watch_older_than_a_week_means_this_week_means_this_week(self):
        fleet = [car(i, first=40) for i in range(5)]
        out = insight.market(fleet, now=NOW, runs=runs_since(90))
        assert out["velocity"]["window_is_the_watch"] is False
        assert out["velocity"]["arrived_7d"] == 0

    def test_only_short_lives_can_finish_inside_a_short_watch(self):
        """Length-biased sampling, flagged rather than silently reported."""
        fleet = [car(i, first=2) for i in range(10)]
        fleet += [car(50 + i, first=2, removed=1, status="gone")
                  for i in range(3)]
        out = insight.market(fleet, now=NOW, runs=runs_since(2))
        assert out["listed_days"]["n"] == 3
        assert out["listed_days"]["biased_short"] is True

    def test_a_long_watch_drops_the_warning(self):
        fleet = [car(i, first=400) for i in range(10)]
        fleet += [car(50 + i, first=300, removed=100, status="gone")
                  for i in range(3)]
        out = insight.market(fleet, now=NOW, runs=runs_since(400))
        assert out["listed_days"]["biased_short"] is False


class TestWordsAPersonWouldWrite:
    def test_one_day_is_not_one_days(self):
        assert insight._days(1) == "1 day"
        assert insight._days(2) == "2 days"
        assert insight._days(0) == "0 days"

    def test_no_programmer_pluralisation_reaches_the_page(self):
        out = insight.market([car(i, first=1) for i in range(3)], now=NOW,
                             runs=runs_since(1))
        assert "(s)" not in out["window"]["note"], out["window"]["note"]


class TestTheWatchStartSurvivesTheRunLog:
    """The run log is a rolling sixty; the watch start is not.

    At eighteen checks a day the log holds three days. A bot that reads its
    own start date off the end of that window reports "watching for 3 days"
    on its first anniversary, and every censored figure downstream stays
    wrong forever while looking freshly computed.
    """

    def test_the_first_run_writes_it_and_later_runs_leave_it_alone(self):
        from autotrader.state import State
        st = State()
        st.record_run({"ok": True})
        first = st.data["watch_started"]
        assert first
        st.record_run({"ok": True})
        assert st.data["watch_started"] == first

    def test_an_old_state_with_no_marker_falls_back_to_the_log(self):
        from autotrader.state import State
        st = State({"version": 2, "runs": [{"at": ago(9)}, {"at": ago(1)}]})
        assert st.watch_started == ago(9)

    def test_a_rolled_over_log_does_not_move_the_start(self):
        from autotrader.state import State
        st = State({"version": 2, "watch_started": ago(400),
                    "runs": [{"at": ago(1)}]})
        out = insight.market([car(1, first=1)], now=NOW, runs=st.runs,
                             since=st.watch_started)
        assert out["still_listed_days"]["watching_days"] == 400
        assert out["still_listed_days"]["censored"] is False
        assert out["velocity"]["window_is_the_watch"] is False

    def test_without_the_marker_the_same_state_would_have_lied(self):
        """The regression, stated as the difference it makes."""
        out = insight.market([car(1, first=1)], now=NOW, runs=[{"at": ago(1)}])
        assert out["still_listed_days"]["watching_days"] == 1
