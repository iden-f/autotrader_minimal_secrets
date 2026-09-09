"""A broken notification channel must never cost a run."""
from datetime import datetime

from autotrader import render
from autotrader.config import Config
from autotrader.listing import Listing
from autotrader.notifiers import Notifier, Result, alert, dispatch, in_quiet_hours
from autotrader.state import Change


def sample():
    a = Listing(id="1", url="https://www.autotrader.ca/a/x/5_1_/", title="2022 BMW M5",
                year=2022, make="BMW", model="M5", trim="Competition", price=102199,
                mileage_km=30244, location="St. Catharines", drivetrain="AWD")
    b = Listing(id="2", url="https://www.autotrader.ca/a/y/19_2_/", title="2021 BMW M5",
                year=2021, make="BMW", model="M5", price=104999, mileage_km=52000)
    return [Change(Change.NEW, a), Change(Change.PRICE_DROP, b, old_price=109999, new_price=104999)]


class Boom(Notifier):
    name = "boom"
    def _send(self, changes, run): raise RuntimeError("channel is on fire")
    def _send_text(self, s, b): raise RuntimeError("still on fire")


class Fine(Notifier):
    name = "fine"
    def __init__(self): super().__init__({}, {}, {}); self.sent = []
    def _send(self, changes, run): self.sent.append(changes); return Result("fine", True)
    def _send_text(self, s, b): self.sent.append((s, b)); return Result("fine", True)


def test_one_failing_channel_does_not_stop_the_others():
    good = Fine()
    results = dispatch(Config.defaults(), sample(), notifiers=[Boom({}, {}, {}), good])
    assert [r.ok for r in results] == [False, True]
    assert good.sent


def test_a_failing_channel_never_raises():
    results = dispatch(Config.defaults(), sample(), notifiers=[Boom({}, {}, {})])
    assert results[0].ok is False and "on fire" in results[0].detail


def test_health_alerts_also_survive_a_broken_channel():
    results = alert(Config.defaults(), "subject", "body", notifiers=[Boom({}, {}, {}), Fine()])
    assert [r.ok for r in results] == [False, True]


def test_no_changes_means_no_message():
    good = Fine()
    assert dispatch(Config.defaults(), [], notifiers=[good]) == []
    assert not good.sent


def test_missing_configuration_is_reported_not_silent():
    results = dispatch(Config.defaults(), sample(), env={})
    assert results[0].skipped and "no notification channel" in results[0].detail


def test_channels_switch_on_by_themselves_once_their_secrets_exist():
    cfg = Config.defaults()
    assert cfg.active_channels({}) == []
    assert cfg.active_channels({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}) == ["telegram"]


def test_a_half_configured_channel_stays_off_and_says_what_is_missing():
    cfg = Config.defaults()
    status = cfg.channel_status({"TELEGRAM_BOT_TOKEN": "t"})["telegram"]
    assert not status["active"]
    assert status["missing"] == ["TELEGRAM_CHAT_ID"]


def test_twilio_stays_off_unless_explicitly_enabled():
    cfg = Config.defaults()
    env = {"TWILIO_SID": "s", "TWILIO_TOKEN": "t", "TWILIO_FROM": "+1", "TWILIO_TO": "+2"}
    assert "twilio" not in cfg.active_channels(env)   # it costs money, so opt-in


class TestQuietHours:
    S = {"quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00"},
         "timezone": "America/Toronto"}

    def test_inside_the_window(self):
        assert in_quiet_hours(self.S, datetime(2026, 9, 9, 2, 0))
        assert in_quiet_hours(self.S, datetime(2026, 9, 9, 23, 30))

    def test_outside_the_window(self):
        assert not in_quiet_hours(self.S, datetime(2026, 9, 9, 12, 0))
        assert not in_quiet_hours(self.S, datetime(2026, 9, 9, 7, 0))

    def test_disabled_means_never_quiet(self):
        assert not in_quiet_hours({"quiet_hours": {"enabled": False}}, datetime(2026, 9, 9, 2, 0))

    def test_a_broken_time_does_not_silence_everything(self):
        bad = {"quiet_hours": {"enabled": True, "start": "nonsense", "end": "07:00"}}
        assert not in_quiet_hours(bad, datetime(2026, 9, 9, 2, 0))


class TestRendering:
    def test_the_headline_counts_each_kind(self):
        assert render.headline(sample()) == "AutoTrader: 1 new listing, 1 price drop"

    def test_sms_stays_within_one_message_budget(self):
        assert len(render.as_sms(sample())) <= render.MAX_SMS

    def test_telegram_html_escapes_user_text(self):
        bad = Listing(id="1", url="http://x", title='<script>alert("x")</script>')
        out = render.as_telegram_html([Change(Change.NEW, bad)])
        assert "<script>" not in out and "&lt;script&gt;" in out

    def test_email_html_escapes_user_text(self):
        bad = Listing(id="1", url="http://x", title='"><img src=x onerror=alert(1)>')
        out = render.as_email_html([Change(Change.NEW, bad)])
        # The payload may appear as inert text, but never as a live tag or as
        # an attribute that escapes the href it sits in.
        assert "<img src=x" not in out
        assert '"><img' not in out
        assert "&lt;img src=x onerror=alert(1)&gt;" in out

    def test_discord_respects_its_ten_embed_limit(self):
        many = [Change(Change.NEW, Listing(id=str(i), url="http://x", title=f"Car {i}"))
                for i in range(30)]
        assert len(render.as_discord_embeds(many, limit=25)) == 10

    def test_a_price_drop_shows_both_prices(self):
        out = render.as_text(sample())
        assert "$5,000 off" in out and "$104,999" in out

    def test_the_trim_is_not_repeated_after_the_title(self):
        line = render.as_text([sample()[0]])
        assert line.count("Competition") == 1

    def test_a_car_with_no_price_still_renders(self):
        listing = Listing(id="1", url="http://x", title="2020 BMW M5")
        out = render.as_text([Change(Change.NEW, listing)])
        assert "Price not listed" in out

    def test_the_webhook_payload_is_json_serialisable(self):
        import json
        json.dumps(render.as_json_payload(sample(), {"ok": True}))
