"""A broken notification channel must never cost a run."""
from datetime import datetime

from autotrader import render
from autotrader.config import Config
from autotrader.listing import Listing
from autotrader import notifiers
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
    def test_the_headline_leads_with_the_drop_and_its_figure(self):
        """A title is a lock screen, not an index.

        "1 new listing, 1 price drop" costs a look at the phone to find out
        what dropped and by how much, which is the entire content.
        """
        line = render.headline(sample())
        assert line.startswith("AutoTrader: ")
        assert "$" in line, line
        assert "1 more change" in line, line

    def test_one_change_names_the_car_and_the_figure(self):
        [only] = [c for c in sample() if c.kind == Change.PRICE_DROP]
        line = render.headline([only])
        assert "down $" in line and only.listing.price_text in line, line

    def test_no_changes_says_so(self):
        assert render.headline([]) == "AutoTrader: no changes"

    def test_without_a_drop_it_counts_the_kinds(self):
        news = [c for c in sample() if c.kind == Change.NEW]
        assert render.headline(news * 2) == "AutoTrader: 2 new listings"

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
        """In the body. The headline names the car too, and should."""
        body = render.as_text([sample()[0]]).split("\n", 1)[1]
        assert body.count("Competition") == 1, body

    def test_a_car_with_no_price_still_renders(self):
        listing = Listing(id="1", url="http://x", title="2020 BMW M5")
        out = render.as_text([Change(Change.NEW, listing)])
        assert "Price not listed" in out

    def test_the_webhook_payload_is_json_serialisable(self):
        import json
        json.dumps(render.as_json_payload(sample(), {"ok": True}))


class TestWhatThePushCarries:
    """The alert is half the product, so what rides on it is tested.

    All of this is checked against the request the notifier builds, never by
    sending: a suite that touches ntfy.sh is a suite that fails when ntfy.sh
    is having a bad day, and it would deliver to the topic on a real phone.
    """

    def _posted(self, changes, settings=None, monkeypatch=None):
        sent = {}

        class FakeResponse:
            ok = True
            status_code = 200
            text = ""

        def fake_post(url, data=None, headers=None, timeout=None):
            sent["url"] = url
            sent["headers"] = headers or {}
            sent["body"] = (data or b"").decode("utf-8")
            return FakeResponse()

        monkeypatch.setattr(notifiers.requests, "post", fake_post)
        # `settings or {...}` would swallow an explicit empty dict, which is
        # exactly the case one of these tests is about.
        if settings is None:
            settings = {"dashboard_url": "https://example.test/watch"}
        n = notifiers.NtfyNotifier({"topic": "t"}, {}, settings)
        result = n.send(changes, {})
        assert result.ok, result.detail
        return sent

    def test_a_price_drop_wakes_the_phone_and_a_removal_does_not(self, monkeypatch):
        drop = [c for c in sample() if c.kind == Change.PRICE_DROP]
        assert self._posted(drop, monkeypatch=monkeypatch)["headers"]["Priority"] == "high"

        gone = [Change(Change.REMOVED, sample()[0].listing)]
        assert self._posted(gone, monkeypatch=monkeypatch)["headers"]["Priority"] == "low"

    def test_it_opens_the_car_on_the_dashboard_not_the_search_page(self, monkeypatch):
        drop = [c for c in sample() if c.kind == Change.PRICE_DROP]
        click = self._posted(drop, monkeypatch=monkeypatch)["headers"]["Click"]
        assert click == f"https://example.test/watch/#/listing/{drop[0].listing.id}"

    def test_with_no_dashboard_it_falls_back_to_the_listing(self, monkeypatch):
        drop = [c for c in sample() if c.kind == Change.PRICE_DROP]
        click = self._posted(drop, settings={}, monkeypatch=monkeypatch)["headers"]["Click"]
        assert click == drop[0].listing.url

    def test_one_change_carries_the_photo(self, monkeypatch):
        listing = Listing(id="9", url="http://x", title="2022 BMW M5", price=90000,
                          images=["https://pics.test/a.jpg"])
        sent = self._posted([Change(Change.NEW, listing)], monkeypatch=monkeypatch)
        assert sent["headers"]["Attach"] == "https://pics.test/a.jpg"

    def test_a_digest_carries_no_photo(self, monkeypatch):
        """Forty cars and one picture says the wrong thing about which one."""
        listing = Listing(id="9", url="http://x", title="2022 BMW M5", price=90000,
                          images=["https://pics.test/a.jpg"])
        many = [Change(Change.NEW, listing), Change(Change.NEW, listing)]
        assert "Attach" not in self._posted(many, monkeypatch=monkeypatch)["headers"]

    def test_the_title_carries_the_figure(self, monkeypatch):
        drop = [c for c in sample() if c.kind == Change.PRICE_DROP]
        title = self._posted(drop, monkeypatch=monkeypatch)["headers"]["Title"]
        assert "down $" in title and "change" not in title, title


class TestWhatTheAlertActuallySays:
    """The part of this product nobody screenshots.

    A sweep for user-visible strings derived rather than stored found four
    defects here that had never been looked at, because looking means reading
    a push notification on a phone rather than a page in a browser.
    """

    def car(self, **kw):
        from autotrader.listing import Listing
        base = dict(id="c39aad61", title="BMW X3 xDrive30i", year=2019,
                    make="BMW", model="X3", price=21999, price_source="detail",
                    mileage_km=103000, location="Thornhill", province="ON")
        base.update(kw)
        return Listing(**base)

    def test_a_dealers_feature_list_is_not_repeated_under_the_name(self):
        """The page has trimmed this since the day it was written; every
        notification repeated it verbatim."""
        from autotrader import render
        facts = render._facts(self.car(
            trim="XDrive30i * NO ACCIDENTS * ONE OWNER * CERTIFIED"))
        assert not any("ACCIDENTS" in f for f in facts), facts

    def test_a_field_a_dealer_left_as_na_is_not_a_specification(self):
        from autotrader import render
        facts = render._facts(self.car(transmission="n/a"))
        assert "n/a" not in facts, facts

    def test_an_accented_name_survives_the_ntfy_header(self):
        """HTTP headers are latin-1 at best. This encoded UTF-8 and decoded
        the bytes as latin-1, which is the definition of mojibake - and there
        is a "BMW M3 COMPETITION" with an accent in this watch."""
        from autotrader.notifiers import _ascii_header
        out = _ascii_header("AutoTrader: 2025 BMW M3 COMPÉTITION $118,995")
        assert out == "AutoTrader: 2025 BMW M3 COMPETITION $118,995"
        assert out.isascii()

    def test_a_header_of_pure_non_latin_still_says_something(self):
        from autotrader.notifiers import _ascii_header
        assert _ascii_header("中文").isascii()


class TestTheCoverageAlertCountsSlots:
    """The "79.2% - 51 of 48 expected checks" bug, in the alert.

    The dashboard printed a percentage derived from slots beside a fraction
    derived from runs. That was fixed on the page and left standing here,
    where the same sentence is sent by email - which is how a bug survives a
    fix: the second copy is in the channel nobody looks at.
    """

    def test_the_fraction_matches_the_percentage(self, tmp_path, monkeypatch):
        import json
        from datetime import datetime, timedelta, timezone
        from autotrader import events
        from autotrader.config import Config
        from autotrader.state import State

        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        cfg.set("health.expected_interval_minutes", 120)
        cfg.save()
        state = State(path=tmp_path / "state.json")
        now = datetime.now(timezone.utc)
        # Four checks in a 24-hour window that asks for twelve.
        state.data["runs"] = [
            {"at": (now - timedelta(hours=h)).isoformat(timespec="seconds"),
             "ok": True, "searches_run": 1, "listings_seen": 5}
            for h in (1, 7, 13, 19)]
        note = events.thin_coverage(cfg, state, {}, now=now)
        assert note, "a quarter of the slots covered should be worth saying"
        body = note["body"]
        assert "half-hours" not in body, body
        assert "2-hour slots" in body, body
        # The number in the sentence is the number the percentage came from.
        import re
        got = re.search(r"(\d+) of (\d+) 2-hour slots", body)
        assert got, body
        covered, expected = int(got.group(1)), int(got.group(2))
        assert round(covered / expected * 100, 1) == note["pct"], (body, note["pct"])


class TestTheEmailIsHtmlNotEscapedHtml:
    def test_the_fact_separator_is_a_separator_not_its_own_source_code(self):
        """`_esc(' &#183; '.join(...))` escaped the ampersand first, so the
        later .replace could never match and the email showed the characters
        "&#183;" between every fact."""
        from autotrader.listing import Listing
        from autotrader.state import Change
        from autotrader import render
        car = Listing(id="x", title="BMW M4", year=2018, make="BMW", model="M4",
                      price=64499, price_source="detail", mileage_km=50122,
                      color="Black", location="Burnaby")
        html = render.as_email_html([Change(kind=Change.NEW, listing=car)])
        assert "&amp;#183;" not in html
        assert "&#183;" in html, "the separator went missing entirely"

    def test_a_dealer_title_with_an_ampersand_is_still_escaped(self):
        """The fix must not stop escaping the thing escaping is for."""
        from autotrader.listing import Listing
        from autotrader.state import Change
        from autotrader import render
        car = Listing(id="x", title="BMW M4", year=2018, make="BMW", model="M4",
                      price=1, price_source="detail", color="Black & Gold")
        html = render.as_email_html([Change(kind=Change.NEW, listing=car)])
        assert "Black &amp; Gold" in html
