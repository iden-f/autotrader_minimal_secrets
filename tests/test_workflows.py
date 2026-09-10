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
