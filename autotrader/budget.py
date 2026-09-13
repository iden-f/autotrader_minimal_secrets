"""What this bot costs to run, and refusing to cost more than it may.

The bot spent its first week justifying an expensive schedule with "the
repository is public, so the minutes are free". That was true - GitHub reports
zero billable milliseconds against every run of it - and it was still the
wrong way to decide. Free compute on someone else's machines is a bill one
settings change away from being real: make the repository private, move it
into an organisation with a policy, and sixteen hours of runner a day stops
being free without anything in this repository changing.

So the bot now counts what it uses, projects the month, and stops itself
before it spends past an allowance. It counts WALL CLOCK runner minutes and
treats every one of them as billable, which over-counts by exactly the amount
the public-repository exemption is worth. That is the conservative direction:
the guard trips early on a public repo and on time on a private one.

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


def _month_of(when: datetime) -> str:
    return f"{when.year:04d}-{when.month:02d}"


@dataclass
class Ledger:
    """Minutes used this month, by day, and what that projects to."""

    month: str = ""
    days: dict[str, float] = field(default_factory=dict)
    runs: int = 0
    allowance: int = DEFAULT_ALLOWANCE
    stop_at: float = DEFAULT_STOP_AT
    # Whether these minutes draw on the allowance at all. False on a public
    # repository, where GitHub charges nothing for Actions - confirmed against
    # 705 runs of this one, every one returning billable {} or total_ms 0.
    charged: bool = True

    @property
    def used(self) -> float:
        return round(sum(self.days.values()), 2)

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

    def per_day(self) -> float:
        return round(self.used / self.days_elapsed, 2)

    def projected(self, now: datetime) -> float | None:
        """Month-end total at the current rate, or None if it is too early."""
        if self.days_elapsed < MIN_DAYS_TO_PROJECT:
            return None
        return round(self.used + self.per_day() * self.days_remaining(now), 1)

    def ceiling(self) -> float:
        return round(self.allowance * self.stop_at, 1)

    def verdict(self, now: datetime) -> dict[str, Any]:
        """Where this month stands, in numbers and in a sentence."""
        projection = self.projected(now)
        over = projection is not None and projection > self.ceiling()
        spent_out = self.used >= self.ceiling()

        if not self.charged:
            # Exempt: counted, shown, never alarming. The first version of
            # this treated every minute as billable "to be conservative", and
            # the effect was a guard that would shout about an allowance
            # nothing was drawing on. A warning that fires when nothing is
            # wrong is a warning that gets muted, and then it is not a guard.
            return {
                "month": self.month, "used": self.used, "runs": self.runs,
                "per_day": self.per_day(), "days_elapsed": self.days_elapsed,
                "projected": projection, "allowance": self.allowance,
                "ceiling": self.ceiling(), "charged": False,
                "state": "exempt",
                "text": (f"{self.used:,.0f} runner minute"
                         f"{'' if self.used == 1 else 's'} this month"
                         + (f", about {projection:,.0f} by month end"
                            if projection is not None else "")
                         + ". None of it draws on the allowance: GitHub does "
                           "not charge Actions on a public repository."),
                "should_stop": False,
            }
        if spent_out:
            state, text = "stop", (
                f"{self.used:,.0f} of {self.allowance:,} minutes are gone this "
                f"month, past the {self.ceiling():,.0f} this bot allows itself. "
                f"It has stopped checking.")
        elif over:
            state, text = "over", (
                f"At {self.per_day():,.1f} minutes a day this month ends at "
                f"about {projection:,.0f} minutes, past the "
                f"{self.ceiling():,.0f} this bot allows itself out of "
                f"{self.allowance:,}. It will stop before it gets there.")
        elif projection is None:
            state, text = "early", (
                f"{self.used:,.1f} minutes used over {self.days_elapsed} day"
                f"{'' if self.days_elapsed == 1 else 's'} - too little to "
                f"project a month from.")
        else:
            state, text = "ok", (
                f"{self.used:,.0f} minutes used, about {projection:,.0f} by "
                f"month end against an allowance of {self.allowance:,}.")
        return {
            "month": self.month,
            "used": self.used,
            "charged": True,
            "runs": self.runs,
            "per_day": self.per_day(),
            "days_elapsed": self.days_elapsed,
            "projected": projection,
            "allowance": self.allowance,
            "ceiling": self.ceiling(),
            "state": state,
            "text": text,
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
    return Ledger(month=month, days=dict(raw.get("days") or {}),
                  runs=int(raw.get("runs") or 0), allowance=allowance,
                  stop_at=stop_at, charged=draws_on_the_allowance(cfg, state))


def draws_on_the_allowance(cfg: Any, state: Any) -> bool:
    """Do this repository's Actions minutes come out of the allowance?

    Public repositories are exempt - confirmed against 705 runs of this one,
    every one of which returned ``billable: {}`` or ``total_ms: 0``. The
    workflow passes GitHub's own answer through, so this is an observed fact
    rather than a setting somebody has to keep in step with reality.

    The default when nothing is known is True. Assuming you are charged and
    being wrong costs a sentence on a dashboard; assuming you are not and
    being wrong costs money.
    """
    told = None if cfg is None else cfg.get("budget.charged", None)
    if told is not None:
        return bool(told)
    visibility = ""
    try:
        visibility = str((state.data.get("repo") or {}).get("visibility") or "")
    except AttributeError:
        pass
    if visibility:
        return visibility.lower() != "public"
    return True


def record(state: Any, minutes: float, *, cfg: Any = None,
           now: datetime | None = None) -> dict[str, Any]:
    """Add this run's minutes to the month and return where that leaves it."""
    now = now or datetime.now(timezone.utc)
    ledger = load(state, cfg, now=now)
    day = now.date().isoformat()
    # Never negative, and never zero: a job that ran at all cost a minute.
    ledger.days[day] = round(ledger.days.get(day, 0.0) + max(1.0, minutes), 2)
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
