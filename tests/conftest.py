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
