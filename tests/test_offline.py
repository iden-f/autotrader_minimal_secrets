"""The installed app: what it shows with no network, and how it ever updates.

Both halves of this shipped broken and both looked fine.

The worker's own comments said data.json was cached for offline use. It was
not: the page's first fetch of it happens before a freshly installed worker
controls the page, so it never reached the fetch handler. A phone that
installed the app and then lost signal got "Could not load the data".

The shell was cache-first with a hardcoded cache name, so an installed app ran
whatever app.js it first saw - forever, through every deploy. The replacement
that tried to notice a change by re-fetching behind the response and comparing
text picked up nothing either, measured against a real browser.

The tests here are static: they assert the properties that made those bugs
possible are gone. The behavioural proof is in scratch/audit_sw.py, which
takes the network away by stopping the server rather than by asking the
browser to pretend - the first version of that asked the browser, which does
not apply to a service worker's own fetches or to loopback, so every offline
assertion in it passed without ever being offline.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autotrader import dashboard

DOCS = Path("docs")
SW = DOCS / "sw.js"


@pytest.fixture
def worker() -> str:
    return SW.read_text(encoding="utf-8")


class TestTheWorkerCanBeUpdated:
    def test_the_cache_names_are_built_from_the_stamp(self, worker):
        """A hardcoded cache name is an app that can never update."""
        assert "const BUILD = '" in worker
        for name in ("SHELL", "DATA"):
            line = re.search(rf"const {name} = (.+);", worker).group(1)
            assert "BUILD" in line, f"{name} does not depend on BUILD: {line}"

    def test_the_stamp_is_not_still_the_placeholder(self, worker):
        build = re.search(r"const BUILD = '([^']*)';", worker).group(1)
        assert build and build != "__BUILD__", (
            "the worker was published unstamped, so every deploy ships the "
            "same cache name and no installed app will ever update")

    def test_stamping_is_deterministic(self, tmp_path):
        import shutil
        for name in ("sw.js", "app.js", "index.html"):
            shutil.copy(DOCS / name, tmp_path / name)
        first = dashboard.stamp_worker(tmp_path)
        again = dashboard.stamp_worker(tmp_path)
        assert first and first == again, "a publish that changes nothing must " \
                                         "not invalidate every installed app"

    def test_changing_the_page_changes_the_stamp(self, tmp_path):
        import shutil
        for name in ("sw.js", "app.js", "index.html"):
            shutil.copy(DOCS / name, tmp_path / name)
        before = dashboard.stamp_worker(tmp_path)
        (tmp_path / "app.js").write_text(
            (tmp_path / "app.js").read_text() + "\n// a fix\n")
        assert dashboard.stamp_worker(tmp_path) != before

    def test_writing_the_data_stamps_the_worker(self, tmp_path):
        """Nobody has to remember, which is the only discipline that lasts."""
        import shutil
        from autotrader.config import Config
        from autotrader.state import State
        for name in ("sw.js", "app.js", "index.html"):
            shutil.copy(DOCS / name, tmp_path / name)
        (tmp_path / "sw.js").write_text(
            (tmp_path / "sw.js").read_text().replace(
                re.search(r"const BUILD = '([^']*)';",
                          (tmp_path / "sw.js").read_text()).group(0),
                "const BUILD = '__BUILD__';"))
        cfg = Config({"version": 2, "searches": [], "filters": {},
                      "dashboard": {"enabled": True}})
        state = State({"version": 2, "listings": {}, "searches": {}, "runs": []})
        dashboard.write(cfg, state, {}, path=tmp_path / "data.json")
        assert "__BUILD__" not in (tmp_path / "sw.js").read_text()

    def test_the_install_bypasses_the_browsers_own_cache(self, worker):
        """Otherwise a new worker is handed the bytes the old one was using,
        and the install is not an update."""
        assert "cache: 'reload'" in worker


class TestOfflineIsNotAFreshPage:
    def test_the_data_is_cached_on_install(self, worker):
        """Not on first fetch - that happens before the worker controls the
        page, which is why this was missing in practice while every comment
        in the file said it was there."""
        install = worker.split("addEventListener('install'")[1].split(
            "addEventListener('activate'")[0]
        assert "data.json" in install

    def test_a_failed_data_cache_does_not_fail_the_install(self, worker):
        install = worker.split("addEventListener('install'")[1].split(
            "addEventListener('activate'")[0]
        assert ".catch(" in install, (
            "a first visit during an outage would leave the app with no "
            "worker at all")

    def test_data_is_network_first(self, worker):
        """A car that sold yesterday shown as available today is worse than
        no page at all."""
        body = worker.split("async function data(")[1].split("async function shell")[0]
        assert body.index("await fetch(request)") < body.index("caches.match(request)")

    def test_a_cached_answer_is_labelled_as_one(self, worker):
        assert "X-From-Cache" in worker

    def test_the_page_reads_that_label(self):
        """With a worker installed, an offline load still returns 200. Without
        reading the header the page cannot tell that apart from a live fetch,
        and draws stale data with no mention of the network."""
        app = (DOCS / "app.js").read_text()
        assert "X-From-Cache" in app
        assert "app.offline = true" in app

    def test_nothing_resolves_respond_with_undefined(self, worker):
        """caches.match() misses resolve to undefined, which fails the request
        with a TypeError the page cannot tell from a parse error."""
        for handler in ("async function data(", "async function shell("):
            body = worker.split(handler)[1].split("\n}")[0]
            assert "new Response(" in body, handler

    def test_offline_outranks_everything_else_the_trust_line_says(self):
        """"Checked 3 days ago" reads as "the bot is broken" when the truth
        may be "your phone has no signal", and the two want opposite actions.
        """
        app = (DOCS / "app.js").read_text()
        state = app.split("function trustState()")[1].split("\n}")[0]
        assert state.index("app.offline") < state.index("if (!run.at)"), (
            "the offline branch has to come before the other verdicts or one "
            "of them answers first")
