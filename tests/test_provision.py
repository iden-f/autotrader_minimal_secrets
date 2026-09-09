"""A fresh install must work without anybody configuring it first."""
import json
import re

import pytest

from autotrader.config import Config
from autotrader.provision import (adopt_legacy_search, bootstrap, ensure_notifications,
                                  generate_topic, subscribe_url, write_notify_file)

URL = "https://www.autotrader.ca/cars/bmw/m5/?rcp=15&prx=-2"


class TestTopicGeneration:
    def test_topics_are_unguessable_and_unique(self):
        topics = {generate_topic() for _ in range(200)}
        assert len(topics) == 200
        assert all(len(t) >= 28 for t in topics)

    def test_topics_avoid_ambiguous_characters(self):
        """This gets typed into a phone by hand."""
        for _ in range(50):
            tail = generate_topic().split("-", 1)[1]
            assert not set(tail) & set("ilo01")

    def test_topics_are_url_safe(self):
        for _ in range(50):
            assert re.fullmatch(r"[a-z0-9-]+", generate_topic())


class TestFirstRunNotifications:
    def test_a_bare_install_configures_ntfy_by_itself(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        assert cfg.active_channels({}) == []

        result = ensure_notifications(cfg, {})
        assert result["changed"]
        assert cfg.active_channels({}) == ["ntfy"]
        assert subscribe_url(cfg).startswith("https://ntfy.sh/autotrader-")

    def test_nothing_is_touched_when_a_channel_already_works(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
        result = ensure_notifications(cfg, env)
        assert not result["changed"]
        assert not cfg.get("notifications.channels.ntfy.topic")

    def test_an_existing_topic_is_kept(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        cfg.set("notifications.channels.ntfy.topic", "my-own-topic-abcdefgh")
        ensure_notifications(cfg, {})
        assert cfg.get("notifications.channels.ntfy.topic") == "my-own-topic-abcdefgh"

    def test_running_twice_changes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        ensure_notifications(cfg, {})
        topic = cfg.get("notifications.channels.ntfy.topic")
        assert not ensure_notifications(cfg, {})["changed"]
        assert cfg.get("notifications.channels.ntfy.topic") == topic

    def test_the_subscribe_instructions_are_written_out(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        ensure_notifications(cfg, {})
        path = write_notify_file(cfg, tmp_path / "NOTIFY.md")
        body = path.read_text()
        assert cfg.get("notifications.channels.ntfy.topic") in body
        assert "ntfy" in body.lower()
        # The privacy trade-off must be stated, not buried.
        assert "anyone who knows this topic" in body.lower()


class TestLegacySecret:
    def test_the_secret_is_adopted_once(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        result = adopt_legacy_search(cfg, {"SEARCH_URL": URL})
        assert result["changed"]
        assert [s.url for s in cfg.searches] == [URL]
        assert cfg.data["legacy_search_url_adopted"] is True

    def test_it_is_never_read_again(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        adopt_legacy_search(cfg, {"SEARCH_URL": URL})
        cfg.remove_search(cfg.searches[0].id)
        result = adopt_legacy_search(cfg, {"SEARCH_URL": URL})
        assert not result["changed"]
        assert cfg.searches == [], "a stale secret must not resurrect a deleted search"

    def test_the_note_says_the_secret_can_go(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        adopt_legacy_search(cfg, {"SEARCH_URL": URL})
        assert "delete it" in cfg.searches[0].notes

    def test_no_secret_is_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        assert not adopt_legacy_search(cfg, {})["changed"]

    def test_a_secret_matching_an_existing_search_does_not_duplicate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        cfg.add_search(URL, "Mine")
        adopt_legacy_search(cfg, {"SEARCH_URL": URL})
        assert len(cfg.searches) == 1

    def test_an_unparseable_secret_is_still_adopted(self, tmp_path, monkeypatch):
        """Better a search the user can see and fix than one silently dropped."""
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        odd = "https://www.autotrader.ca/cars/"
        assert adopt_legacy_search(cfg, {"SEARCH_URL": odd})["changed"]
        assert len(cfg.searches) == 1


class TestBootstrap:
    def test_a_fresh_install_ends_up_usable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        result = bootstrap(cfg, {"SEARCH_URL": URL})
        assert result["changed"]
        assert result["channels"] == ["ntfy"]
        assert result["subscribe_url"]
        assert len(cfg.searches) == 1
        assert (tmp_path / "NOTIFY.md").exists()
        # ...and it was persisted, not just held in memory.
        assert json.loads((tmp_path / "config.json").read_text())["searches"]

    def test_it_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        bootstrap(cfg, {"SEARCH_URL": URL})
        before = (tmp_path / "config.json").read_text()
        again = bootstrap(Config.load(tmp_path / "config.json"), {"SEARCH_URL": URL})
        assert not again["changed"]
        assert (tmp_path / "config.json").read_text() == before

    def test_it_defers_to_a_real_channel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = Config.defaults(tmp_path / "config.json")
        result = bootstrap(cfg, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"})
        assert result["channels"] == ["telegram"]
        assert not (tmp_path / "NOTIFY.md").exists()
