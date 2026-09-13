"""What a car is called, everywhere a person reads it.

This exists because of one screenshot. The Feed, whose entire job is to be
readable at a glance, was forty rows of:

    2025 AutoTrader listing 01108a1a-2812-475d-b800-be530145cf6f

Nothing had failed. The parser writes that placeholder when a results card
carries no readable name, enrichment then fills in year, make, model and trim,
and nothing ever goes back to fix the title. It showed up on hidden cars
because those are deliberately never enriched from their own detail pages - so
the rows a person scrolls past fastest were the ones named worst.
"""

from __future__ import annotations

from autotrader import insight
from autotrader.config import Config
from autotrader.dashboard import build_payload
from autotrader.listing import Listing, name_of
from autotrader.state import State


class TestNamingACarFromAStateRow:
    def test_a_placeholder_is_replaced_by_what_we_know(self):
        assert name_of({"id": "01108a1a", "title": "AutoTrader listing 01108a1a",
                        "year": 2025, "make": "BMW", "model": "M4"}) == "2025 BMW M4"

    def test_a_real_title_keeps_the_dealers_words(self):
        """Dealer titles carry detail no reconstruction has - but only up to
        the first pipe, after which it is a feature list, and with the year in
        front because a card without one is a car of unknown age."""
        assert name_of({"id": "x", "year": 2019, "make": "BMW", "model": "M4",
                        "title": "BMW M4 Competition | Carbon roof | One owner"
                        }) == "2019 BMW M4 Competition"

    def test_a_title_that_already_opens_with_the_year_gets_no_second_one(self):
        assert name_of({"id": "x", "year": 2018, "make": "BMW", "model": "M3",
                        "title": "2018 BMW M3 Competition"
                        }) == "2018 BMW M3 Competition"

    def test_a_car_we_know_nothing_about_keeps_its_placeholder(self):
        """Better an id than a confident lie about which car this is."""
        row = {"id": "x", "title": "AutoTrader listing x"}
        assert name_of(row) == "AutoTrader listing x"

    def test_the_trim_is_included_when_it_reads_as_a_name(self):
        assert name_of({"id": "x", "title": "", "year": 2020, "make": "BMW",
                        "model": "X3", "trim": "M Competition"}) == \
            "2020 BMW X3 M Competition"


class TestTrimsDealersActuallyType:
    def name(self, trim: str) -> str:
        return Listing(id="x", year=2019, make="BMW", model="M4",
                       trim=trim).display_title

    def test_a_capital_i_used_as_a_pipe_is_a_separator(self):
        """"M4 I Premium PKG I M Carbon Exterior PKG" is one real card."""
        assert self.name("Competition I Premium PKG I M Carbon Exterior") == \
            "2019 BMW M4 Competition"

    def test_a_long_trim_is_cut_at_a_word(self):
        """"...M Carbon Exterior PKG Ca" read as a rendering fault, and was."""
        out = self.name("xDrive30i Premium Enhanced Package With Every Option Listed")
        assert len(out) <= 60 and not out.endswith(("Ca", "Op", "List"))
        assert out.split()[-1] in {"With", "Every", "Package", "Enhanced", "Option"}

    def test_a_trim_that_starts_on_its_own_separator(self):
        """Real: trim "I Premium PKG I M Carbon Exterior PKG Carbon Fibre",
        which named a car "2025 BMW M4 I Premium PKG"."""
        assert self.name("I Premium PKG I M Carbon Exterior PKG") == \
            "2019 BMW M4 Premium PKG"

    def test_a_leading_pipe_is_not_part_of_the_name(self):
        assert self.name("| Competition | Carbon roof") == "2019 BMW M4 Competition"

    def test_a_trim_that_merely_starts_with_i_is_not_mangled(self):
        assert self.name("I6 Turbo") == "2019 BMW M4 I6 Turbo"

    def test_a_trim_that_is_just_the_model_again_is_dropped(self):
        """Real row: make BMW, model X3, trim X3 - "2010 BMW X3 X3"."""
        assert Listing(id="x", year=2010, make="BMW", model="X3",
                       trim="X3").display_title == "2010 BMW X3"

    def test_a_star_separated_feature_list_is_cut_at_the_first_star(self):
        """Real trim: "XDrive30i * NO ACCIDENTS * ONE OWNER * CERTIFIED"."""
        assert self.name("XDrive30i * NO ACCIDENTS * ONE OWNER") == \
            "2019 BMW M4 XDrive30i"

    def test_a_short_trim_is_untouched(self):
        assert self.name("Competition") == "2019 BMW M4 Competition"

    def test_a_trim_with_no_spaces_is_still_cut_rather_than_dropped(self):
        assert self.name("A" * 80).startswith("2019 BMW M4 A")


class TestEverywhereItIsRead:
    def bench(self, tmp_path):
        cfg = Config.defaults(tmp_path / "config.json")
        search = cfg.add_search("https://www.autotrader.ca/cars/bmw/m4/?rcp=25", "M4")
        state = State(path=tmp_path / "state.json")
        state.record(Listing(id="nameless", title="AutoTrader listing nameless",
                             year=2025, make="BMW", model="M4", price=108999,
                             price_source="search", search_id=search.id),
                     filtered=True, filter_reason="year 2025 above maximum 2020")
        return cfg, state

    def test_the_feed_never_shows_a_placeholder(self, tmp_path):
        _, state = self.bench(tmp_path)
        events = insight.events(state.listings.values())
        assert events and all(not e["title"].startswith("AutoTrader listing")
                              for e in events), [e["title"] for e in events]
        assert events[0]["title"] == "2025 BMW M4"

    def test_the_published_listing_never_shows_one_either(self, tmp_path):
        cfg, state = self.bench(tmp_path)
        payload = build_payload(cfg, state, {})
        assert payload["listings"][0]["title"] == "2025 BMW M4"

    def test_state_keeps_what_was_really_parsed(self, tmp_path):
        """The published name is a presentation choice; the record is a record."""
        _, state = self.bench(tmp_path)
        assert state.listings["nameless"]["title"] == "AutoTrader listing nameless"


class TestThePageAndTheAlertAgree:
    """The naming bug was a fork in the road, not a broken function.

    render.py builds a notification from a Listing and reads
    ``listing.display_title``, which composes a name from year, make and model
    - so alerts were always right. dashboard.py and insight.py build the page
    from state ROWS, which are dicts, and read ``entry["title"]`` - so the
    page was always wrong. Two answers to "what is this car called", one of
    them correct, and the difference invisible because nobody compares a push
    notification to a web page.
    """

    def car(self) -> Listing:
        return Listing(id="01108a1a-2812-475d-b800-be530145cf6f",
                       title="AutoTrader listing 01108a1a-2812-475d-b800-be530145cf6f",
                       year=2025, make="BMW", model="M4", price=108999,
                       price_source="detail")

    def test_they_produce_the_same_name(self):
        listing = self.car()
        assert name_of(listing.to_dict()) == listing.display_title

    def test_and_for_a_car_with_a_real_title(self):
        listing = Listing(id="x", title="BMW M4 Competition | One owner",
                          year=2019, make="BMW", model="M4")
        assert name_of(listing.to_dict()) == listing.display_title

    def test_and_for_a_car_nothing_is_known_about(self):
        listing = Listing(id="deadbeef", title="AutoTrader listing deadbeef")
        assert name_of(listing.to_dict()) == listing.display_title

    def test_the_notification_never_said_the_placeholder(self, tmp_path):
        """Worth pinning: the alerts were the half that was right."""
        from autotrader import render
        from autotrader.state import Change
        text = render.as_text([Change(kind=Change.NEW, listing=self.car())])
        assert "AutoTrader listing" not in text, text
        assert "2025 BMW M4" in text, text
