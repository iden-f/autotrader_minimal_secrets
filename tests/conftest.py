import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
# Ten real listing pages the previous bot captured, kept gzipped. They used to
# be read straight out of archives/, but archives/ is data the bot prunes -
# compacting it away took fifty tests with it. A test fixture belongs in the
# test suite, where nothing else gets to delete it.
LISTING_PAGES = FIXTURES / "listings"


@pytest.fixture
def fixture_html():
    def read(name: str) -> str:
        return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    return read


@pytest.fixture
def archive_html():
    """Real listing pages captured by the previous version of the bot."""
    def read(listing_id: str) -> str:
        return gzip.decompress(
            (LISTING_PAGES / f"{listing_id}.html.gz").read_bytes()
        ).decode("utf-8", "replace")
    return read


@pytest.fixture
def archive_ids():
    if not LISTING_PAGES.exists():
        return []
    return sorted(p.name.replace(".html.gz", "")
                  for p in LISTING_PAGES.glob("*.html.gz"))


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


# --- the suite must not write to the repository it is testing ---------------
#
# A test that forgets to chdir into tmp_path runs the real bot in the real
# working tree. That happened: monkeypatch.undo() in one test reverted the
# fixture's own chdir along with the patch it meant to remove, so the run
# after it provisioned a fresh ntfy topic and rewrote NOTIFY.md here. Nothing
# failed. It was one `git add -A` from redirecting live alerts to a topic
# nobody is subscribed to, and the only reason it was caught is that a rebase
# happened to conflict on the file.
#
# So the suite watches its own hands. These are the files the bot writes; if
# running the tests changes any of them, the tests are not running where they
# think they are.
_REPO_FILES = ("NOTIFY.md", "config.json", "state.json", "docs/data.json",
               "EVENTS.md", "docs/events.json", "SETUP.md")


def _fingerprint() -> dict[str, str]:
    import hashlib
    out = {}
    for name in _REPO_FILES:
        path = ROOT / name
        try:
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out[name] = "absent"
    return out


@pytest.fixture(scope="session", autouse=True)
def _repo_is_left_alone():
    before = _fingerprint()
    yield
    changed = [name for name, digest in _fingerprint().items()
               if before.get(name) != digest]
    assert not changed, (
        "the test suite wrote to the repository it is testing: "
        + ", ".join(changed)
        + ". A test is running outside tmp_path - look for a missing "
          "monkeypatch.chdir, or a monkeypatch.undo() that reverted one."
    )
