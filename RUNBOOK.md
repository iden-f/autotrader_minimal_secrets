# When something is wrong

Written for the version of you that has forgotten all of it, is holding a
phone, and wants to know whether this matters.

**The first question is always: is the bot still reading the site?** Almost
everything else is cosmetic by comparison. Open the dashboard, look at the
Status tab, and read the "When it checked" strip. If the last few cells are
green, the bot is working and whatever else you are seeing can wait.

---

## The thirty-second triage

| What you see | What it means | Do this |
|---|---|---|
| Dot beside the title is green | Checked recently, no complaints | Nothing. |
| Dot is amber | Checked, but coverage is thin or it complained | Read "When it checked". Usually [thin schedule](#the-schedule-is-thin). |
| Dot is red | No check in hours, or every search is failing | [Nothing has run](#nothing-has-run-for-hours) or [the site changed](#every-search-returns-nothing). |
| "Offline · 2h ago" | **Your phone** has no connection | Nothing is wrong with the bot. |
| "Not checked yet" | Fresh install, no run yet | Run the workflow once by hand. |

---

## Nothing has run for hours

**How you know**: the Status strip is grey on the right; the trust line says
hours, not minutes. You may also have had a message from the watchdog — it is
scheduled independently of the watcher precisely so it can report its silence.

**Almost always**: GitHub dropped the schedules. This is normal and it is the
biggest limitation of this bot. One cron slot in seven is served on a bad day,
and the bot only asks for twelve.

**Or**: it stopped itself. If `BUDGET-STOP` exists at the top of the
repository, the month's runner minutes are spent and every check is exiting
early on purpose - see [what this costs](#what-this-costs).

**Check it is only that:**

1. Actions tab → **Check AutoTrader** → are there recent runs at all?
2. If there are runs but they are red, this is not a scheduling problem — see
   [the run is failing](#the-run-is-failing).
3. If there are no runs at all for a day or more, check the workflow has not
   been **disabled for inactivity**. GitHub does this to scheduled workflows
   in repositories with no recent pushes, and it is silent. The Actions page
   shows a banner with an "Enable workflow" button.

**What to do:**

- Right now: Actions → Check AutoTrader → **Run workflow**. That always works.
- Or from anywhere with a token:
  ```bash
  curl -X POST -H "Authorization: Bearer $TOKEN" \
    https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches \
    -d '{"event_type":"check"}'
  ```
- Longer term: check `BUDGET-STOP` does not exist in the repository root -
  the bot writes it when the month's runner minutes are spent, and every
  check exits immediately while it is there. The run summary says so.

**What not to do**: do not add more cron slots. They are dropped from the same
pool; more slots on one workflow does not help. More *independent workflows*
does, which is why there are three.

---

## The run is failing

**Read what it says before assuming the check broke.** The workflow exits 1
for several unrelated reasons and only some of them mean the bot is blind.

Actions → the failed run → the `check` job → the last twenty lines.

**"the bot's own bookkeeping is inconsistent"** — the run worked. It fetched
everything, wrote everything down, told you what changed, and then failed its
own audit. No car was missed. It is still worth fixing, because the audit
exists to make "we never told you" impossible.

The usual one is `hidden-has-a-reason`: a car that stopped being filtered but
kept "hidden by your rules" as its reason for staying quiet. The named
listing id is in the message. This has happened when a car arrives with no
price (so no rule rejects it) after previously being rejected on price.

**"the page loaded but no listings could be read"** — this is serious. See
[every search returns nothing](#every-search-returns-nothing).

**"first-run parse check failed"** — a new search's first parse looked wrong,
so nothing was recorded rather than filling state with debris. Run
`python -m autotrader doctor --live` and compare a few rows against the site
by eye.

**"could not save state"** — the disk or a permission. Rare on a runner.

**"could not push"** — usually another run pushed first. It retries. If it
persists, look for a **push protection rejection** in the log: something
credential-shaped got into a committed file. The bot scans for this before
writing, so this means something got in another way.

---

## Every search returns nothing

**How you know**: "read 0 listings", `empty_parses`, or every car suddenly
marked gone.

**It has happened before.** autotrader.ca migrated to a new platform
mid-project and the winning parse strategy stopped returning anything.

**What the bot does on its own**: a page that loads and parses to zero is
treated as a failure, not as an empty market. **Cars are not marked gone** —
a page it could not read is not evidence of anything. So a parser break costs
you alerts, not your data.

**What to do:**

1. `python -m autotrader doctor --live` — prints which strategies found how
   many listings, and a sample parse to check by eye.
2. Ask the next run to capture the real page: commit an empty file named
   `.capture-raw` at the repository root. The next run saves the live search
   page, gzipped, and deletes the marker so it cannot become a standing cost.
3. Rewrite a strategy in `parser.py` against that capture, with a test.

**The one thing to check first**: is it actually the site, or is it your
rules? "read 19 listings and none passed your rules" is a working parser and
a narrow search. The warning says which.

---

## The schedule is thin

**How you know**: Status says something like "35% - 17 of 48 half-hours had a
check", and "When it checked" is mostly grey.

**Read the interval first.** The bot asks for a check every **two hours**, not
every thirty minutes, and it says so on the Status tab. Twelve checks a day is
the schedule, on purpose - see "What this costs" below. Thin against twelve is
a fault; thin against forty-eight is arithmetic.

It matters because a car can be listed and sold inside a gap.

**Levers, in order of how much they help:**

1. Ask for a check directly. It costs the same as a scheduled one and never
   gets dropped:
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches \
     -d '{"event_type":"check"}'
   ```
   A phone shortcut pointed at that is the most reliable clock this bot has.
2. Push to the repository occasionally. GitHub deprioritises schedules in quiet
   repositories and eventually disables them outright - the sibling repository
   `autotrader_notifier` is `disabled_inactivity` right now for exactly that.
3. Check `health.min_interval_minutes` in **config.json**, not just the default
   in `config.py`. config.json is written once at setup and then owns its own
   copy, so changing the default does nothing to a bot already running.
4. Accept what is left. More scheduled slots is not a free lever any more -
   every served one is a job-minute. See below.

**What not to do**: do not add a workflow that holds a runner to dispatch
checks on a timer. This repository had three of them. They worked, and they
cost up to sixteen hours of runner a day for a watch that needed twelve
minutes.

---

## What this costs

**The short version**: about 17 billed minutes a day, against a GitHub
allowance of 3,000 a month. The Status tab shows the running total.

**How GitHub charges**: every *job* is rounded up to a whole minute. A
35-second check costs one minute. Two 35-second jobs cost two. The number of
jobs is the only lever that matters; seconds are almost irrelevant.

**Public repositories are not charged at all.** Verified through the API on
this repository: `get_workflow_run_usage` returns `billable: {}` or
`total_ms: 0` for every run, including a 115-minute one. If the account's
billing page shows minutes consumed, they are coming from somewhere else -
a private repository, Codespaces, or the *gross usage* report, which lists
public-repo minutes at $0. Settings -> Billing -> Plans and usage is the page
that distinguishes them.

**The bot counts anyway.** `budget.py` treats every wall-clock runner minute
as billable, which over-counts by exactly what the public exemption is worth.
That is deliberate: the exemption is a repository setting, and the day someone
flips this repository to private the guard is already right.

**When it stops**: at 85% of the allowance the bot writes `BUDGET-STOP` at the
top of the repository, alerts you once, and every check after that exits
before installing anything. Delete the file to resume; it rewrites itself next
run if the month is still over, and clears itself when the month turns.

```bash
python -m autotrader doctor     # prints the month's ledger among everything else
```

---

## You stopped getting messages

Work down this list; it is ordered by how often each one is the answer.

1. **Did anything actually change?** The Feed shows everything, including
   cars your rules hide. If the Feed is empty, the market was quiet.
2. **Did you mute or dismiss the car?** A muted car says "muted — no alerts"
   on its card. Dismissed cars are hidden from the list you scroll and live
   under "Not interested".
3. **Quiet hours.** `notifications.quiet_hours` in `config.json`. Alerts are
   *held*, not dropped — they arrive when the window ends.
4. **Is the channel disabled?** A channel that fails repeatedly is switched
   off with the reason recorded. Status shows which channels are live and
   why any are not. Email is off right now: Gmail rejected the credentials on
   five consecutive runs, and the reason is written into `config.json`.
5. **Is the alert owed rather than lost?** A car with `pending` in state is
   waiting for a working channel. The Status view counts these as
   "unaccounted cars", which should be zero.
6. `python -m autotrader test-notify` sends a sample to every live channel.

---

## The dashboard looks stale or wrong

**"Offline"** — your phone, not the bot. It is showing the saved copy and
saying how old it is.

**Old content after a deploy** — an installed app caches its code. It picks up
a new version on the *second* load after a publish, and offers a "A newer
version is ready · Reload" banner on the first. If it never updates, check
`sw.js` has a real build stamp rather than `__BUILD__` — the publish step
writes it, and `tests/test_offline.py` fails if it ships unstamped.

**Photos missing** — the card says which, and these are four different
things:

- "no photo" — the seller published none.
- "photo not copied yet" — there is one and the bot has not fetched it. A
  check fetches at most 24 new photos.
- "not kept for hidden cars" — deliberate; a car your rules hide does not get
  its photos published.
- "photo not loaded" — the bot has the file and *this device* could not read
  it. Offline, or a bad connection. The service worker keeps the photos in
  their own cache that survives a publish, so this should only appear on a
  device that has never opened the page with a connection.

The fourth one exists because without it, offline showed "photo not copied
yet" — a statement about the bot, for a fault in your aeroplane.

**Numbers disagree between views** — that is a bug; they are all computed once
in `insight.py`. File it.

---

## Changing something from a phone

The page cannot write to the repository, so a change opens GitHub's web editor
with a control file already filled in. Tap **Commit changes** and the
`control.yml` workflow applies it, deletes the file, and triggers a check.

- A refused change writes `control/<name>.REJECTED.md` next to it saying
  exactly what was wrong, and the commit carries a red X. **Nothing is ever
  half-applied.**
- Fix the file and commit again; it is read on every push.
- Only the repository owner can drive it.

To do it by hand, commit a file at `control/anything.json`:

```json
[{"action": "set-rule", "search": "BMW M3 2015-2020 (F80)",
  "rule": "max_price", "value": 90000}]
```

Valid actions: `set-rule`, `add-search`, `remove-search`, `mute-listing`,
`unmute-listing`, `shortlist`, `unshortlist`, `dismiss`, `undismiss`,
`set-channel`, `note`.

---

## You changed what it watches and the old cars are still there

Removing a search **retires** its cars: they stop being live, they keep their
price history, and they stay on the dashboard. That is right for "I have
stopped watching this one" and wrong for "I am hunting a different car now",
where the leftovers are a Listings tab full of cars you will never look at and
a Market view taking medians from the wrong ones.

```bash
python -m autotrader forget         # says what it would drop, changes nothing
python -m autotrader forget --yes   # drops it
python -m autotrader remove <id> --forget   # both, in one step
```

A car still owed an alert is never dropped, and the command says how many it
kept for that reason. Photos of forgotten cars go on the next check, and
`docs/data.json` is rebuilt by `python -m autotrader dashboard`.

---

## Breaking glass

**Stop everything**: Actions → Check AutoTrader → ⋯ → Disable workflow. Or
commit a file named `BUDGET-STOP` at the top of the repository: every check
reads it before it installs anything, says why it stopped in the run summary,
and does nothing else. Delete it to resume.

**state.json is corrupt**: do nothing. The bot quarantines it to
`state.corrupt.json`, rebuilds, and costs you exactly one round of
re-announcements. Restoring the previous `state.json` from `git log` avoids
even that.

**Start over from a known-good point**: `git revert` the bad commit. State is
in git; there is nothing else to restore.

**Verify a clean clone still works**: that is what `coldstart.yml` does every
day. Read its last run before assuming a fresh checkout is fine.

---

## Checking it by hand

```bash
pip install -r requirements.txt -r requirements-dev.txt

python -m pytest -q                  # the whole suite
python -m autotrader doctor          # config, links, credentials, disk
python -m autotrader doctor --live   # fetches the site; prints a sample parse
python -m autotrader run --dry-run   # a full run that changes and sends nothing
python -m autotrader ui              # dashboard with working settings, locally
python -m autotrader dashboard       # rebuild docs/data.json from state
```

`doctor` verifies every credential **without sending anything**. `--dry-run`
is safe to run against production state.
