import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
ARCHIVES = ROOT / "archives"


@pytest.fixture
def fixture_html():
    def read(name: str) -> str:
        return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    return read


@pytest.fixture
def archive_html():
    """Real listing pages captured by the previous version of the bot."""
    def read(listing_id: str) -> str:
        return (ARCHIVES / listing_id / "page.html").read_text(
            encoding="utf-8", errors="replace")
    return read


@pytest.fixture
def archive_ids():
    if not ARCHIVES.exists():
        return []
    return sorted(p.name for p in ARCHIVES.iterdir()
                  if p.is_dir() and (p / "page.html").exists())


# ---------------------------------------------------------------- run harness

import pytest as _pytest  # noqa: E402

from autotrader import notifiers, runner as runner_mod  # noqa: E402
from autotrader.config import Config  # noqa: E402
from autotrader.http import Response  # noqa: E402
from autotrader.notifiers import Notifier, Result  # noqa: E402
from autotrader.runner import run  # noqa: E402
from autotrader.state import State  # noqa: E402

from .helpers import Capture, FakeFetcher, use_channels  # noqa: E402

SEARCH = "https://www.autotrader.ca/cars/bmw/m5/?rcp=15&srt=35&prx=-2&loc=M5V"


@_pytest.fixture
def bench(tmp_path, monkeypatch, fixture_html, archive_html):
    """A working directory with a config, a fake site and a captured channel."""
    monkeypatch.chdir(tmp_path)
    cfg = Config.defaults(tmp_path / "config.json")
    cfg.add_search(SEARCH, "BMW M5")
    cfg.set("scraping.delay_ms", 0)
    cfg.set("scraping.retries", 0)
    cfg.set("archive.mode", "off")
    cfg.save()

    sink = Capture()
    use_channels(monkeypatch, runner_mod, [sink])

    details = {i: archive_html(i) for i in ("13166607", "68819631", "13221555")}

    def go(search_html=None, fail=None, config=None):
        return run(config or cfg, State.load(tmp_path / "state.json"),
                   fetcher=FakeFetcher(search_html or fixture_html("search_cards"),
                                       details, fail))

    return type("Bench", (), {"cfg": cfg, "sink": sink, "run": staticmethod(go),
                              "path": tmp_path, "cards": fixture_html("search_cards")})
