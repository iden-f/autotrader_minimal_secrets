#!/usr/bin/env sh
# Run the whole suite as if it were another date, in another timezone.
#
# WHY: this project has shipped three time bombs. A test seeded "195 minutes
# times the day of the month" and crossed a ceiling the morning the date rolled
# from the 13th to the 14th - it failed with no code change at all. Another
# compared a hardcoded '2026-09-13' against a live clock. A third computed
# quiet hours in the host's local zone because a fallback used a naive
# datetime.now().
#
# None of those are findable by running the suite once, today, in UTC. So the
# suite is run at the dates and in the zones where time arithmetic breaks:
# month ends, a leap day, a year boundary, DST transitions, and the two
# extremes of the UTC offset range including a 45-minute one.
#
# Usage:  sh scripts/time-gate.sh            (all combinations)
#         sh scripts/time-gate.sh -q         (only report failures)
set -eu

PY="${PY:-.venv/bin/python}"
QUIET=0
[ "${1:-}" = "-q" ] && QUIET=1

# Dates chosen because each one has broken something somewhere, once.
#   month end / next day    - "days remaining" arithmetic, per-day ledgers
#   leap day                - date construction that assumes 28 days
#   new year's eve, UTC+14  - already tomorrow somewhere, still last year here
#   31st                    - months that have no 31st
DATES="2026-09-30T23:58:00Z 2026-10-01T00:02:00Z 2028-02-29T12:00:00Z
       2026-12-31T23:59:00Z 2027-01-01T00:01:00Z 2026-01-31T12:00:00Z
       2026-03-29T01:30:00Z 2026-11-01T05:30:00Z"

# Zones chosen for the extremes and the awkward offsets.
#   Kiritimati  UTC+14, the furthest ahead of UTC any place is
#   Niue        UTC-11, the furthest behind
#   Kathmandu   UTC+05:45, a 45-minute offset
#   Chatham     UTC+12:45/13:45, 45 minutes AND daylight saving
#   New_York    ordinary DST, where most such bugs are first noticed
ZONES="UTC Pacific/Kiritimati Pacific/Niue Asia/Kathmandu Pacific/Chatham America/New_York"

fails=0
runs=0
for tz in $ZONES; do
  for when in $DATES; do
    runs=$((runs + 1))
    [ "$QUIET" -eq 1 ] || printf '%-22s %s ... ' "$tz" "$when"
    if out=$(TZ="$tz" AUTOTRADER_NOW="$when" "$PY" -m pytest tests -q -x \
              --deselect tests/test_dashboard_layout.py 2>&1); then
      [ "$QUIET" -eq 1 ] || printf 'ok\n'
    else
      fails=$((fails + 1))
      [ "$QUIET" -eq 1 ] && printf '%-22s %s ... ' "$tz" "$when"
      printf 'FAILED\n'
      printf '%s\n' "$out" | grep -E '^(FAILED|E  )' | head -6
    fi
  done
done

printf '\n%d combinations, %d failed\n' "$runs" "$fails"
[ "$fails" -eq 0 ] || exit 1
