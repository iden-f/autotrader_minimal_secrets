"""One word per idea, everywhere a person reads it.

DESIGN.md fixes six words. The value of fixing them is entirely in their being
the same in the digest, on the page and in the docs - a bot that says "hidden"
on the dashboard and "filtered out" in a Telegram message is two products
wearing one name, and the reader has to work out that they mean the same
thing.

These tests read the strings a person actually sees. Code identifiers are not
copy: `filter_reason` is a field name, `Change.RELISTED` is a constant, and
neither is ever rendered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BANNED = {
    "scan":        "check",
    "poll":        "check",
    "vehicle":     "listing",
    "filtered out": "hidden",
}

# "sold" is not on that list, and the first version of this file put it there.
#
# The rule is not "never write the word". "A car can be listed and sold
# between checks" is a true sentence about the market and the right thing to
# say. The rule is that the bot must never *label a listing* sold, because it
# cannot know that - a listing coming down means the seller stopped
# advertising it. So the check below is on the labels, not on the prose.
STATE_WORDS = ("sold", "sells", "purchased")

# Strings that are addresses or parameters rather than anything anyone reads.
NOT_COPY = re.compile(r"https?://|\?\w+=|^\w+/\w+$|^[\w.-]+\.\w{2,4}$")


def page_copy() -> list[tuple[str, str]]:
    """Every literal string the dashboard renders, with where it came from."""
    out = []
    js = Path("docs/app.js").read_text(encoding="utf-8")
    # Strip // comments and block comments: they are for whoever reads the
    # code, and several of them discuss the banned words on purpose.
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
    for match in re.finditer(r"'((?:[^'\\\n]|\\.){4,})'|`((?:[^`\\]|\\.){4,})`",
                             js, re.S):
        out.append(("docs/app.js", match.group(1) or match.group(2)))

    html = Path("docs/index.html").read_text(encoding="utf-8")
    body = html.split("<body", 1)[-1] if "<body" in html else html
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    for text in re.findall(r">([^<>{}]{4,})<", body):
        out.append(("docs/index.html", text))
    return out


def message_copy() -> list[tuple[str, str]]:
    """Strings the notification channels put in front of a person."""
    out = []
    for name in ("autotrader/render.py", "autotrader/notifiers.py",
                 "autotrader/insight.py", "autotrader/control.py"):
        source = Path(name).read_text(encoding="utf-8")
        source = re.sub(r'"""[\s\S]*?"""', "", source)     # docstrings
        source = re.sub(r"^\s*#.*$", "", source, flags=re.M)
        for match in re.finditer(r'"((?:[^"\\\n]|\\.){6,})"|f"((?:[^"\\\n]|\\.){6,})"',
                                 source):
            out.append((name, match.group(1) or match.group(2)))
    return out


def _hits(banned, strings):
    return [(where, text) for where, text in strings
            if not NOT_COPY.search(text)
            and re.search(rf"\b{re.escape(banned)}\b", text, re.I)]


@pytest.mark.parametrize("banned,instead", sorted(BANNED.items()))
def test_the_page_does_not_use_it(banned, instead):
    hits = _hits(banned, page_copy())
    assert not hits, f"say {instead!r}: {hits[:3]}"


@pytest.mark.parametrize("banned,instead", sorted(BANNED.items()))
def test_the_messages_do_not_use_it(banned, instead):
    hits = _hits(banned, message_copy())
    assert not hits, f"say {instead!r}: {hits[:3]}"


def test_no_label_claims_a_car_was_sold():
    """The bot cannot know. A listing coming down is a seller who stopped
    advertising, which is not the same thing and matters to a buyer."""
    js = Path("docs/app.js").read_text(encoding="utf-8")
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)

    labels = []
    block = re.search(r"const KIND = \{(.*?)\n\};", js, re.S).group(1)
    labels += re.findall(r"(?:label|group|rule):\s*'([^']+)'", block)
    labels += re.findall(r"el\('(?:span|b|button)', '(?:flag|chip|tag)[^']*', '([^']+)'", js)

    from autotrader.state import Change
    from autotrader.listing import Listing
    for kind in (v for k, v in vars(Change).items()
                 if k.isupper() and isinstance(v, str)):
        # Both shapes: a change with prices to compare and one without, since
        # several kinds word themselves differently for each.
        for prices in (({}, ), ({"old_price": 90000, "new_price": 86000}, )):
            change = Change(kind, Listing(id="x", url="u", price=86000),
                            **prices[0])
            labels.append(change.describe())

    for label in labels:
        for word in STATE_WORDS:
            assert not re.search(rf"\b{word}\b", label, re.I), \
                f"a label says {word!r}: {label!r}"


def test_the_documents_do_not_use_it_in_their_own_voice():
    """Quoted UI copy and the design rules themselves are exempt."""
    for name in ("README.md", "ARCHITECTURE.md", "RUNBOOK.md"):
        text = Path(name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().startswith(("|", ">")) or "`" in line:
                continue
            for banned in ("scan", "poll", "vehicle"):
                assert not re.search(rf"\b{banned}\b", line, re.I), \
                    f"{name}: {line.strip()[:80]}"


class TestOneNameForEachThing:
    def test_every_event_kind_the_bot_emits_has_page_copy(self):
        """A kind with no label renders as an empty chip."""
        insight = Path("autotrader/insight.py").read_text()
        emitted = set(re.findall(r'add\("(\w+)"', insight))
        block = re.search(r"const KIND = \{(.*?)\n\};",
                          Path("docs/app.js").read_text(), re.S).group(1)
        known = set(re.findall(r"^\s*(\w+):", block, re.M))
        assert emitted <= known, emitted - known

    def test_every_change_kind_the_runner_queues_has_digest_copy(self):
        """A kind with no line in render.py falls through to a bare price."""
        from autotrader.state import Change
        render = Path("autotrader/render.py").read_text()
        kinds = {v for k, v in vars(Change).items()
                 if k.isupper() and isinstance(v, str)}
        for kind in kinds:
            const = next(k for k, v in vars(Change).items() if v == kind)
            assert f"Change.{const}" in render, f"render.py never mentions {kind}"

    def test_the_control_actions_the_page_sends_all_exist(self):
        from autotrader import control
        js = Path("docs/app.js").read_text()
        js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
        for action in re.findall(r"action: '([\w-]+)'", js):
            assert action in control.ACTIONS, action

    def test_sentence_case_on_the_page_headings(self):
        """No Title Case. A heading is a sentence, not a sign."""
        html = Path("docs/app.js").read_text()
        html = re.sub(r"^\s*//.*$", "", html, flags=re.M)
        for heading in re.findall(r"<h[12][^>]*>([^<${}]{6,})</h[12]>", html):
            words = [w for w in heading.split() if w.isalpha() and len(w) > 3]
            capped = [w for w in words[1:] if w[0].isupper()]
            assert len(capped) <= 1, f"Title Case: {heading!r}"


class TestOneIsNotPlural:
    """"1 photos" on a card, "2 day(s)" in a note. Small, and the kind of
    small that makes a page read like output rather than like writing."""

    def test_the_page_never_hardcodes_a_plural_after_a_count(self):
        js = Path("docs/app.js").read_text()
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
        # A ${…} immediately followed by a space and a word ending in s, with
        # no conditional suffix anywhere in the same template piece.
        for match in re.finditer(r"\$\{[^{}]{1,60}\}\s(\w+s)\b(?!\s*\$\{)", js):
            word = match.group(1)
            if word in ("is", "was", "has", "as", "its", "this", "says",
                        "checks", "hours", "days", "minutes", "cars", "rules",
                        "searches", "listings", "photos", "half",
                        # A car with exactly one kilometre on it does not
                        # happen, and the reader of this string is a screen
                        # reader announcing an odometer.
                        "kilometres"):
                continue        # counted elsewhere, or not a count at all
            raise AssertionError(f"possible hardcoded plural: {match.group(0)!r}")

    def test_the_counts_that_can_be_one_are_conditional(self):
        js = Path("docs/app.js").read_text()
        for phrase in ("photo${", "day${", "search${"):
            assert phrase in js, f"{phrase} is not pluralised conditionally"

    def test_no_programmer_pluralisation_in_the_documents(self):
        """"2 day(s)" is a programmer talking to themselves in public."""
        for name in ("README.md", "ARCHITECTURE.md", "RUNBOOK.md", "DESIGN.md"):
            assert "(s)" not in Path(name).read_text(), name

    def test_none_in_the_message_strings_either(self):
        """Deliberately not applied to docs/app.js.

        The page's copy lives in backtick template literals that span code,
        so the string extractor at the top of this file cannot separate a
        sentence from the JavaScript around it - and a rule of "no (s)"
        flagged esc(s) and appendChild(s), which are calls. The page is
        covered by the conditional-plural test above instead, which checks the
        counts rather than the characters.
        """
        for where, text in message_copy():
            assert "(s)" not in text, (where, text[:90])

    def test_none_in_the_terminal_output_either(self):
        """A terminal is where "3 listing(s)" is most at home and least
        excusable - it is exactly the script's-output look this was meant to
        stop having."""
        import re as _re
        for name in ("autotrader/cli.py", "autotrader/runner.py"):
            source = Path(name).read_text()
            source = _re.sub(r'"""[\s\S]*?"""', "", source)
            source = _re.sub(r"^\s*#.*$", "", source, flags=_re.M)
            assert "(s)" not in source, name


class TestOneFormatterPerIdea:
    """Every number a person reads goes through one function.

    Written after "$1113 /1000km" appeared beside "$69,000" on the same card -
    the same currency formatted two ways, one line apart - and after a
    "runner minutes" tile used a bare toLocaleString(), which is the browser's
    locale rather than this page's, so the same figure would render "3,000" in
    one tile and "3.000" in the next on a German phone.

    It is also a guard against a specific way of failing to fix things: the
    edit that was supposed to route per_1000km through money() silently
    matched nothing and was reported as done.
    """

    def app(self) -> str:
        return Path("docs/app.js").read_text(encoding="utf-8")

    def code_lines(self):
        """Lines of real code, with comments stripped."""
        out = []
        in_block = False
        for line in self.app().splitlines():
            stripped = line.strip()
            if in_block:
                if "*/" in stripped:
                    in_block = False
                continue
            if stripped.startswith("/*"):
                in_block = "*/" not in stripped
                continue
            if stripped.startswith("//"):
                continue
            out.append(line)
        return out

    def test_no_currency_is_formatted_by_hand(self):
        """`$${x}` in a template is money() not being used."""
        import re
        bad = [l.strip() for l in self.code_lines()
               if re.search(r"\$\$\{", l)]
        assert not bad, bad

    def test_every_locale_is_named(self):
        """A bare toLocaleString is the reader's locale, not the page's."""
        import re
        bad = [l.strip() for l in self.code_lines()
               if re.search(r"toLocale\w*\(\s*[\)\[]", l)]
        assert not bad, bad

    # The three functions allowed to group a number, plus the one tooltip that
    # formats a date rather than a figure.
    def test_only_three_functions_group_a_number(self):
        """A fourth way of writing a number is a fourth way of writing it
        differently. If this fails, either route the new call through money(),
        num() or signed(), or add it here on purpose.
        """
        import re
        calls = [l.strip() for l in self.code_lines() if "toLocaleString" in l]
        # money() and num() define the grouping; signed() reuses it; the
        # coverage strip's tooltip formats a DATE, not a figure.
        assert len(calls) == 5, calls
        assert sum(1 for c in calls if "cell.title" in c) == 1, calls

    def test_the_slot_word_is_derived_not_typed(self):
        """"half-hours" was typed into five strings while the schedule
        happened to be half-hourly, and stayed there when it stopped."""
        import re
        for line in self.code_lines():
            if "half-hour" in line:
                assert "slotWord" in line or "mins === 30" in line, line.strip()

    def test_python_says_it_the_same_way(self):
        """The alert and the page describe one schedule.

        Read from the AST rather than from the text, so a docstring saying
        "half-hours" - like this one - is not mistaken for a string somebody
        is sent.
        """
        import ast
        tree = ast.parse(Path("autotrader/events.py").read_text())
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docs.add(doc)
        # _slot_word is the one function allowed to know the word. That is
        # the whole point: it was in five places.
        allowed = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_slot_word":
                allowed = {n for n in ast.walk(node)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "half-hour" in node.value and node.value not in docs
                    and node not in allowed):
                raise AssertionError(
                    f"a literal 'half-hour' at line {node.lineno} - "
                    f"_slot_word is where that word lives")
