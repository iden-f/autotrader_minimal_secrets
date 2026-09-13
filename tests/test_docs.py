"""The handoff documents, checked against the thing they describe.

Documentation rots silently, and a runbook that names a command that no longer
exists is worse than no runbook: it is read under pressure, by someone who has
forgotten everything, and it sends them somewhere that does not exist.

These are not style checks. Every assertion here is "this document claims X;
is X still true".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(".")
DOCS = {name: (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "ARCHITECTURE.md", "RUNBOOK.md", "DESIGN.md")}
ALL = "\n".join(DOCS.values())


class TestEveryCommandTheDocsTellYouToRun:
    def test_each_subcommand_exists(self):
        from autotrader import cli
        import argparse

        parser = cli.build_parser() if hasattr(cli, "build_parser") else None
        if parser is None:
            source = (ROOT / "autotrader/cli.py").read_text()
            known = set(re.findall(r'sub\.add_parser\(\s*"([\w-]+)"', source))
        else:
            known = {a for action in parser._actions
                     if isinstance(action, argparse._SubParsersAction)
                     for a in action.choices}
        assert known, "could not work out which subcommands exist"

        named = set(re.findall(r"python -m autotrader ([\w-]+)", ALL))
        named -= {"--no-colour"}
        missing = named - known
        assert not missing, f"the docs name commands that do not exist: {missing}"

    def test_the_flags_they_name_exist(self):
        source = (ROOT / "autotrader/cli.py").read_text()
        for flag in re.findall(r"python -m autotrader [\w-]+ (--[\w-]+)", ALL):
            assert f'"{flag}"' in source, flag


class TestEveryFileAndWorkflowTheDocsPointAt:
    @pytest.mark.parametrize("doc", sorted(DOCS))
    def test_the_workflows_named_exist(self, doc):
        here = Path(".github/workflows")
        gone = {
            # Named as history, not as a pointer. v1's workflow was renamed
            # because GitHub remembers "disabled" against a file path.
            "run_bot.yml",
            "poke-the-watcher.yml",   # lives in the sibling repository
        }
        for name in set(re.findall(r"`?([\w-]+\.yml)`?", DOCS[doc])) - gone:
            assert (here / name).exists(), f"{doc} names {name}"

    def test_the_modules_named_exist(self):
        for name in set(re.findall(r"\*\*`(\w+)`\*\*", DOCS["ARCHITECTURE.md"])):
            assert (ROOT / f"autotrader/{name}.py").exists(), name

    def test_the_paths_named_exist(self):
        for path in set(re.findall(r"`(docs/[\w./*-]+)`", ALL)):
            if "*" in path:
                # The directory has to be the one the code writes to. Whether
                # it currently holds files is a question about this morning's
                # market - it is empty the day the watched searches change,
                # and full again one check later.
                assert Path(path).parent.exists(), path
            else:
                assert Path(path).exists(), path

    def test_the_photo_directory_is_the_one_the_code_writes_to(self):
        """What the glob above used to prove by accident, proven on purpose."""
        from autotrader.thumbs import THUMB_DIR
        named = {p for p in re.findall(r"`(docs/[\w./*-]+)`", ALL) if "thumb" in p}
        assert named, "the docs stopped naming the photo directory"
        for path in named:
            assert Path(path).parent == THUMB_DIR, path

    def test_the_sibling_documents_exist(self):
        # \b at the front, or <name>.REJECTED.md matches as REJECTED.md.
        for name in set(re.findall(r"(?:^|[\s`(])([A-Z]+\.md)\b", ALL)):
            assert (ROOT / name).exists(), name


class TestTheMechanismsTheRunbookReliesOn:
    """Each of these is a sentence in RUNBOOK.md that has to stay true."""

    def test_the_capture_marker_is_the_one_the_workflow_looks_for(self):
        assert ".capture-raw" in DOCS["RUNBOOK.md"]
        assert ".capture-raw" in Path(".github/workflows/watch.yml").read_text()

    def test_the_kill_switch_is_the_one_the_pacemakers_read(self):
        assert "PACEMAKER-OFF" in DOCS["RUNBOOK.md"]
        for name in ("pacemaker.yml", "pacemaker-b.yml", "pacemaker-c.yml"):
            assert "PACEMAKER-OFF" in Path(f".github/workflows/{name}").read_text(), name

    def test_the_corrupt_state_file_is_named_correctly(self):
        from autotrader import state
        assert "state.corrupt.json" in DOCS["RUNBOOK.md"]
        assert "corrupt" in Path("autotrader/state.py").read_text()

    def test_the_control_actions_it_lists_are_the_real_ones(self):
        from autotrader import control
        listed = set(re.findall(r"`([a-z-]+)`",
                                DOCS["RUNBOOK.md"].split("Valid actions:")[1]
                                .split("\n\n")[0]))
        assert listed == set(control.ACTIONS), (
            f"runbook lists {sorted(listed)}, module has "
            f"{sorted(control.ACTIONS)}")

    def test_the_example_control_file_would_actually_apply(self):
        """The snippet a person copies at 11pm has to be valid."""
        from autotrader import control
        block = re.search(r"```json\n(.*?)```", DOCS["RUNBOOK.md"], re.S).group(1)
        items = control.parse(block)
        assert items and items[0]["action"] in control.ACTIONS

    def test_the_dispatch_call_names_this_repository_and_a_real_event(self):
        for doc in ("ARCHITECTURE.md", "RUNBOOK.md"):
            call = re.search(r'"event_type":"(\w+)"', DOCS[doc])
            assert call, doc
            watch = Path(".github/workflows/watch.yml").read_text()
            assert call.group(1) in watch, f"{doc}: watch.yml has no such type"

    def test_the_photo_fallback_wordings_are_the_ones_the_page_uses(self):
        app = Path("docs/app.js").read_text()
        for phrase in ("no photo", "photo not copied yet",
                       "not kept for hidden cars"):
            assert phrase in DOCS["RUNBOOK.md"], phrase
            assert phrase in app, f"the page no longer says {phrase!r}"

    def test_the_unstamped_worker_check_it_promises_exists(self):
        assert "__BUILD__" in DOCS["RUNBOOK.md"]
        assert "__BUILD__" in Path("tests/test_offline.py").read_text()

    def test_the_per_run_photo_cap_it_quotes_is_the_real_one(self):
        from autotrader import thumbs
        assert f"at most {thumbs.MAX_PER_RUN} new photos" in DOCS["RUNBOOK.md"]

    def test_the_request_budget_it_quotes_is_the_real_default(self):
        from autotrader.config import Config
        budget = Config.defaults().get("scraping.request_budget")
        assert f"({budget} by default)" in DOCS["ARCHITECTURE.md"]

    def test_the_run_history_depth_it_quotes_is_the_real_one(self):
        from autotrader.state import MAX_RUN_HISTORY
        assert f"last {MAX_RUN_HISTORY} run" in DOCS["ARCHITECTURE.md"]


class TestTheClaimsAboutBehaviour:
    def test_hidden_cars_really_are_kept_and_explained(self, tmp_path):
        """The claim is about what the bot does, not about what it holds today.

        This read the published data.json and required it to contain a hidden
        car, which made a documentation test depend on the market: the day the
        watched searches were swapped out, state was empty and the docs were
        suddenly "wrong". The behaviour is what the sentence promises, so the
        behaviour is what gets checked - on a bot built here, with a rule that
        hides one of two cars.
        """
        from autotrader import filters
        from autotrader.config import Config
        from autotrader.dashboard import build_payload
        from autotrader.listing import Listing
        from autotrader.state import State

        assert "kept and explained" in ALL
        cfg = Config.defaults(tmp_path / "config.json")
        search = cfg.add_search("https://www.autotrader.ca/cars/bmw/m3/?rcp=25", "M3")
        cfg.set("filters.max_price", 70000)
        state = State(path=tmp_path / "state.json")
        cars = [Listing(id="cheap", title="2016 BMW M3", price=62000,
                        price_source="detail", search_id=search.id),
                Listing(id="dear", title="2020 BMW M3 CS", price=119000,
                        price_source="detail", search_id=search.id)]
        kept, unpriced, dropped = filters.apply(cars, cfg.rules_for(search)["filters"])
        for listing in kept + unpriced:
            state.record(listing)
        for listing, reason in dropped:
            state.record(listing, filtered=True, filter_reason=reason)
        payload = build_payload(cfg, state, {})
        hidden = [l for l in payload["listings"] if l.get("filtered")]
        assert [l["id"] for l in hidden] == ["dear"], "the dear one should be hidden"
        assert all(l.get("filter_reason") for l in hidden)
        assert {l["id"] for l in payload["listings"]} == {"cheap", "dear"}, \
            "a hidden car must still be published, or it is dropped not hidden"

    def test_the_published_data_keeps_that_promise_too(self):
        """And when there is live data, it has to hold there as well."""
        data = json.loads(Path("docs/data.json").read_text())
        hidden = [l for l in data["listings"] if l.get("filtered")]
        assert all(l.get("filter_reason") for l in hidden), \
            "a car hidden with no reason given"

    def test_the_secret_scan_really_runs_before_the_write(self):
        source = Path("autotrader/dashboard.py").read_text()
        body = source.split("def write(")[1]
        assert body.index("find_secrets") < body.index("tmp.replace(path)")
