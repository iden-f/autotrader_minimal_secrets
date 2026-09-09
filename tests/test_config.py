import json

import pytest

from autotrader.config import Config, ConfigError


def test_a_pasted_link_becomes_a_named_search(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    search = cfg.add_search(
        "https://www.autotrader.ca/cars/bmw/m5/?rcp=15&srt=35&yRng=2021%2c&prx=-2")
    assert search.name == "2021+ BMW M5"
    assert search.enabled and search.id


def test_the_same_search_cannot_be_added_twice(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15")
    with pytest.raises(ConfigError, match="already being watched"):
        cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15")


def test_tracking_parameters_do_not_create_a_duplicate_search(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15")
    with pytest.raises(ConfigError):
        cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15&utm_source=email")


def test_a_link_from_another_site_is_refused_with_an_explanation(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    with pytest.raises(ConfigError, match="not autotrader.ca"):
        cfg.add_search("https://www.kijiji.ca/b-cars/")


def test_force_accepts_an_unusual_link(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    assert cfg.add_search("https://www.autotrader.ca/", strict=False)


def test_config_survives_a_round_trip(tmp_path):
    path = tmp_path / "c.json"
    cfg = Config.defaults(path)
    cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15", "My M5")
    cfg.set("filters.max_price", 110000)
    cfg.save()
    again = Config.load(path)
    assert [s.name for s in again.searches] == ["My M5"]
    assert again.get("filters.max_price") == 110000


def test_missing_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"searches": []}))
    cfg = Config.load(path)
    assert cfg.get("scraping.max_pages") == 3
    assert cfg.get("notifications.notify_on.new") is True


def test_a_broken_config_file_says_so(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        Config.load(path)


def test_loading_config_never_reads_the_environment(tmp_path, monkeypatch):
    """Two sources of truth is how a stale secret quietly resurrects a search.

    The v1 SEARCH_URL secret is adopted once, by provision.adopt_legacy_search,
    and never read again.
    """
    monkeypatch.setenv("SEARCH_URL", "https://www.autotrader.ca/cars/bmw/m5/?rcp=15")
    assert Config.load(tmp_path / "c.json").searches == []


def test_disabled_searches_are_not_run(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    search = cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15")
    cfg.data["searches"][0]["enabled"] = False
    assert cfg.searches and cfg.active_searches == []


def test_removing_a_search(tmp_path):
    cfg = Config.defaults(tmp_path / "c.json")
    search = cfg.add_search("https://www.autotrader.ca/cars/bmw/m5/?rcp=15")
    assert cfg.remove_search(search.id)
    assert not cfg.remove_search("nope")
    assert cfg.searches == []


def test_search_ids_stay_unique(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"searches": [
        {"id": "dup", "url": "https://www.autotrader.ca/cars/bmw/m5/?a=1"},
        {"id": "dup", "url": "https://www.autotrader.ca/cars/bmw/m3/?a=1"}]}))
    ids = [s.id for s in Config.load(path).searches]
    assert len(set(ids)) == 2


def test_junk_entries_are_ignored_rather_than_crashing(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"searches": [
        "not an object", {"no_url": True},
        {"url": "https://www.autotrader.ca/cars/bmw/m5/?a=1"}]}))
    assert len(Config.load(path).searches) == 1
