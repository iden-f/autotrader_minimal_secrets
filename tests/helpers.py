"""Test doubles shared by the runner and failure-mode suites."""

from autotrader import notifiers as _notifiers
from autotrader.http import Response
from autotrader.notifiers import Notifier, Result

# Captured at import, before any test patches them. Re-reading
# notifiers.dispatch inside a test picks up whatever an outer fixture already
# substituted, and calling that recurses or silently ignores your channels.
ORIGINAL_DISPATCH = _notifiers.dispatch
ORIGINAL_ALERT = _notifiers.alert


def use_channels(monkeypatch, runner_module, channels, alert_channels=None):
    """Point the runner's notification calls at specific test doubles."""
    targets = alert_channels if alert_channels is not None else channels

    def alert(c, s, b, e=None, notifiers=None):
        # A send aimed at particular channels - the runner does this to reach
        # a topic it is about to stop using - is recorded rather than
        # performed. The doubles still receive the message, so nothing here
        # ever touches the real service, and the test can still see where the
        # runner meant to send it.
        if notifiers is not None:
            for channel in notifiers:
                where = str((channel.config or {}).get("topic") or channel.name)
                for double in targets:
                    if hasattr(double, "aimed_at"):
                        double.aimed_at.append(where)
        return ORIGINAL_ALERT(c, s, b, e, targets)

    monkeypatch.setattr(
        runner_module.notifiers, "dispatch",
        lambda c, ch, r=None, e=None, n=None: ORIGINAL_DISPATCH(c, ch, r, e, channels))
    monkeypatch.setattr(runner_module.notifiers, "alert", alert)


class FakeFetcher:
    """Serves a search page, and a detail page for any listing we know."""

    def __init__(self, search_html, details=None, fail=None, budget=0):
        self.search_html = search_html
        self.details = details or {}
        self.fail = fail
        self.urls = []
        self.budget = budget
        self.spent = 0
        self.stats = {"requests": 0, "retries": 0, "failures": 0, "blocked": 0,
                      "budget": budget, "spent": 0}

    @property
    def budget_left(self):
        return max(0, self.budget - self.spent) if self.budget else 1_000_000

    def get(self, url, referer=None, allow_block=False):
        self.urls.append(url)
        self.spent += 1
        self.stats["requests"] = self.stats["spent"] = self.spent
        if self.fail:
            self.stats["failures"] += 1
            raise self.fail
        for listing_id, html in self.details.items():
            if f"_{listing_id}_" in url:
                return Response(url=url, status=200, text=html, elapsed_ms=1)
        return Response(url=url, status=200, text=self.search_html, elapsed_ms=1)

    def get_bytes(self, *a, **k):
        self.spent += 1
        return None

    def close(self):
        pass


class Capture(Notifier):
    """A notification channel that records instead of sending."""

    name = "capture"

    def __init__(self):
        super().__init__({}, {}, {})
        self.digests = []
        self.alerts = []
        # Where the runner aimed a send that named its own channels, rather
        # than letting the configured ones be picked.
        self.aimed_at = []

    def _send(self, changes, run):
        self.digests.append(list(changes))
        return Result("capture", True)

    def _send_text(self, subject, body):
        self.alerts.append((subject, body))
        return Result("capture", True)
