"""The standing record of the first time each market event really happens.

Six hours of live watching produced no price drop, no removal and no
relisting - or so the run counters said. Two of them had in fact happened, on
cars the filters hide, where nothing counts them. This is what notices.
"""
import json

import pytest

from autotrader import events
from autotrader.listing import Listing
from autotrader.state import Change, State


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "EVENTS.md", tmp_path / "events.json"


def car(state, lid="1", price=100000, **kw):
    state.record(Listing(id=lid, url=f"https://www.autotrader.ca/offers/x-{lid}",
                         title="2022 BMW M5", price=price,
                         price_source="detail", search_id="s"), **kw)
    return state.listings[lid]


class TestFindingTheFirstOfEachKind:
    def test_a_price_coming_down_is_recorded_with_both_figures(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=139798)
        car(state, price=139399)
        state.mark_notified(["1"])

        record = events.update(state, *paths)
        drop = record["first"]["price_drop"]
        assert drop["before"] == 139798 and drop["after"] == 139399
        assert "$399 off" in drop["detail"]
        assert "delivered at" in drop["delivered"]

    def test_a_price_going_up_is_a_different_event(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=100000)
        car(state, price=104000)
        record = events.update(state, *paths)
        assert "price_rise" in record["first"]
        assert "price_drop" not in record["first"]

    def test_a_removal_records_when_it_was_last_seen(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state)
        state.mark_missing("s", set())
        state.mark_missing("s", set())

        gone = events.update(state, *paths)["first"]["removed"]
        assert gone["before"] == "active" and gone["after"] == "gone"
        assert "last seen" in gone["detail"]

    def test_a_car_coming_back_is_recorded(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state)
        state.mark_missing("s", set())
        state.mark_missing("s", set())
        car(state)                                  # it returns

        back = events.update(state, *paths)["first"]["relisted"]
        assert back["before"] == "gone" and back["after"] == "active"

    def test_a_call_for_price_car_naming_a_figure_is_recorded(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=None)
        car(state, price=175895)

        priced = events.update(state, *paths)["first"]["priced"]
        assert priced["before"] is None and priced["after"] == 175895
        assert "no price" in priced["detail"]

    def test_a_car_that_never_moved_produces_nothing(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state)
        car(state)
        record = events.update(state, *paths)
        assert record["first"] == {}
        assert sorted(record["waiting"]) == sorted(events.KINDS)


class TestWhatItRecordsAboutDelivery:
    def test_a_hidden_car_records_why_nothing_was_sent(self, tmp_path, paths):
        """The first real price drop this bot ever saw was on a hidden car."""
        state = State(path=tmp_path / "s.json")
        car(state, price=139798, filtered=True, filter_reason="above maximum")
        car(state, price=139399, filtered=True, filter_reason="above maximum")
        state.silence("1", "hidden by your rules: above maximum")

        drop = events.update(state, *paths)["first"]["price_drop"]
        assert "deliberately quiet" in drop["delivered"]
        assert "above maximum" in drop["delivered"]

    def test_an_undelivered_alert_says_it_is_queued(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        entry = car(state, price=100000)
        car(state, price=90000)
        state.defer([Change(Change.PRICE_DROP, Listing.from_dict(entry),
                            old_price=100000, new_price=90000)])

        assert "queued" in events.update(state, *paths)["first"]["price_drop"]["delivered"]

    def test_a_car_nobody_decided_about_is_called_out(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=100000)
        car(state, price=90000)
        for key in ("notified_at", "quiet_reason", "pending"):
            state.listings["1"].pop(key, None)

        assert "no record" in events.update(state, *paths)["first"]["price_drop"]["delivered"]


class TestTheLedgerItself:
    def test_the_first_sighting_is_kept_even_after_later_ones(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=100000)
        car(state, price=95000)
        first = events.update(state, *paths)["first"]["price_drop"]["at"]

        car(state, price=90000)
        again = events.update(state, *paths)["first"]["price_drop"]
        assert again["at"] == first
        assert again["after"] == 95000

    def test_it_says_what_is_still_missing(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=100000)
        car(state, price=95000)
        events.update(state, *paths)

        text = paths[0].read_text(encoding="utf-8")
        assert "still waiting" in text
        assert "a car left the market" in text

    def test_a_new_kind_is_flagged_as_new_exactly_once(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=100000)
        car(state, price=95000)
        assert events.update(state, *paths)["new_kinds"] == ["price_drop"]
        assert events.update(state, *paths)["new_kinds"] == []

    def test_a_corrupt_ledger_does_not_stop_it(self, tmp_path, paths):
        paths[1].parent.mkdir(parents=True, exist_ok=True)
        paths[1].write_text("{not json", encoding="utf-8")
        state = State(path=tmp_path / "s.json")
        car(state, price=100000)
        car(state, price=95000)
        assert "price_drop" in events.update(state, *paths)["first"]

    def test_history_imported_from_v1_is_not_an_event_it_watched(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        state.listings["old"] = {
            "id": "old", "status": "gone", "migrated_from": "v1-archive",
            "removed_at": "2025-08-12T02:41:06+00:00",
            "price_history": [{"at": "2025-01-01T00:00:00+00:00", "price": 120000},
                              {"at": "2025-06-01T00:00:00+00:00", "price": 110000}]}
        assert events.update(state, *paths)["first"] == {}


class TestItCannotDisturbTheBot:
    def test_it_never_writes_state(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        car(state, price=100000)
        car(state, price=95000)
        state.save()
        before = (tmp_path / "s.json").read_text(encoding="utf-8")

        events.update(state, *paths)
        assert (tmp_path / "s.json").read_text(encoding="utf-8") == before

    def test_it_reads_a_state_file_it_has_never_seen_before(self, tmp_path, paths):
        state = State(path=tmp_path / "s.json")
        record = events.update(state, *paths)
        assert record["first"] == {}
        assert json.loads(paths[1].read_text())["waiting"]
