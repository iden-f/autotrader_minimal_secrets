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
