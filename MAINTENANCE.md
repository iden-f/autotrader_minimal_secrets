# What will rot, and when

Nothing here needs doing on a schedule. This is a list of the things that
decay on their own, roughly when each one is due, and how you will find out —
because for half of them the bot tells you, and for the other half it cannot.

If you read only one line: **the two that will actually bite you are the
personal access token expiring and AutoTrader changing its pages.** The
second one announces itself. The first one does not — it is the only failure
here that can be completely silent, and it is also the only one with a date
you can write down in advance. Write it down.

---

## The bot tells you

These have an alarm. You do not have to remember them; you have to not
ignore the message when it comes.

### The parser falls behind the site — months to a year

**How you find out:** "AutoTrader changed how its pages are built", and then,
if it gets worse, "AutoTrader watcher needs attention".

AutoTrader.ca has already moved once during this project's life: from server-
rendered cards to a `__NEXT_DATA__` JSON blob in the page. The parser reads
four different shapes in order of preference and records which one worked, so
a change usually costs nothing — it simply falls to the next rung of the
ladder and says so.

**What to do:** `python -m autotrader doctor --live` names which strategies
still work against the real page. If none do, `python -m autotrader capture`
saves the page as it is now, and `tests/fixtures/` is where a new fixture
goes. The parser is `autotrader/parser.py` and every strategy in it is
independent — adding a sixth does not touch the other five.

### A search stops returning anything — any time

**How you find out:** "AutoTrader watcher needs attention", naming the search.

AutoTrader changes its query parameters. A URL that worked a year ago can
start returning everything, or nothing, without an error. The bot cannot tell
"this search is now too narrow" from "this search is broken", so it reports
the failure and refuses to call any car sold on a search it could not read.

**What to do:** open the search URL in a browser. If the site shows cars and
the bot does not, it is the parser. If the site shows nothing either, the
search is the problem — edit it on the Searches tab.

### The schedule thins out — continuously, already happening

**How you find out:** "AutoTrader watcher covered only N% of yesterday", and
the amber dot on the dashboard.

GitHub drops scheduled runs under load, in whole windows rather than one at a
time. Measured on this repository: **40% of the firings asked for, with a
4h42m gap between two consecutive checks.** This is not a fault and there is
nothing to fix in the code.

**What to do:** KEEPING-TIME.md sets up an external timer that does not drop
windows. It takes about five minutes and the bot needs no code change to use
one.

### The month's minutes run out — only if something changes

**How you find out:** "This month's runner minutes are heading over", then
"The watcher has stopped".

While this repository is public and on standard runners, GitHub bills nothing
for it and the ledger labels every minute `exempt`. Make it private, or move
it into an organisation with a policy, and the same runs start drawing on a
3,000-minute allowance without anything in the repository changing.

**What to do:** the message says. If the spending is fine, delete
`BUDGET-STOP`. If it is not, lengthen `health.expected_interval_minutes`.

---

## The bot cannot tell you

These fail silently. They are the ones worth a calendar entry.

### The personal access token expires — **one year from the day you made it**

**How you find out:** you do not. cron-job.org starts getting 401s and stops
being able to start the bot; the bot goes quiet; six hours later it sends
"AutoTrader watcher has gone quiet" — *if* something else is still starting
it. If the token was the only timer, nothing starts the bot at all and
nothing sends the message.

This is the single failure in the whole system that can be completely silent,
and it is the one with a known date.

**What to do:** GitHub fine-grained tokens have a maximum lifetime and the
expiry is fixed when you create it. Put the date in a calendar. When it
comes, make a new one with the same single permission (Contents: read and
write on this repository only) and paste it into the cron-job.org job's
Authorization header. KEEPING-TIME.md has the exact steps and
`scripts/keep-time.sh` will tell you in plain words if the new one is wrong.

The cheap insurance: leave GitHub's own `schedule:` in `watch.yml` enabled
alongside the external timer. It serves 41.7% of its windows, which is poor
as a primary and excellent as a thing that notices the primary has died.

### The external timer's account lapses — years, or never

**How you find out:** same as above. cron-job.org is free and has no reason to
stop, but a free account with no logins is the kind of thing that gets
cleaned up.

**What to do:** the job's "last execution" column on cron-job.org is the
honest answer. The dashboard's Status tab also names whichever timer filled
the most slots, so if "Keeping time" stops saying `cron-job.org`, it stopped.

### GitHub disables the schedule — 60 days of no repository activity

GitHub switches off `schedule:` triggers on repositories with 60 days of no
commits. This bot commits its state on every run, so as long as it is running
the clock never starts. The trap is the other way round: once it has been
broken for 60 days, fixing the break does not bring the schedule back.

**What to do:** the Actions tab shows a banner, and a single push re-enables
it. Worth knowing before you spend an afternoon debugging cron.

### The workflow's pinned versions go stale — one to two years

`actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`
and Python 3.11. GitHub deprecates old action majors with a warning first and
a hard failure eventually; a Python version leaves support after about five
years.

**What to do:** bump the tag in `.github/workflows/*.yml`. The test suite is
the check — it runs on the same version the workflow does.

### Dependencies go stale — continuously, harmlessly

`requests` and `beautifulsoup4`, both pinned to a major version. There is no
lockfile and deliberately so: two libraries with wide compatibility ranges do
not need one, and a lockfile is a thing that rots on its own.

**What to do:** nothing, until CI fails. Then read what failed.

### The repository grows — slowly, and it prunes itself

Photos are kept per car in `docs/thumbs/` and outlive a build's cache on
purpose. Archived pages are pruned by retention policy; `state.json` keeps
the last 60 runs and prunes listings gone longer than the retention window.

**What to do:** nothing. `python -m autotrader prune` if you ever want it
smaller sooner.

### The ntfy topic is a secret in a URL — no expiry, but no protection either

Alerts go to a public ntfy.sh topic with a random name. Anyone who learns the
name can read your alerts; nobody can guess it. It is not a password and the
bot does not pretend it is.

**What to do:** if you ever paste the topic somewhere public, change
`notifications.ntfy.topic`. The bot announces the move on both the old topic
and the new one, so the phone still subscribed to the old one hears about it.

---

## What is NOT on this list, and why

- **The state file format.** It carries a version and `State.upgrade()` fills
  in bookkeeping older entries were written without, on load, idempotently.
  An old state file is a supported input, not a migration.
- **The dashboard's service worker.** `BUILD` is a hash of the files it
  caches, rewritten on every publish, and a browser re-fetches `sw.js` on
  navigation. There is no cache to clear by hand.
- **Clock and date handling.** `scripts/time-gate.sh` runs the whole suite
  under eight dates and six timezones — month ends, a leap day, a year
  boundary, two DST transitions and the two extremes of the UTC offset range.
  A date-dependent bug fails there rather than on a random Tuesday.
- **The tests themselves.** Not one of them makes a network request: every
  page they read is a fixture captured from the real site and kept in
  `tests/fixtures/`. The suite cannot start failing because AutoTrader had
  an outage, and cannot start passing because it had a good day.
  `tests/test_offline.py` asserts that property rather than trusting it.
