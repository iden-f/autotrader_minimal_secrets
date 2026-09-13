"""The workflow files themselves, checked the way GitHub checks them.

Two of these shipped broken and nothing noticed, because a workflow that does
not compile does not fail a job - it fails the whole run before any job
exists, so there is no log, no annotation in anything the bot reads, and no
test touches it. The watcher was off for eleven minutes and the soak had been
unstartable for five hours before either was spotted by hand.

Both were ordinary mistakes that a parser catches instantly:

* a step given two `env:` blocks, so the second silently replaced the first
  under a plain YAML load and made the file invalid under GitHub's;
* `hashFiles()` in a job-level `if`, where it is not available - which is a
  compile error for the file, not a false condition for the job.

So the suite reads them now. It is not a full implementation of the Actions
expression language; it is the handful of shapes that have actually cost this
repository a run.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(".github/workflows")).glob("*.yml"))

# Functions GitHub only exposes to a step. Called from a job-level `if`, the
# workflow does not compile.
STEP_ONLY_FUNCTIONS = ("hashFiles",)
# Contexts that do not exist yet when a job's `if` is evaluated.
JOB_IF_FORBIDDEN_CONTEXTS = ("steps.", "job.", "runner.", "env.")


class _NoDuplicates(yaml.SafeLoader):
    """A loader that refuses what GitHub refuses.

    PyYAML's default is to let a later key win, which is exactly why the
    duplicate `env:` looked fine locally and killed the watcher on push.
    """


def _mapping(loader, node, deep=False):
    seen: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise AssertionError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            )
        seen[key] = loader.construct_object(value_node, deep=deep)
    return seen


_NoDuplicates.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_NoDuplicates)


def test_there_are_workflows_to_check():
    assert WORKFLOWS, "no workflow files found - is the test running from the repo root?"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_parses_with_no_duplicate_keys(path: Path):
    """The watcher bug: a second `env:` on the same step."""
    doc = _load(path)
    assert isinstance(doc, dict), f"{path} is not a mapping"
    # `on` is the YAML 1.1 boolean True once parsed, which is fine - it just
    # has to be there.
    assert "jobs" in doc, f"{path} has no jobs"
    assert True in doc or "on" in doc, f"{path} has no triggers"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_job_conditions_only_use_what_a_job_can_see(path: Path):
    """The soak bug: hashFiles() in a job-level `if`.

    It reads as a condition that is simply false. It is not - the file does
    not compile, so committing the marker the soak waits for would have
    started nothing at all.
    """
    for name, job in (_load(path).get("jobs") or {}).items():
        condition = str(job.get("if", ""))
        if not condition:
            continue
        for fn in STEP_ONLY_FUNCTIONS:
            assert f"{fn}(" not in condition, (
                f"{path.name}: job '{name}' calls {fn}() in its `if`. That is "
                f"only available to a step, and using it here makes the whole "
                f"workflow invalid - every run fails before any job starts."
            )
        for ctx in JOB_IF_FORBIDDEN_CONTEXTS:
            assert ctx not in condition, (
                f"{path.name}: job '{name}' reads `{ctx}` in its `if`, which "
                f"does not exist when a job's condition is evaluated"
            )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_can_actually_run(path: Path):
    for name, job in (_load(path).get("jobs") or {}).items():
        assert "runs-on" in job or "uses" in job, (
            f"{path.name}: job '{name}' has neither runs-on nor uses")
        # A job that can hang forever is a job that holds a concurrency group
        # forever, which is how one wedged soak silenced the watcher.
        if "runs-on" in job:
            assert "timeout-minutes" in job, (
                f"{path.name}: job '{name}' has no timeout-minutes")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_needs_point_at_jobs_that_exist(path: Path):
    jobs = _load(path).get("jobs") or {}
    for name, job in jobs.items():
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for dep in needs:
            assert dep in jobs, (
                f"{path.name}: job '{name}' needs '{dep}', which is not a job "
                f"in this file")
        # And a condition that names a job it does not wait for is reading an
        # output that will never be there.
        for referenced in re.findall(r"needs\.([A-Za-z0-9_-]+)",
                                     str(job.get("if", ""))):
            assert referenced in needs, (
                f"{path.name}: job '{name}' reads needs.{referenced} but does "
                f"not list it in `needs`")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_local_reusable_workflows_exist(path: Path):
    for name, job in (_load(path).get("jobs") or {}).items():
        uses = str(job.get("uses", ""))
        if uses.startswith("./"):
            assert Path(uses[2:]).is_file(), (
                f"{path.name}: job '{name}' reuses {uses}, which is not there")


def test_the_watcher_is_still_scheduled():
    """The one workflow whose absence is the bot not existing."""
    doc = _load(Path(".github/workflows/watch.yml"))
    triggers = doc.get(True) or doc.get("on") or {}
    assert "schedule" in triggers, "the watcher has no schedule"
    assert triggers["schedule"], "the watcher's schedule is empty"


def _triggers_on(doc: dict) -> list[str]:
    """Names of the workflows whose completion starts this one."""
    on = doc.get(True) or doc.get("on") or {}
    if not isinstance(on, dict):
        return []
    ran = on.get("workflow_run") or {}
    return [str(n) for n in (ran.get("workflows") or [])]


GUARD = "github.event.workflow_run.event"


def _job_guarded(name: str, jobs: dict, seen: frozenset = frozenset()) -> bool:
    """Will this job stay put when the chain it did not ask for arrives?

    Either it asks about the triggering run itself, or it waits on a job that
    does and stands down when that job stands down. `always()` alone is not
    enough: a job that runs even when its dependency was skipped still
    completes the workflow, and completing is what starts the next one.
    """
    job = jobs.get(name) or {}
    condition = str(job.get("if", ""))
    if GUARD in condition:
        return True
    needs = job.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    return bool(needs) and all(
        f"needs.{dep}.result != 'skipped'" in condition
        and dep not in seen
        and _job_guarded(dep, jobs, seen | {name})
        for dep in needs)


def _guarded(doc: dict) -> bool:
    """Does every job here refuse a chained trigger it did not want?"""
    jobs = doc.get("jobs") or {}
    return bool(jobs) and all(_job_guarded(name, jobs) for name in jobs)


def test_chained_workflows_cannot_bounce_forever():
    """The watcher starts the ledger, and the ledger starts the watcher.

    That arrangement exists because GitHub serves neither schedule reliably
    and they are not dropped together - but on its own it is two workflows
    taking turns for ever, a runner each, until somebody notices the bill.
    One side of any such loop has to refuse a chain it did not want.
    """
    docs = {}
    for path in WORKFLOWS:
        doc = _load(path)
        docs[str(doc.get("name") or path.stem)] = doc

    starts: dict[str, set[str]] = {name: set() for name in docs}
    for name, doc in docs.items():
        for upstream in _triggers_on(doc):
            if upstream in starts:
                starts[upstream].add(name)

    def cycle_from(start: str) -> list[str] | None:
        stack = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for nxt in starts.get(node, ()):
                if nxt == start:
                    return path
                if nxt not in path:
                    stack.append((nxt, path + [nxt]))
        return None

    seen: set[frozenset[str]] = set()
    for name in docs:
        loop = cycle_from(name)
        if not loop or frozenset(loop) in seen:
            continue
        seen.add(frozenset(loop))
        assert any(_guarded(docs[n]) for n in loop), (
            "workflow_run loop with nothing to break it: "
            + " -> ".join(loop + [loop[0]])
            + ". One of them must gate every job on "
            "github.event.workflow_run.event, so only a run the scheduler "
            "started can start the next one."
        )


class TestNothingHoldsARunner:
    """The pattern this repository spent a week paying for, kept out.

    Three "pacemaker" workflows each held a GitHub runner for up to five and a
    half hours, sleeping in a loop to dispatch checks on a timer, because
    GitHub drops scheduled runs and a job that is already alive can ask for
    work reliably. It worked. Measured over 24 hours it also cost up to
    sixteen hours of runner a day - for a watch whose checks total about
    twelve minutes - and it was justified in writing with "the minutes are
    free because the repository is public".

    That justification is true and it is not a property of this code: it is a
    repository setting that one click changes. These tests are what stops the
    idea coming back the next time the schedule looks thin.
    """

    # GitHub cancels a job at 360 minutes - measured, by a probe that counted
    # out loud and got to 361. Nothing here should want a tenth of it.
    RUNNER_CEILING = 360
    SANE_CEILING = 30

    @staticmethod
    def workflows():
        from pathlib import Path
        return sorted(Path(".github/workflows").glob("*.yml"))

    def test_the_pacemakers_are_gone(self):
        names = {p.name for p in self.workflows()}
        assert not (names & {"pacemaker.yml", "pacemaker-b.yml",
                             "pacemaker-c.yml"}), sorted(names)

    def test_nothing_long_running_is_on_a_timer(self):
        """A long job is fine. A long job GitHub starts by itself is not.

        The soak deliberately holds a runner for hours to watch behaviour over
        time - that is its whole job, it is asked for by hand, and it is
        bounded. What must never exist again is a job with a big ceiling and a
        cron in front of it.
        """
        import re, yaml
        for path in self.workflows():
            body = path.read_text()
            longest = max((int(t) for t in
                           re.findall(r"timeout-minutes:\s*(\d+)", body)),
                          default=0)
            if longest <= self.SANE_CEILING:
                continue
            doc = yaml.safe_load(body)
            on = doc[True] if True in doc else doc.get("on") or {}
            triggers = set(on) if isinstance(on, dict) else {on}
            assert "schedule" not in triggers, (
                f"{path.name} may run for {longest} minutes AND is on a "
                f"schedule. Every minute of it is billed.")
            assert longest < self.RUNNER_CEILING, (
                f"{path.name}'s {longest}-minute ceiling is past the "
                f"{self.RUNNER_CEILING} the runner allows, so it is cancelled "
                f"rather than finishing.")

    def test_a_scheduled_job_is_minutes_not_hours(self):
        import re, yaml
        for path in self.workflows():
            body = path.read_text()
            doc = yaml.safe_load(body)
            on = doc[True] if True in doc else doc.get("on") or {}
            if not isinstance(on, dict) or "schedule" not in on:
                continue
            for timeout in re.findall(r"timeout-minutes:\s*(\d+)", body):
                assert int(timeout) <= self.SANE_CEILING, (
                    f"{path.name} is scheduled and allows a job to run for "
                    f"{timeout} minutes")

    def test_nothing_sleeps_its_way_through_a_shift(self):
        """Retry backoff is seconds. A pacemaker is minutes, in a loop."""
        import re
        for path in self.workflows():
            for line in path.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "sleep" not in stripped:
                    continue
                assert "INTERVAL_MINUTES" not in stripped, f"{path.name}: {stripped}"
                literal = re.search(r"\bsleep\s+(\d+)\b", stripped)
                if literal:
                    assert int(literal.group(1)) <= 60, f"{path.name}: {stripped}"
                assert not re.search(r"\bsleep\s+\$\(\(\s*\w+\s*\*\s*60", stripped), \
                    f"{path.name}: {stripped}"

    def test_nothing_starts_the_watcher_on_a_timer(self):
        """One-shot dispatches are fine - control.yml asks for a check after
        it applies a change, which is a person's action. A dispatch inside a
        loop is a clock, and a clock is a held runner."""
        for path in self.workflows():
            body = path.read_text()
            if "gh workflow run watch.yml" not in body:
                continue
            assert path.name in {"watch.yml", "control.yml"}, (
                f"{path.name} starts the watcher")
            after = body[body.index("gh workflow run watch.yml"):]
            assert "while" not in after.split("\n")[0], f"{path.name}: in a loop"
            assert "INTERVAL" not in body, f"{path.name} dispatches on a timer"

    def test_the_watcher_is_one_job(self):
        """Every job rounds up to a whole minute, so two jobs is two minutes."""
        import yaml
        from pathlib import Path
        doc = yaml.safe_load(Path(".github/workflows/watch.yml").read_text())
        assert list(doc["jobs"]) == ["check"], list(doc["jobs"])

    def test_the_schedule_asks_for_what_the_config_expects(self):
        """The interval in config.json has to be the one the cron produces,
        or every coverage number on the dashboard is measured against a
        schedule that does not exist."""
        import re, yaml
        from pathlib import Path
        from autotrader.config import Config
        doc = yaml.safe_load(Path(".github/workflows/watch.yml").read_text())
        on = doc[True] if True in doc else doc["on"]
        (cron,) = [c["cron"] for c in on["schedule"]]
        hours = re.match(r"^\S+\s+\S*\*/(\d+)", cron)
        assert hours, f"cannot read an interval out of {cron!r}"
        asked = int(hours.group(1)) * 60
        for where in (Config.defaults(), Config.load("config.json")
                      if Path("config.json").exists() else Config.defaults()):
            expected = int(where.get("health.expected_interval_minutes"))
            assert expected == asked, (
                f"the cron asks for a check every {asked} minutes and the "
                f"config expects one every {expected}")
