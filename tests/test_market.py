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
