"""What this bot costs to run, and refusing to cost more than it may.

The bot spent its first week justifying an expensive schedule with "the
repository is public, so the minutes are free". That was true - GitHub reports
zero billable milliseconds against every run of it - and it was still the
wrong way to decide. Free compute on someone else's machines is a bill one
settings change away from being real: make the repository private, move it
into an organisation with a policy, and sixteen hours of runner a day stops
being free without anything in this repository changing.

TWO KINDS OF MINUTE, COUNTED SEPARATELY.

On 13 September the account's 3,000 included minutes hit 100% with eighteen
days left in the month, and this bot said nothing, because the minutes that
exhausted them were spent in two private repositories it cannot see. What it
had been saying - "None of it draws on the allowance" - was true of its own
minutes and read like a statement about the account. It was safety it could
not prove.

So every minute is labelled WHEN IT IS SPENT, not when the ledger is read:

    exempt    this repository was public and on a standard runner at the time
    drawing   it was not, so the minute came out of the allowance
    unknown   the bot could not tell, which counts as drawing

A single flag on the whole month would relabel September retroactively the
day the repository changed. It does not, because the label is written into
the day's row beside the number.

And the guard says out loud what it cannot see: an allowance has one meter
per account and this bot watches one repository.

GitHub rounds every JOB up to a whole minute, so a 34-second check costs one
minute and two 34-second jobs cost two. Minutes are counted per job here for
the same reason.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# GitHub Free: 2,000 minutes a month. Pro: 3,000. Whatever the plan, the
# number belongs in config.json rather than here - this is only the fallback
# for a bot that has never been told.
DEFAULT_ALLOWANCE = 3000

# Stop at this share of the allowance, not at 100%. The projection is a
# straight line through a month that has not finished; leaving room means a
# bad estimate costs nothing rather than costing money.
DEFAULT_STOP_AT = 0.85

# Below this many days of data, a month-end projection is arithmetic rather
# than evidence: one busy afternoon on day one projects to 30 busy days.
MIN_DAYS_TO_PROJECT = 2

STOP_FILE = "BUDGET-STOP"

# The three labels a minute can carry, and the only ones. "unknown" is counted
# as drawing everywhere a decision is made - assuming you are charged and
# being wrong costs a sentence on a dashboard; assuming you are not and being
# wrong costs money.
EXEMPT, DRAWING, UNKNOWN = "exempt", "drawing", "unknown"

# Runner labels GitHub does not charge for on a public repository. A LARGER
# runner is billed even there, so a repository that moves onto one stops being
# exempt without its visibility changing. tests/test_budget.py asserts every
# runs-on in this repository is on this list.
FREE_ON_PUBLIC = frozenset({
    "ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04", "ubuntu-20.04",
    "windows-latest", "windows-2025", "windows-2022", "windows-2019",
    "macos-latest", "macos-15", "macos-14", "macos-13",
})


def _month_of(when: datetime) -> str:
    return f"{when.year:04d}-{when.month:02d}"


def _empty_day() -> dict[str, float]:
    return {EXEMPT: 0.0, DRAWING: 0.0, UNKNOWN: 0.0}


def _as_day(raw: Any) -> dict[str, float]:
    """One day's row, from either shape of ledger.

    The old format was a bare number per day with a single charged flag on the
    month. A number carries no label, so it becomes "unknown" rather than
    being assigned one now - which is the whole reason the format changed.
    """
    day = _empty_day()
    if isinstance(raw, dict):
        for key in (EXEMPT, DRAWING, UNKNOWN):
            try:
                day[key] = round(float(raw.get(key) or 0.0), 2)
            except (TypeError, ValueError):
                day[key] = 0.0
        return day
    try:
        day[UNKNOWN] = round(float(raw), 2)
    except (TypeError, ValueError):
        pass
    return day


@dataclass
class Ledger:
    """Minutes used this month, labelled by day, and what that projects to."""

    month: str = ""
    days: dict[str, dict[str, float]] = field(default_factory=dict)
    runs: int = 0
    allowance: int = DEFAULT_ALLOWANCE
    stop_at: float = DEFAULT_STOP_AT
    # How the NEXT minute will be labelled, and why. Not applied backwards:
    # see the module docstring.
    label: str = UNKNOWN
    why: str = "nothing has told this bot what kind of repository it is in"

    def _total(self, key: str) -> float:
        return round(sum(d.get(key, 0.0) for d in self.days.values()), 2)

    @property
    def exempt(self) -> float:
        return self._total(EXEMPT)

    @property
    def unknown(self) -> float:
        return self._total(UNKNOWN)

    @property
    def drawing(self) -> float:
        """Minutes that came out of the allowance, unlabelled ones included."""
        return round(self._total(DRAWING) + self.unknown, 2)

    @property
    def used(self) -> float:
        """Every minute this bot spent, whoever paid for it."""
        return round(self.exempt + self._total(DRAWING) + self.unknown, 2)

    @property
    def days_elapsed(self) -> int:
        """Days of the month that have actually produced data, not the date.

        A bot installed on the 20th has not used 19 days of allowance, and
        dividing by the day-of-month would tell it that it had.
        """
        return max(1, len(self.days))

    def days_in_month(self, now: datetime) -> int:
        return calendar.monthrange(now.year, now.month)[1]

    def days_remaining(self, now: datetime) -> int:
        return max(0, self.days_in_month(now) - now.day)

    def days_to_reset(self, now: datetime) -> int:
        """Until the allowance refills. The day the month turns counts."""
        return self.days_in_month(now) - now.day + 1

    def per_day(self) -> float:
        return round(self.drawing / self.days_elapsed, 2)

    def projected(self, now: datetime) -> float | None:
        """Allowance-drawing minutes by month end, or None if it is too early."""
        if self.days_elapsed < MIN_DAYS_TO_PROJECT:
            return None
        return round(self.drawing + self.per_day() * self.days_remaining(now), 1)

    def ceiling(self) -> float:
        return round(self.allowance * self.stop_at, 1)

    # The one sentence this guard is not allowed to leave out.
    BLIND_SPOT = (
        "This is what THIS repository spent. The allowance has one meter per "
        "account and this bot can see one repository, so it cannot tell you "
        "how much of the allowance is left."
    )

    def _short(self, projection: float | None) -> str:
        """One line for a tile. No caveat, no blind spot, no arithmetic."""
        drawing, exempt = self.drawing, self.exempt
        if not drawing:
            return (f"{exempt:,.0f} minute{'' if exempt == 1 else 's'} spent, "
                    f"none of {'it' if exempt == 1 else 'them'} on the allowance")
        bits = [f"{drawing:,.0f} of {self.allowance:,} this month"]
        if projection is not None:
            bits.append(f"about {projection:,.0f} by month end")
        if exempt:
            bits.append(f"{exempt:,.0f} more ran exempt")
        if self.unknown:
            bits.append(f"{self.unknown:,.0f} unlabelled, counted as drawing")
        return " \u00b7 ".join(bits)

    def verdict(self, now: datetime) -> dict[str, Any]:
        """Where this month stands, in numbers and in a sentence."""
        projection = self.projected(now)
        drawing, exempt = self.drawing, self.exempt
        over = projection is not None and projection > self.ceiling()
        spent_out = drawing >= self.ceiling()

        if not drawing:
            # Nothing of this bot's came out of the allowance. That is a fact
            # about this repository and is stated as one - the account's
            # allowance can still be exhausted by repositories this cannot
            # see, and on 13 September it was.
            state = "exempt"
            text = (f"{exempt:,.0f} runner minute"
                    f"{'' if exempt == 1 else 's'} this month, none of "
                    f"{'it' if exempt == 1 else 'them'} drawing on the "
                    f"allowance. {self.why.capitalize()}. " + self.BLIND_SPOT)
        elif spent_out:
            state = "stop"
            text = (f"{drawing:,.0f} of {self.allowance:,} allowance minutes "
                    f"are gone this month, past the {self.ceiling():,.0f} this "
                    f"bot allows itself. It has stopped checking.")
        elif over:
            state = "over"
            text = (f"At {self.per_day():,.1f} allowance minutes a day this "
                    f"month ends at about {projection:,.0f}, past the "
                    f"{self.ceiling():,.0f} this bot allows itself out of "
                    f"{self.allowance:,}. It will stop before it gets there.")
        elif projection is None:
            state = "early"
            text = (f"{drawing:,.1f} allowance minutes over "
                    f"{self.days_elapsed} day"
                    f"{'' if self.days_elapsed == 1 else 's'} - too little to "
                    f"project a month from. " + self.BLIND_SPOT)
        else:
            state = "ok"
            text = (f"{drawing:,.0f} allowance minutes used, about "
                    f"{projection:,.0f} by month end against {self.allowance:,}. "
                    + self.BLIND_SPOT)

        if exempt and drawing:
            text += (f" A further {exempt:,.0f} minute"
                     f"{'' if exempt == 1 else 's'} ran exempt.")
        if self.unknown:
            text += (f" {self.unknown:,.0f} minute"
                     f"{'' if self.unknown == 1 else 's'} could not be "
                     f"labelled and {'is' if self.unknown == 1 else 'are'} "
                     f"counted as drawing.")

        return {
            "month": self.month,
            "runs": self.runs,
            "days_elapsed": self.days_elapsed,
            "days_to_reset": self.days_to_reset(now),
            "exempt_minutes": exempt,
            "drawing_minutes": drawing,
            "unknown_minutes": self.unknown,
            "used": self.used,
            "per_day": self.per_day(),
            "projected": projection,
            "allowance": self.allowance,
            "ceiling": self.ceiling(),
            "label": self.label,
            "why": self.why,
            "blind_spot": self.BLIND_SPOT,
            # The tile's own line, without the caveat. Six lines of prose in
            # a stat tile is prose nobody reads, and the caveat matters too
            # much to be the fifth line of one - the page prints it once,
            # under the row, where it applies to every figure in it.
            "short": self._short(projection),
            "state": state,
            "text": text,
            # Whether the next check can run at all. An exhausted allowance
            # does not stop an exempt repository - proved on 13 September,
            # when the allowance hit 100% and this repository's scheduled
            # runs kept succeeding - so this is about the bot's OWN guard.
            "can_still_run": state != "stop",
            "should_stop": state == "stop",
        }


def load(state: Any, cfg: Any = None, *, now: datetime | None = None) -> Ledger:
    """The ledger for the current month, starting fresh when the month turns."""
    now = now or datetime.now(timezone.utc)
    raw = dict((state.data.get("actions") or {}))
    month = _month_of(now)
    if raw.get("month") != month:
        # A new month is a new allowance. The old one is not history worth
        # keeping in a file every run rewrites.
        raw = {"month": month, "days": {}, "runs": 0}
    allowance, stop_at = DEFAULT_ALLOWANCE, DEFAULT_STOP_AT
    if cfg is not None:
        allowance = int(cfg.get("budget.included_minutes", DEFAULT_ALLOWANCE)
                        or DEFAULT_ALLOWANCE)
        stop_at = float(cfg.get("budget.stop_at", DEFAULT_STOP_AT)
                        or DEFAULT_STOP_AT)
    label, why = label_for(cfg, state)
    days = {day: _as_day(v) for day, v in (raw.get("days") or {}).items()}
    return Ledger(month=month, days=days, runs=int(raw.get("runs") or 0),
                  allowance=allowance, stop_at=stop_at, label=label, why=why)


def label_for(cfg: Any, state: Any) -> tuple[str, str]:
    """How to label the minutes this run is about to spend, and why.

    Returns the label and the evidence for it, because a guard that says
    "exempt" without saying on what grounds is asking to be believed rather
    than checked.
    """
    told = None if cfg is None else cfg.get("budget.charged", None)
    if told is not None:
        return ((DRAWING, "config.json says these minutes are charged")
                if told else
                (EXEMPT, "config.json says these minutes are not charged"))

    repo = {}
    try:
        repo = dict(state.data.get("repo") or {})
    except (AttributeError, TypeError, ValueError):
        repo = {}
    visibility = str(repo.get("visibility") or "").strip().lower()
    runner = str(repo.get("runner") or "").strip().lower()

    if not visibility:
        return UNKNOWN, ("nothing has told this bot whether its repository is "
                         "public - it is not running inside Actions, or the "
                         "workflow stopped passing the answer through")
    if visibility != "public":
        return DRAWING, (f"GitHub reports this repository as {visibility}, and "
                         f"Actions on a {visibility} repository come out of "
                         f"the allowance")
    if runner and runner not in FREE_ON_PUBLIC:
        # The exemption is a property of the runner as well as the repository,
        # and a larger runner is billed on a public repo like any other.
        return DRAWING, (f"this repository is public, but {runner} is not one "
                         f"of the standard runners GitHub gives away")
    return EXEMPT, ("GitHub reports this repository as public and its jobs run "
                    "on standard runners, which GitHub does not bill")


def draws_on_the_allowance(cfg: Any, state: Any) -> bool:
    """Will the next minute come out of the allowance?

    Unknown counts as yes. Kept as a boolean for the callers that only need
    the decision; label_for gives the evidence with it.
    """
    return label_for(cfg, state)[0] != EXEMPT


def record(state: Any, minutes: float, *, cfg: Any = None,
           now: datetime | None = None) -> dict[str, Any]:
    """Add this run's minutes to the month and return where that leaves it."""
    now = now or datetime.now(timezone.utc)
    ledger = load(state, cfg, now=now)
    day = now.date().isoformat()
    row = ledger.days.setdefault(day, _empty_day())
    # Never negative, and never zero: a job that ran at all cost a minute.
    # Labelled here, with what is known NOW, and never relabelled.
    row[ledger.label] = round(row.get(ledger.label, 0.0) + max(1.0, minutes), 2)
    ledger.runs += 1
    state.data["actions"] = {"month": ledger.month, "days": ledger.days,
                             "runs": ledger.runs}
    return ledger.verdict(now)


def minutes_for(seconds: float, jobs: int = 1) -> float:
    """What GitHub charges for a job of this length: whole minutes, rounded up.

    A 34-second check is one minute, not 0.57 of one. Getting this wrong in
    the optimistic direction is how a budget guard reports that everything is
    fine while the bill says otherwise.
    """
    import math
    per_job = max(1, math.ceil(max(0.0, float(seconds)) / 60.0))
    return float(per_job * max(1, int(jobs)))
