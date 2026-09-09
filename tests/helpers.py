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
    monkeypatch.setattr(
        runner_module.notifiers, "dispatch",
        lambda c, ch, r=None, e=None, n=None: ORIGINAL_DISPATCH(c, ch, r, e, channels))
    monkeypatch.setattr(
        runner_module.notifiers, "alert",
        lambda c, s, b, e=None, n=None: ORIGINAL_ALERT(
            c, s, b, e, alert_channels if alert_channels is not None else channels))


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

    def _send(self, changes, run):
        self.digests.append(list(changes))
        return Result("capture", True)

    def _send_text(self, subject, body):
        self.alerts.append((subject, body))
        return Result("capture", True)
