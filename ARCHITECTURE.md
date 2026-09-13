# How this works

Written for the version of you that has forgotten all of it. Every section
answers "what is this for" before "how does it work", because six months from
now the second question is easy and the first one is not.

If you only read one thing: **the bot is a single Python program that runs for
about thirty seconds, reads two web pages, and writes two files.** Everything
else here is either making that reliable or making the result legible.

---

## The shape of it

```
  a GitHub Actions runner, every so often
  ─────────────────────────────────────────────────────────────
   config.json ──▶ ┌──────────────────────────────────┐
   state.json  ──▶ │ python -m autotrader run         │
                   │                                  │
                   │  urls    → what to fetch          │
                   │  http    → fetch it, politely     │
                   │  parser  → HTML into Listings     │
                   │  validate→ is this parse real?    │
                   │  enrich  → detail pages, capped   │
                   │  geo     → how far away is it     │
                   │  filters → does it pass your rules│
                   │  state   → what changed since last │
                   │  notifiers→ tell you, once         │
                   │  thumbs  → keep a copy of a photo  │
                   │  insight → what the numbers mean   │
                   │  dashboard→ write docs/data.json   │
                   │  invariants→ does the ledger add up│
                   └──────────────────────────────────┘
                        │                    │
                        ▼                    ▼
                  state.json           docs/data.json
                  (committed)          (committed, then Pages)
                        │                    │
                        ▼                    ▼
                   your phone           the dashboard
```

Two files are the whole persistence layer, and both live in git. That is the
central design decision and everything else follows from it: no database, no
server, no credentials beyond the notification channels, and the entire
history of what the bot believed is `git log`.

---

## The files that matter

| File | Written by | Read by | What it is |
|---|---|---|---|
| `config.json` | you, or the control channel | every run | Your searches and rules. No secrets, ever. |
| `state.json` | every run | every run | Every car ever seen, its price history, whether you were told, and the last 60 run summaries. |
| `docs/data.json` | every run | the dashboard | A published, secret-free projection of state for the page. ~85 KB gzipped. |
| `docs/thumbs/*.webp` | every run | the dashboard | Our own copies of car photos. Budgeted and pruned. |
| `control/*.json` | you, from a phone | `control.yml` | A requested config change. Deleted once applied. |

Secrets live only in GitHub repository secrets and reach the bot as
environment variables. `config.json` and `docs/data.json` are scanned before
every write and the write is refused if anything credential-shaped is in them
— see `dashboard.find_secrets`.

---

## The modules, and why each exists

Read these in roughly this order; it is the order the data moves.

**`urls`** — parses an autotrader.ca search link into something the bot can
re-fetch and describe in English. The entire configuration story is "paste a
link", so this is where that promise is kept.

**`http`** — one rate-limited, retrying, budgeted client. A per-run request
budget (250 by default) is a hard stop covering search pages, detail lookups
*and* photos, with retries billed against it. `get()` returns text;
`get_asset()` returns bytes and a content type, because a WebP decoded as text
is mojibake — that mistake cost a day of photos that silently never arrived.

**`parser`** — four independent strategies (`jsonld`, `embedded_json`,
`anchors`, `regex`), best result wins, and the winner is recorded per search.
That is not over-engineering: autotrader.ca migrated to a new platform
mid-project and the strategy that had been winning stopped returning anything.
You can watch a strategy degrade in the Status view before cars stop arriving.

**`validate`** — decides whether a parse can be *trusted*, separately from
whether it succeeded. A page that returns 200 and parses to zero listings is a
failure unless the page itself says the search matched nothing. On a first run
for a search, a suspicious parse stops and records nothing rather than filling
state with debris somebody has to unpick by hand later.

**`shape`** — remembers what each search page looked like structurally, so a
site redesign shows up as drift before it shows up as silence.

**`enrich`** — pulls real price, odometer, colour, drivetrain and photo URLs
out of each listing's schema.org JSON-LD. Capped per run and billed to the
same budget.

**`geo`** — how far a car is from you, from its city and province or from a
postcode. It exists because the new platform stopped honouring the `prx`/`loc`
parameters in a search link, so a "near Vancouver" search was returning
Ontario and Quebec cars. Distance is now enforced bot-side.

**`filters`** — your rules, applied on top of whatever the link already does.
A car your rules reject is **kept and explained**, never dropped: the
dashboard shows the count and the reason. A number you can click on beats a
number that quietly omits.

**`state`** — the ledger. Per-car records with price history, written
atomically and saved in a `finally` block so a crash mid-run costs at most
that run's work. This is where change detection lives: new, price drop, price
rise, now priced, gone, back on the market, and back inside your rules.

**`notifiers`** — one interface per channel (Telegram, Discord, ntfy, Slack,
email, webhook, Twilio). Each is isolated: a failing channel is reported and
the alert is **held in state and retried next run**, never lost. A channel
that fails repeatedly is disabled with the reason written down.

**`render`** — turns a set of changes into a digest. One message per run, not
one per car.

**`thumbs`** — fetches and commits small copies of car photos, with a byte
budget and pruning. The dashboard used to hotlink the seller's CDN, which
meant the page did not work offline or in the installed app, and the photos
vanished the day a car was delisted. Nothing here may fail a check.

**`insight`** — everything the page reports as a number: coverage, events,
comparables, the market view, the deal-score backtest. Kept out of the page so
the numbers are computed once, tested, and identical everywhere they appear.

**`invariants`** — the bot auditing its own bookkeeping. Every car must end up
in exactly one of three states: told about, owed an alert, or deliberately
quiet with a recorded reason. A violation fails the run loudly, because
"we never told you" must always be a decision you can read back.

**`dashboard`** — builds `docs/data.json`, refuses to write anything
credential-shaped, and stamps the service worker so an installed app actually
updates.

**`control`** — parses a config change that arrived from a phone. Applies it
whole or refuses it whole; every refusal names what was wrong with the input.

**`lock`**, **`archive`**, **`diagnose`**, **`migrate`**, **`provision`**,
**`ui`**, **`events`** — a single-run lock, optional listing archiving with
retention, page captures when a parse fails, the v1 upgrade path, first-run
self-configuration, a local settings server, and a standing record of the
first time each kind of market event really happened.

---

## The dashboard

`docs/` is a static site: one HTML file, one JS file, one service worker, a
data file and some photos. No build step. Open `docs/index.html` in a browser
and it works.

- **Design decisions and the vocabulary it uses** are in `DESIGN.md`. Read
  that before changing anything visual.
- **Five views**: Feed (what changed), Listings (every car), Market (what they
  say together), Searches (what is being watched), Status (whether the bot is
  healthy).
- **Offline**: the service worker caches the shell and the data on install.
  Offline shows the saved copy *clearly labelled with its age* — never a stale
  page pretending to be current.
- **Updating**: `sw.js` carries a build stamp derived from `index.html` and
  `app.js`, rewritten on every publish. Changed bytes install a new worker,
  which re-fetches everything. Without that an installed app runs whatever it
  first cached, forever — which it did, until it was measured.
- **The page cannot write.** Anything that changes configuration opens
  GitHub's web editor with a prefilled control file, or a prefilled issue
  where the repository has issues enabled.

---

## The workflows

| Workflow | Trigger | What it is for |
|---|---|---|
| `watch.yml` | schedule (every 2h), `repository_dispatch`, push to `config.json`, dispatch | **The bot.** One check, one job: it scrapes, records events, saves state and publishes the dashboard without a second job. |
| `pages.yml` | push to `docs/`, dispatch | Publishes the dashboard by hand. The watcher no longer calls it - that was a second job-minute for a duplicate of work already done. |
| `ci.yml` | push and PR, ignoring everything the bot writes | The test suite. |
| `control.yml` | push to `control/*.json`, or an issue | Applies a config change from a phone. |
| `events.yml` | four times a day | The only thing that can report the watcher's silence, so it keeps a schedule independent of it. The market-events ledger itself is recorded inside the check. |
| `coldstart.yml` | weekly | Proves the repository still works from a clean clone. |
| `soak.yml` | on demand | Long-running behaviour checks. |
| `add-search.yml` | dispatch | Paste a link in the Actions UI. |

### The schedule, and what it costs

Twelve checks a day, one job each, on `cron: '11 */2 * * *'`. That is the whole
schedule. Everything below is why it is not more.

**What GitHub charges.** Every *job* is rounded up to a whole minute. A check
takes about 35 seconds, so it costs one minute; a check plus a separate
publishing job costs two. That one fact decides the shape of this - the lever
is the number of jobs, not the number of seconds - which is why publishing is
now a step inside the check rather than the second job it used to be, and why
recording market events moved in with it.

**What it used to cost.** Measured here over the 24 hours before it changed:
56 checks, 68 billed job-minutes, and that was the watcher alone. Beside it ran
a ledger workflow on its own 30-minute schedule, and three "pacemaker"
workflows, each holding a runner for up to five and a half hours to dispatch
checks on a timer. Together they asked for 144 firings a day and, when served,
held up to sixteen hours of runner between them.

**Why that was allowed to happen.** The pacemakers were justified, in writing,
in this file, with "runner minutes are free here because the repository is
public". That claim is *true* - GitHub returns `billable: {}` and
`total_ms: 0` against those runs, checked through the API - and it was still
the wrong way to decide. The exemption is a repository setting, not a property
of this code; nothing here would have noticed the day someone made the
repository private. "It was free when I wrote it" is not a budget, and sixteen
hours a day of somebody else's machines to watch a page that changes a few
times a week was a bad trade at any price.

**What is left.** Twelve checks (12 job-minutes), the ledger four times a day
(4), a weekly cold start (about 2 amortised): roughly 17 billed minutes a day
against an allowance of 3,000 a month.

**The cost guard.** `budget.py` adds every run to a per-day ledger in
`state.json`, projects the month at the current rate, and writes a
`BUDGET-STOP` file at the top of the repository when the month passes 85% of
the allowance. `watch.yml` reads that file before it installs anything and
stops; the Status tab shows the running total either way. It counts wall-clock
runner minutes and treats every one as billable, which over-counts by exactly
the value of the public-repository exemption - deliberately, so the guard
trips early on a public repo and on time on a private one. Deleting the file
resumes, and a new month clears it by itself.

**What the schedule buys, honestly.** GitHub drops scheduled runs; measured
here, roughly one slot in seven on a bad day. Asking for twelve and being
served eight or nine means a real interval of two to four hours, and a car
listed and sold inside one of those gaps is missed. That is the trade, and it
is now a decision instead of an accident. Three levers cost no scheduled job
at all: `repository_dispatch` from anything that can make one authenticated
POST (see RUNBOOK.md), a push to `config.json`, and the Run workflow button.

A fourth was described here for days and never existed:
`poke-the-watcher.yml` in `iden-f/autotrader_notifier`, firing
`repository_dispatch` on its own schedule. It never ran once. GitHub registers
scheduled workflows from a repository's **default branch only**, and that file
only ever sat on a feature branch - so coverage it was being credited with was
always somebody else's. It has been deleted.

To trigger a check from anywhere, with a token that has `contents: write`:

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches \
  -d '{"event_type":"check"}'
```

This costs one job-minute, the same as a scheduled check, and it is the lever
that does not depend on GitHub's scheduler at all.

---

## Things that look wrong and are not

**Every scheduled run seems to fail.** Check *why* before assuming the check
broke. A run that reads both searches, records two hundred listings and then
exits 1 on an invariant has covered its slot completely — no car came or went
unseen. Coverage counts runs that read the site and reports the complaints
separately, for exactly this reason.

**A narrow search finds nothing, run after run.** This is normal and the
dashboard says so in words: "reads fine, keeps nothing". The watch that came
before these three was a 2021-2023 M5 within 250 km of Vancouver, and it was
shut out on every run it ever made - the cars existed, they were in Alberta
and Ontario, and the distance rule rejected them. Working parser, narrow
search. The Searches tab distinguishes the two.

**"Hidden by a rule: 120."** Working as intended. Hidden cars are kept,
counted and explained. Click the number.

**Prices differ between the card and the detail page.** They genuinely do, on
the site. The bot records where each figure came from and **never compares
across sources** — doing so produced a phantom price drop on every single run.

**The market view says "at least 2 days".** Right-censoring. Nothing has been
watched longer than the watch has existed, so those figures are floors.

---

## Where to look when you change something

- Changing what the bot watches → `config.json`, or the Searches tab. Swapping
  one hunt for a different one → also `python -m autotrader forget --yes`,
  which drops the cars no remaining search is watching. Without it they are
  retired rather than dropped: still in state, still on the dashboard, still
  in the market medians.
- Changing what it says → `render.py` and `notifiers.py`.
- Changing what the page shows → `docs/app.js` + `DESIGN.md`, and re-render
  the screenshots.
- Changing what a number means → `insight.py`, and expect a test to argue.
- Something is broken → `RUNBOOK.md`.
