"""The dashboard is published to a public URL, so nothing secret may reach it."""
import json

import pytest

from autotrader.config import Config
from autotrader.dashboard import build_payload, find_secrets, write
from autotrader.listing import Listing
from autotrader.state import State

# Credential-shaped strings for testing the detector. They are assembled at
# runtime rather than written out: a literal Twilio-shaped SID in a source file
# trips GitHub's push protection, which blocks the push outright even though
# the value is invented.
FAKE_CREDENTIALS = {
    "telegram": lambda: "8123456789" + ":" + "AAH2x-" + "a1b2c3" * 5,
    "discord": lambda: "https://discord" + ".com/api/webhooks/123456789/" + "x" * 20,
    "slack": lambda: "https://hooks.slack" + ".com/services/T00000000/B00000/" + "y" * 16,
    "twilio_sid": lambda: "A" + "C" + "0123456789abcdef" * 2,
    "twilio_key": lambda: "S" + "K" + "fedcba9876543210" * 2,
}


def _payload(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15", "M5")
    state = State(path=tmp_path / "s.json")
    state.record(Listing(id="1", url="https://www.autotrader.ca/a/x/19_1_/",
                         title="2021 BMW M5", price=100000,
                         price_source="detail", search_id=cfg.searches[0].id))
    return cfg, state


class TestSecretDetection:
    def test_a_normal_payload_is_clean(self, tmp_path):
        cfg, state = _payload(tmp_path)
        assert find_secrets(build_payload(cfg, state, {})) == []

    def test_secret_names_in_help_text_are_not_flagged(self, tmp_path):
        """data.json says "TELEGRAM_BOT_TOKEN" as the name of a secret to set."""
        cfg, state = _payload(tmp_path)
        payload = build_payload(cfg, state, {})
        assert "TELEGRAM_BOT_TOKEN" in json.dumps(payload)
        assert find_secrets(payload) == []

    @pytest.mark.parametrize("where", ["token", "password", "api_key", "auth"])
    def test_a_credential_under_a_telltale_key_is_caught(self, where):
        assert find_secrets({"config": {where: "some-value"}})

    @pytest.mark.parametrize("shape", list(FAKE_CREDENTIALS))
    def test_a_credential_under_an_innocent_key_is_still_caught(self, shape):
        assert find_secrets({"notes": FAKE_CREDENTIALS[shape]()}), shape

    def test_an_empty_credential_field_is_not_a_leak(self):
        assert find_secrets({"token": "", "password": None}) == []

    def test_ordinary_listing_text_is_not_flagged(self):
        assert find_secrets({"title": "2021 BMW M5 - ask about the token"}) == []
        assert find_secrets({"help": "open bot<TOKEN>/getUpdates"}) == []

    def test_the_writer_refuses_to_publish_a_leak(self, tmp_path):
        cfg, state = _payload(tmp_path)
        cfg.set("notifications.channels.webhook.url", FAKE_CREDENTIALS["discord"]())
        with pytest.raises(ValueError, match="credential-shaped"):
            write(cfg, state, {}, tmp_path / "docs" / "data.json")

    def test_a_clean_payload_writes_normally(self, tmp_path):
        cfg, state = _payload(tmp_path)
        path = write(cfg, state, {}, tmp_path / "docs" / "data.json")
        assert json.loads(path.read_text())["listings"]


class TestPayloadShape:
    def test_channels_report_presence_never_values(self, tmp_path):
        cfg, state = _payload(tmp_path)
        payload = build_payload(cfg, state, {"TELEGRAM_BOT_TOKEN": "supersecret",
                                             "TELEGRAM_CHAT_ID": "123"})
        assert "supersecret" not in json.dumps(payload)
        assert payload["channels"]["telegram"]["active"] is True

    def test_bare_imported_ids_are_not_published(self, tmp_path):
        cfg, state = _payload(tmp_path)
        state.listings["999"] = {"id": "999", "imported_from": "seen_listings.json"}
        ids = {l["id"] for l in build_payload(cfg, state, {})["listings"]}
        assert "999" not in ids

    def test_the_listing_cap_is_respected(self, tmp_path):
        cfg, state = _payload(tmp_path)
        cfg.set("dashboard.max_listings", 2)
        for i in range(10):
            state.record(Listing(id=str(100 + i), url=f"https://x/a/19_{100+i}_/",
                                 title=f"Car {i}", price=1000 * i,
                                 price_source="detail", search_id="s"))
        assert len(build_payload(cfg, state, {})["listings"]) == 2


class TestTheBotsOwnState:
    """The dashboard showed cars and nothing else.

    Every question about whether the watcher was still working - is a parser
    failing? did a channel die? why does it show fewer cars than the site? -
    had to be answered by reading state.json by hand.
    """

    def _bench(self, tmp_path):
        cfg = Config.defaults(tmp_path / "c.json")
        cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15", "M5")
        sid = cfg.searches[0].id
        state = State(path=tmp_path / "s.json")
        state.record(Listing(id="keep", url="https://www.autotrader.ca/offers/a-" + "0" * 8
                             + "-1111-2222-3333-444444444444",
                             title="2021 BMW M5", price=100000, search_id=sid))
        state.record(Listing(id="ask", url="https://www.autotrader.ca/offers/b-" + "1" * 8
                             + "-1111-2222-3333-444444444444",
                             title="2022 BMW M5", price=None, search_id=sid))
        state.record(Listing(id="nope", url="https://www.autotrader.ca/offers/c-" + "2" * 8
                             + "-1111-2222-3333-444444444444",
                             title="2023 BMW M5", price=400000, search_id=sid),
                     filtered=True, filter_reason="price $400,000 above maximum $150,000")
        return cfg, state, sid

    def test_hidden_cars_are_published_with_the_reason_they_were_hidden(self, tmp_path):
        cfg, state, _ = self._bench(tmp_path)
        payload = build_payload(cfg, state, env={})

        hidden = [l for l in payload["listings"] if l["filtered"]]
        assert [l["id"] for l in hidden] == ["nope"]
        assert "above maximum" in hidden[0]["filter_reason"]

    def test_a_hidden_car_carries_no_photos_or_history(self, tmp_path):
        """It has to be countable and explainable, not browsable."""
        cfg, state, _ = self._bench(tmp_path)
        hidden = next(l for l in build_payload(cfg, state, env={})["listings"]
                      if l["filtered"])
        assert "images" not in hidden
        assert len(hidden["price_history"]) <= 2

    def test_counts_separate_live_hidden_and_call_for_price(self, tmp_path):
        cfg, state, sid = self._bench(tmp_path)
        payload = build_payload(cfg, state, env={})

        assert payload["health"]["counts"] == {
            "active": 2, "filtered": 1, "unpriced": 1, "gone": 0, "total": 3}
        assert payload["searches"][0]["counts"]["active"] == 2
        assert payload["searches"][0]["counts"]["filtered"] == 1

    def test_the_whole_parser_ladder_is_reported_not_just_the_winner(self, tmp_path):
        """Two of four scoring is the warning that matters, and it is only
        visible if the ones scoring zero are named."""
        cfg, state, sid = self._bench(tmp_path)
        state.record_shape(sid, {
            "strategy": "jsonld",
            "scores": {"jsonld": 20, "embedded_json": 20, "anchors": 0, "regex": 0},
            "working": ["embedded_json", "jsonld"],
            "markers": {"offer_links": "many"},
        })
        ladder = build_payload(cfg, state, env={})["health"]["strategies"][sid]

        assert ladder["winner"] == "jsonld"
        assert ladder["order"] == ["jsonld", "embedded_json", "anchors", "regex"]
        assert ladder["of"] == 4
        assert ladder["scores"]["anchors"] == 0

    def test_a_run_that_drifted_says_so(self, tmp_path):
        cfg, state, _ = self._bench(tmp_path)
        state.record_run({"ok": True, "shape_drift": [
            {"search": "M5", "reasons": ["the winning strategy changed"], "serious": True}]})
        health = build_payload(cfg, state, env={})["health"]

        assert health["drift"][0]["serious"] is True
        assert health["ok_streak"] == 1

    def test_a_broken_streak_is_reported_honestly(self, tmp_path):
        cfg, state, _ = self._bench(tmp_path)
        state.record_run({"ok": True})
        state.record_run({"ok": False})
        state.record_run({"ok": True})     # most recent
        assert build_payload(cfg, state, env={})["health"]["ok_streak"] == 1

    def test_hidden_cars_do_not_crowd_live_ones_out_of_the_cap(self, tmp_path):
        cfg, state, sid = self._bench(tmp_path)
        cfg.set("dashboard.max_listings", 2)
        for n in range(20):
            state.record(Listing(id=f"junk{n}", url=f"https://www.autotrader.ca/a/x/19_{n}_/",
                                 title="2019 BMW M5", price=999999, search_id=sid),
                         filtered=True, filter_reason="too dear")

        published = build_payload(cfg, state, env={})["listings"]
        assert len(published) == 2
        assert all(not l["filtered"] for l in published)


class TestTheCallersThatPassNoEnvironment:
    """`python -m autotrader dashboard` and the local `ui` server both do.

    Adding one env lookup that did not guard for it crashed both commands with
    an AttributeError, and nothing in the suite noticed because every test
    passed a dict.
    """

    def test_build_payload_without_an_environment(self):
        from autotrader import dashboard
        from autotrader.config import Config
        from autotrader.state import State
        cfg = Config({"version": 2, "searches": [], "filters": {},
                      "notifications": {"channels": {}},
                      "dashboard": {"enabled": True}})
        state = State({"version": 2, "listings": {}, "searches": {}, "runs": []})
        payload = dashboard.build_payload(cfg, state)
        assert payload["repo_issues"] is False

    def test_write_without_an_environment(self, tmp_path):
        from autotrader import dashboard
        from autotrader.config import Config
        from autotrader.state import State
        cfg = Config({"version": 2, "searches": [], "filters": {},
                      "notifications": {"channels": {}},
                      "dashboard": {"enabled": True}})
        state = State({"version": 2, "listings": {}, "searches": {}, "runs": []})
        assert dashboard.write(cfg, state, path=tmp_path / "docs/data.json")
