"""The script the owner pastes to keep this bot on time.

GitHub's scheduler fills about 40% of this repository's two-hour slots, and it
drops whole windows rather than individual firings, so a second cron offset
buys very little. An outside timer calling repository_dispatch is the fix, and
the handoff for setting one up is the thing most likely to be got wrong - so
it is a script in the repository rather than a curl in a README, and the
script's assumptions are asserted against the workflow here.

Every status-code branch is exercised with a stubbed curl. The README version
of this needed a table of HTTP codes that could not be verified from where it
was written: the sandbox's proxy intercepts api.github.com and rewrote the
replies (an invalid token came back 200), and docs.github.com was blocked. A
table of unverified codes reads exactly like a table of facts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "keep-time.sh"
WATCH = ROOT / ".github" / "workflows" / "watch.yml"


def workflow():
    doc = yaml.safe_load(WATCH.read_text())
    return doc[True] if True in doc else doc["on"]


def run(*args, code="204", body='{"ok":1}', token="github_pat_x", **env):
    """Run the script with curl stubbed to return a canned status."""
    stub = ROOT / "tests" / "fixtures" / "fake-curl.sh"
    environ = dict(os.environ, KEEP_TIME_CURL=str(stub),
                   FAKE_CODE=code, FAKE_BODY=body, **env)
    if token:
        environ["GITHUB_TOKEN"] = token
    else:
        environ.pop("GITHUB_TOKEN", None)
        environ.pop("GH_TOKEN", None)
    return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True,
                          text=True, env=environ, cwd=ROOT, timeout=30)


class TestItCannotDriftFromTheWorkflow:
    """The script names an event type and a repository. If either stops
    matching the workflow, the owner's timer silently stops working and the
    dashboard says the schedule is keeping time when nothing is."""

    def test_the_event_type_is_one_the_workflow_accepts(self):
        accepted = workflow()["repository_dispatch"]["types"]
        body = SCRIPT.read_text()
        used = [l for l in body.splitlines() if l.startswith("EVENT=")]
        assert len(used) == 1, used
        event = used[0].split('"')[1]
        assert event in accepted, f"script sends {event!r}, workflow takes {accepted}"

    def test_the_repository_is_the_one_the_workflow_lives_in(self):
        import re
        body = SCRIPT.read_text()
        repo = re.search(r'^REPO="([^"]+)"', body, re.M).group(1)
        url = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True, cwd=ROOT).stdout.strip()
        assert repo in url, f"script targets {repo}, origin is {url}"

    def test_the_workflow_still_accepts_an_outside_timer_at_all(self):
        assert "repository_dispatch" in workflow(), (
            "the script has nothing to call")


class TestEveryAnswerGitHubCanGive:
    """Exercised, not tabulated."""

    @pytest.mark.parametrize("code,expect", [
        ("204", "OK"),
        ("401", "does not recognise the token"),
        ("403", "Contents: Read and write"),
        ("404", "cannot SEE"),
        ("422", "bug in this script"),
        ("415", "bug in this script"),
        ("000", "no HTTP response"),
        ("500", "not a code this script knows"),
        ("418", "not a code this script knows"),
    ])
    def test_it_explains_what_to_do(self, code, expect):
        out = run(code=code)
        assert expect in (out.stdout + out.stderr), (code, out.stdout, out.stderr)

    def test_only_success_exits_zero(self):
        assert run(code="204").returncode == 0
        for code in ("401", "403", "404", "422", "000", "500"):
            assert run(code=code).returncode == 1, code

    def test_an_unknown_code_still_prints_what_github_said(self):
        """The one thing a person can search for."""
        out = run(code="418", body='{"message":"teapot"}')
        assert "teapot" in out.stderr, out.stderr


class TestItRefusesToDoSomethingUseless:
    def test_no_token_explains_how_to_make_one(self):
        out = run(token=None)
        assert out.returncode == 1
        for needed in ("Fine-grained", "Contents", "Read and write"):
            assert needed in out.stderr, out.stderr

    def test_the_timer_name_reaching_the_dashboard_is_bounded(self):
        """It is printed on a page. Untrusted length and characters are not."""
        out = run("--from", "My Mac <script>alert(1)</script> " + "x" * 60)
        assert out.returncode == 0
        said = [l for l in out.stdout.splitlines() if "as " in l][0]
        name = said.split('"')[1]
        assert len(name) <= 24, name
        assert all(c.isalnum() or c in ".-" for c in name), name
        assert "<" not in name and ">" not in name

    def test_cron_mode_is_silent_on_success(self):
        """A cron entry that prints on every success mails the owner hourly."""
        assert run("--cron", code="204").stdout == ""

    def test_but_never_silent_on_failure(self):
        out = run("--cron", code="403")
        assert out.stderr.strip(), "a silent failure is how a timer dies unnoticed"


class TestTheDocumentationMatchesTheScript:
    def test_keeping_time_tells_the_reader_to_run_this_script(self):
        doc = (ROOT / "KEEPING-TIME.md").read_text()
        assert "scripts/keep-time.sh" in doc

    def test_it_does_not_also_carry_a_rival_curl_to_get_wrong(self):
        """Two ways to do it is two things to keep correct."""
        doc = (ROOT / "KEEPING-TIME.md").read_text()
        dispatch_curls = doc.count("/dispatches")
        assert dispatch_curls <= 1, (
            f"{dispatch_curls} copies of the dispatch call in the document; "
            f"the script is the one that is tested")
