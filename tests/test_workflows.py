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


class TestThePacemakerStaysInsideTheRealCeiling:
    """The runner's job limit, measured rather than read.

    A probe job counted out loud until something stopped it: it reached
    minute 361 and was cancelled. So 360 is the wall. The shift was 180 - half
    of what the runner actually allows - because that number came from the
    documentation rather than from the machine.

    These assert the bounds hold against the measured number, so raising the
    shift again cannot quietly cross it, and so the thing that makes a
    pacemaker safe cannot be removed while nobody is looking.
    """

    CEILING = 360
    PACEMAKERS = ("pacemaker.yml", "pacemaker-b.yml", "pacemaker-c.yml")

    @staticmethod
    def job(name):
        import yaml
        from pathlib import Path
        data = yaml.safe_load(
            (Path(".github/workflows") / name).read_text(encoding="utf-8"))
        (job,) = data["jobs"].values()
        return job

    @pytest.mark.parametrize("name", PACEMAKERS)
    def test_the_timeout_is_under_the_measured_wall(self, name):
        timeout = int(self.job(name)["timeout-minutes"])
        assert timeout < self.CEILING, (
            f"{name}: a {timeout}-minute timeout is past the {self.CEILING} "
            f"the runner actually allows, so the job is cancelled rather than "
            f"finishing and saying what it did")

    @pytest.mark.parametrize("name", PACEMAKERS)
    def test_the_shift_finishes_before_its_own_backstop(self, name):
        job = self.job(name)
        shift = int(job["env"]["SHIFT_MINUTES"])
        timeout = int(job["timeout-minutes"])
        assert shift < timeout, (
            f"{name}: the shift ({shift}) has to end before the backstop "
            f"({timeout}), or the backstop is the thing being relied on")

    @pytest.mark.parametrize("name", PACEMAKERS)
    def test_it_beats_more_than_once(self, name):
        env = self.job(name)["env"]
        shift, interval = int(env["SHIFT_MINUTES"]), int(env["INTERVAL_MINUTES"])
        assert shift >= interval * 2, (
            f"{name}: a shift that fits one interval buys nothing over the "
            f"single check the firing could have dispatched directly")

    @pytest.mark.parametrize("name", PACEMAKERS)
    def test_every_way_of_stopping_it_is_still_there(self, name):
        """A pacemaker without all of these is a process nobody can stop."""
        from pathlib import Path
        source = (Path(".github/workflows") / name).read_text()
        assert "PACEMAKER-OFF" in source, "the kill switch"
        assert "STRIKES" in source, "the failed-dispatch limit"
        assert "timeout-minutes" in source, "the backstop"
        assert "SHIFT_MINUTES" in source, "its own deadline"

    @pytest.mark.parametrize("name", PACEMAKERS)
    def test_it_does_not_start_a_successor(self, name):
        """The line between a shift and something that outlives the decision
        to run it. A pacemaker that dispatches a pacemaker cannot be stopped
        from outside, and that is not a thing to leave in someone's repo."""
        from pathlib import Path
        source = (Path(".github/workflows") / name).read_text()
        body = "\n".join(l for l in source.splitlines()
                         if not l.strip().startswith("#"))
        import re
        # The sharp version: every workflow this one can start, by name. A
        # first attempt asserted the word "Pacemaker" was absent from the job
        # body and failed on the step summary's own heading - which is the
        # workflow printing its name, not starting anything.
        dispatched = set(re.findall(r"gh workflow run\s+(\S+)", body))
        assert dispatched, f"{name}: it dispatches nothing at all"
        assert dispatched <= {"watch.yml"}, (
            f"{name} can start {sorted(dispatched - {'watch.yml'})} - a "
            f"pacemaker that starts a pacemaker cannot be stopped from "
            f"outside, and that is not a thing to leave in someone's repo")

    def test_they_sit_on_different_minutes(self):
        """The whole reason there are three: independent chances at a runner.
        Three workflows on the same minutes are one workflow."""
        import re
        from pathlib import Path
        slots = {}
        for name in self.PACEMAKERS:
            source = (Path(".github/workflows") / name).read_text()
            found = re.findall(r"cron:\s*'([^']+)'", source)
            assert found, name
            slots[name] = {m.strip() for m in found[0].split()[0].split(",")}
        seen = list(slots.values())
        for i, a in enumerate(seen):
            for b in seen[i + 1:]:
                assert not (a & b), f"two pacemakers share minutes: {a & b}"
