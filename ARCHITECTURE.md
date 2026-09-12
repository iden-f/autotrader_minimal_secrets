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
| `watch.yml` | schedule, dispatch, `repository_dispatch`, `workflow_run` | **The bot.** One check. Everything else exists to get this to run. |
| `pages.yml` | push to `docs/`, called by `watch.yml` | Publishes the dashboard. |
| `ci.yml` | every push and PR | The test suite. |
| `control.yml` | push to `control/*.json`, or an issue | Applies a config change from a phone. |
| `events.yml` | after a check, and on its own schedule | Records market firsts, and is the only thing that can report the watcher's silence — so it is scheduled independently of it. |
| `pacemaker.yml`, `-b`, `-c` | three staggered crons | See below. |
| `coldstart.yml` | daily | Proves the repository still works from a clean clone. |
| `soak.yml` | scheduled | Long-running behaviour checks. |
| `add-search.yml` | dispatch | Paste a link in the Actions UI. |
| `probe.yml` | dispatch | Measures the real Actions job time limit. |

### The pacemaker, and why there are three

GitHub drops scheduled runs under load — heavily. A `*/30` cron does not run
every thirty minutes; measured here, roughly one slot in seven is served, and
gaps of several hours are normal. That is the single biggest limit on this
bot and no amount of code fixes it.

What helps: **more independent chances to be served.** Three pacemaker
workflows sit on three unrelated sets of minutes, each in **its own
concurrency group**. Each one that *is* served holds a runner for a bounded
period and dispatches the watcher on a timer.

The separate groups are the whole point and were once missing. All three sat
in a single group, and GitHub keeps at most one pending run per group - so
every firing arriving while a shift ran displaced the previously queued one.
Measured over nine hours: seven firings served, one ran, six cancelled before
starting. Three workflows behaving as one, with extra steps.

That also makes `health.min_interval_minutes` load-bearing. Nine offset
dispatch minutes against an 8-minute floor is a check every 10 minutes rather
than every 30 - three times the load on somebody else's site to learn the
same thing. At 24 the redundancy buys resilience instead: if one pacemaker
dies, another's dispatch lands in the same window and the check still happens
on time.

One served firing holds a runner for 330 minutes and dispatches a check every
30 - eleven checks, five hours of cover. That number is measured, not read: a
probe job counted out loud until GitHub stopped it, reaching minute 361, so
the runner's real ceiling is 360 and the backstop timeout sits at 350. It was
180 before the probe answered, which was half of what the machine allows.
Runner minutes are free here because the repository is public; on a private
one this would be the wrong trade.

They are deliberately **bounded**. A pacemaker does not re-trigger itself.
A self-perpetuating job is a job that cannot be switched off from outside, and
that is not a thing to build into somebody's repository. Every pacemaker
stops on its own, and `PACEMAKER-OFF` in the repository kills all of them.
`tests/test_workflows.py` holds all four bounds - the kill switch, the strike
limit, the backstop under the measured ceiling, and the shift under the
backstop - and asserts the only workflow a pacemaker can start is `watch.yml`.

There is also a fourth lever in the sibling repository
`iden-f/autotrader_notifier`: `poke-the-watcher.yml`, which fires
`repository_dispatch` at this one on its own schedule. Two repositories'
schedules are dropped independently.

To trigger a check from anywhere, with a token that has `contents: write`:

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches \
  -d '{"event_type":"check"}'
```

---

## Things that look wrong and are not

**Every scheduled run seems to fail.** Check *why* before assuming the check
broke. A run that reads both searches, records two hundred listings and then
exits 1 on an invariant has covered its slot completely — no car came or went
unseen. Coverage counts runs that read the site and reports the complaints
separately, for exactly this reason.

**The narrow search finds nothing, every run.** `2021-2023 BMW M5` has been
shut out on every run since it was written: the cars exist, they are in
Alberta and Ontario, and the distance rule rejects them. The warning says so
in words. It is working; it just has nothing to show you.

**"Hidden by a rule: 120."** Working as intended. Hidden cars are kept,
counted and explained. Click the number.

**Prices differ between the card and the detail page.** They genuinely do, on
the site. The bot records where each figure came from and **never compares
across sources** — doing so produced a phantom price drop on every single run.

**The market view says "at least 2 days".** Right-censoring. Nothing has been
watched longer than the watch has existed, so those figures are floors.

---

## Where to look when you change something

- Changing what the bot watches → `config.json`, or the Searches tab.
- Changing what it says → `render.py` and `notifiers.py`.
- Changing what the page shows → `docs/app.js` + `DESIGN.md`, and re-render
  the screenshots.
- Changing what a number means → `insight.py`, and expect a test to argue.
- Something is broken → `RUNBOOK.md`.
