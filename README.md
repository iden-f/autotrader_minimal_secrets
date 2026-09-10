# AutoTrader Watch

Watches AutoTrader.ca searches and tells you when a car shows up or gets
cheaper. You configure it by **pasting a search link** — run the search you
want on autotrader.ca, copy the address bar, done. Every filter you set there
(model, year, price, distance) comes along with the link.

Runs free on GitHub Actions. Notifies over Telegram, Discord, ntfy, Slack or
email — all free and unlimited.

```
┌── you ──────────────┐   ┌── GitHub Actions ────────┐   ┌── your phone ───┐
│ paste a search link │──▶│ every 30 min: scrape,    │──▶│ Telegram        │
│ (UI, CLI or the     │   │ compare, notify, publish │   │ Discord / ntfy  │
│  Actions form)      │   │ the dashboard            │   │ Slack / email   │
└─────────────────────┘   └──────────────────────────┘   └─────────────────┘
```

## What it does

- **New listings** — tells you once, in one message, however many turn up.
- **Price drops** — the thing that actually saves money. Every car's price is
  tracked over time, so a car you saw last week tells you when it drops.
- **Removals** — optional, for spotting what sold and how fast.
- **Tells you when it breaks.** If autotrader.ca stops loading, you get a
  message. The previous version failed silently for a month.
- **A dashboard** with every car, its price history, and the health of each
  search.

## It already works

There is nothing to configure. The bot sets itself up on its first run: it
generates a private [ntfy](https://ntfy.sh) topic — no account, no token — and
sends everything there.

**Your alerts go to <https://ntfy.sh/autotrader-2q592ak9gnx7sqrere5r>**

Open that in a browser, or install the ntfy app and subscribe to the topic
`autotrader-2q592ak9gnx7sqrere5r`. Full instructions are in [NOTIFY.md](NOTIFY.md).

> ntfy topics are destinations, not passwords: anyone who knows this one can
> read your alerts. The name is random so nobody will guess it, but it is
> stored in this public repository. The alerts contain only public AutoTrader
> listings. To use a channel with real authentication, add a Telegram, Discord,
> Slack or email secret and it takes over automatically — see [SETUP.md](SETUP.md).

Everything below is optional.

## Setup

### 1. Fork this repository

Use the **Fork** button. Make it private if you like
(Settings → General → Danger Zone → Change visibility).

### 2. Choose how you want to be told

**You do not have to do this.** ntfy is already set up. Add any of these only
if you want a channel with authentication, or one you already live in. Adding
one does not switch ntfy off — turn it off in Settings if you want.

All of these are free except SMS.

| | What you need | Where to get it |
|---|---|---|
| **Telegram** ← easiest | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Message [@BotFather](https://t.me/botfather), send `/newbot`, copy the token. Then message your new bot once and open `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat id. |
| **ntfy** ← no account | nothing | Install the [ntfy](https://ntfy.sh) app, subscribe to a topic nobody could guess (`m5-watch-8f3k2p`), and put that topic in Settings. |
| **Discord** | `DISCORD_WEBHOOK_URL` | Server Settings → Integrations → Webhooks → New Webhook → Copy URL. |
| **Slack** | `SLACK_WEBHOOK_URL` | Create a Slack app → Incoming Webhooks → Add New Webhook. |
| **Email** | `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Turn on 2-Step Verification, then create an [App Password](https://myaccount.google.com/apppasswords). Not your normal password. |
| SMS (Twilio) | `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM`, `TWILIO_TO` | Costs money per message and per number. Telegram and ntfy give you the same phone alert for nothing. |

Add them under **Settings → Secrets and variables → Actions → New repository
secret**. You only add the ones for the channels you want; a channel with its
secrets present switches itself on.

### 3. Add a search

Run your search on autotrader.ca, copy the address bar, then pick one:

- **In GitHub** — Actions → **Add a search** → *Run workflow* → paste the link.
  Nothing to install.
- **On your computer** — `python -m autotrader ui`, then paste it into the
  Settings page.
- **From a terminal** — `python -m autotrader add "<link>"`.

### 4. Turn on Actions

Actions → *I understand my workflows, go ahead and enable them*. From then on
it checks every 30 minutes. To run it right away: Actions → **Check AutoTrader**
→ *Run workflow*.

> **If it goes quiet for months, check the Actions tab.** GitHub disables
> scheduled workflows on a repository with no activity for 60 days, and a
> workflow can also be switched off by hand — v1's was, on 2026-01-08, after
> weeks of failures. That state is remembered against the workflow's *file
> path*, which is why the bot now lives at `.github/workflows/watch.yml`
> rather than the old `run_bot.yml`: a new path comes back enabled.
>
> You now also get told when it breaks, after three failed runs in a row.

**Full click-by-click setup — Telegram, secrets, the schedule, Pages — is in
[SETUP.md](SETUP.md).**

## The dashboard

**Live at <https://iden-f.github.io/autotrader_minimal_secrets/>**

Every car found, its price history, your searches and their health, and all
the settings. It is republished automatically after every run that changes
something.

`docs/index.html` is a single self-contained page with no build step.

- **Locally** — `python -m autotrader ui` opens it and can save settings
  straight back to `config.json`.
- **On the web** — already published, by the *Publish dashboard* workflow,
  which builds `docs/` onto the `gh-pages` branch. It is read-only there (a
  static page cannot write to your repository), so it offers *Copy config.json*
  and *Download* instead. Pages is free on public repositories; a private
  repository needs a paid plan.

## Commands

```bash
pip install -r requirements.txt

python -m autotrader add "<paste a search link>"   # watch a search
python -m autotrader set filters.max_price 120000  # change a setting
python -m autotrader set max_price 90000 --search "BMW M5"   # ...for one search
python -m autotrader list                          # what is being watched
python -m autotrader run                           # check once
python -m autotrader run --dry-run                 # ...changing nothing
python -m autotrader doctor                        # is anything misconfigured?
python -m autotrader doctor --live                 # ...and does the site load?
python -m autotrader test-notify                   # send yourself a sample
python -m autotrader ui                            # dashboard and settings
python -m autotrader setup                         # get from nothing to working
python -m autotrader setup --non-interactive       # ...without prompting (what CI runs)
python -m autotrader setup --new-topic             # roll a fresh ntfy topic
python -m autotrader prune --dry-run               # what archives would go
python -m autotrader prune --compact               # v1 folders -> metadata only
python -m autotrader capture --raw                 # save the live page as it is now
python -m autotrader migrate                       # import v1 data
```

`doctor` is the one to reach for when something looks wrong. It checks your
config file, your search links, which channels are configured **and whether
their credentials actually work** (without sending anything), your stored data
and your free disk, then says exactly what to fix.

`doctor --live` fetches autotrader.ca for real and prints which of the four
parser strategies won, how many listings each found, and a sample of what was
parsed — so you can compare it against the site by eye. Run this first on any
new install.

## Settings

Non-secret settings live in `config.json`, which the dashboard edits for you.
Secrets never go in it.

| Setting | Default | What it does |
|---|---|---|
| `scraping.max_pages` | 3 | Result pages per search. It stops early once a page adds nothing. |
| `scraping.unpriced_rechecks` | 3 | Times a "call for price" car's own page is checked for a figure before believing there is none. |
| `scraping.delay_ms` | 1200 | Pause between requests, jittered. Raise it if you ever get blocked. |
| `scraping.request_budget` | 250 | Hard ceiling on HTTP requests per run, photos included. Stops a misconfigured crawl. |
| `scraping.enrich_details` | `true` | Read each new car's own page for the exact price, odometer and photos. |
| `notifications.price_drop_min_pct` / `_abs` | 1% / $250 | A drop must clear **both** to be worth a message. |
| `notifications.quiet_hours` | off | Hold alerts overnight. They arrive in the next run afterwards — nothing is lost. |
| `filters.*` | empty | Extra rules on top of the link: price, year, odometer, keywords, sellers. |
| `filters.require_price` | `false` | Do not alert on "call for price" cars. They are still tracked and shown in their own bucket. |
| `health.disable_channel_after` | 2 | Runs of rejected credentials before a channel switches itself off. |
| `health.expected_interval_minutes` | 30 | What the schedule asks for. Used to spot a doubled or dropped run. |
| `health.min_interval_minutes` | 8 | Two scheduled checks closer than this tell you the same thing twice. |
| `health.silent_after_hours` | 3 | No successful check for this long and the alarm goes up. |
| `filters.near` / `filters.max_distance_km` | unset | Where you are, and how far you would drive. Enforced by the bot; the site ignores the link's own version. |
| `filters.provinces` | unset | A region allowlist, if a radius is the wrong shape for it. |
| `health.watch_page_shape` | `true` | Warn when the site changes how its pages are built, before the parser breaks. |
| `archive.mode` | `metadata` | `off`, `metadata`, or `full` (also stores the ~200 KB page). |
| `health.alert_after_failures` | 3 | Failed runs in a row before it warns you. |

## Per-search rules

Each search can override any global filter or alert rule, so a runabout watch
and a collector-car watch can live in the same config:

```bash
python -m autotrader set max_price 40000 --search "Civic"
python -m autotrader set max_mileage_km 120000 --search "Civic"
python -m autotrader set notify_on.removed true --search "911"
python -m autotrader set price_drop_min_abs 2500 --search "911"
```

Anything not overridden falls through to the global settings. `list` shows
which rules a search has of its own.

What you can be told about, and whether it is on by default:

| `notify_on.*` | Default | Meaning |
|---|---|---|
| `new` | on | A car you have not seen before. |
| `price_drop` | on | Confirmed against the listing page, and it must clear both thresholds. |
| `price_rise` | off | |
| `priced` | on | A "call for price" car has published a figure. Not a price drop — there was nothing to compare against. |
| `unpriced` | follows `require_price` | A new car with no figure on it. |
| `removed` | off | Established against the listing page, not guessed from its absence. |
| `relisted` | off | A car you were told had gone is back. |
| `errors` | on | The bot itself is in trouble. |

## When the site changes

autotrader.ca moved onto the AutoScout24 platform in 2026 and every parser
strategy went to zero at once. The bot now fingerprints each results page -
which strategy won, which others still work, which structural markers are
present - and compares it with the previous run.

A changed winner, the loss of the last fallback, a marker disappearing, or
results collapsing by half raises a warning **and commits a capture of the
page under `diagnostics/`** while listings are still flowing. That capture is
the point: by the time a parser actually breaks, the page that broke it is
long gone.

Losing one strategy of three is a note, not an alarm.

## How it reads the site

AutoTrader changes its markup. Rather than one selector that breaks silently,
each page is read four ways and the best result wins:

1. **`jsonld`** — schema.org data. Most reliable; gives exact price, odometer,
   colour, drivetrain and the real photo URLs.
2. **`embedded_json`** — the front-end state blob, for JavaScript-rendered pages.
3. **`anchors`** — listing links plus their result card. On the current
   platform this is the only one that sees both the model year and the seller:
   the JSON-LD publishes no year, the front-end blob publishes no seller.
4. **`regex`** — listing URLs pulled from the raw HTML, as a last resort. It
   knows nothing about a car except that it exists, which is the point.

The Status tab shows all four with their scores, so you can see the reliable
ones stop working before the bot stops finding cars. Two of four scoring is
already a warning: it means the next change to the site could take it out.

## When a car disappears

A car has to be missing from two consecutive runs before it counts as removed,
and even then only when its absence means something.

Most searches return more results than the bot reads — 186 cars against the 60
it samples — and autotrader.ca rotates which listings surface on which page.
So a car vanishing from the sample is not evidence of a sale, and treating it
as one produced fourteen "removed" alerts in a single run for cars still
sitting on page one. When the whole result set was not read, the listing page
is asked directly instead: gone, still listed, or — for a timeout — nothing
at all, in which case it is asked again next run rather than guessed at.

A run that read nothing, or a fraction of its usual count, does not spend the
grace period either. The countdown is there to absorb a car falling off one
page, not to absorb our own broken parse.

And **most of a watch vanishing at once is treated as suspicious, not as
news** — however complete the read looked. More than a quarter missing in one
run forces the same listing-page checks a partial sample gets, and the run
says so. Replaying a 20-car page against a 187-car state used to produce 154
"sold" alerts; it now produces none. If those checks can never establish
anything either way, absence is believed on its own after five runs, so
nothing sits in limbo unannounced and unresolved.

## Nothing goes missing quietly

Every serious bug found while rebuilding this was the same shape: not a crash,
but silently wrong state. Cars announced as sold that were still on the front
page. A search reporting zero listings while working perfectly. A car stored
and never mentioned because another search had already marked it seen. None of
them raised anything.

So the bot checks its own bookkeeping at the end of every run, and a violation
is a **failed run** with the offending entries written to
`diagnostics/invariants.json` — not a warning nobody reads:

- every car it is watching is owned by exactly one of your searches;
- every car is **delivered, queued, or deliberately quiet with a reason**, so
  "you were never told" is always a decision you can read back;
- a car hidden by a rule says which rule, and stops saying so when the rule
  stops applying;
- nothing is live and marked removed, or flagged call-for-price while carrying
  a price, or stuck past the grace period unresolved;
- the run's own numbers match state, and the dashboard's headline figures match
  what it actually publishes.

Status shows a **Never mentioned** figure. It should always read zero.
`python -m autotrader doctor` runs the same checks on demand.

## When what you watch changes

Widening a search does not discover cars — it stops ignoring them. Reading ten
pages instead of three once brought in 120 cars that had been in scope the
whole time, every one labelled a new listing.

Each search carries a fingerprint of what it actually asks for: the link, the
rules layered on it, and how deep the bot reads. When that changes, the next
run **records what it finds as a starting point and says so** instead of
announcing it. A search running for the first time is not a baseline — those
cars really are new to you.

The reverse holds too: relaxing a filter admits cars the bot has been storing
all along, and those are announced, because they are new to your watch even
though they are not new to the bot.

## Where the car is

The 2026 platform ignores the `prx`/`loc` parameters a pasted link carries, so
a search that says "near V6N 3B5" happily returns cars in Ontario, Alberta and
Quebec. Nothing fails; the constraint is simply dropped.

Set `near` and `max_distance_km` on a search and the bot enforces it from the
city and province it already reads off each listing:

```bash
python -m autotrader set near "V6N 3B5" --search "2021-2023 BMW M5"
python -m autotrader set max_distance_km 250 --search "2021-2023 BMW M5"
python -m autotrader set provinces '["BC"]' --search "Civic"   # or just a region
```

A car it cannot place is **never** excluded — the place table is the thing most
likely to be incomplete, and hiding a match because a town is missing from it
would be the worst failure this bot can have. A town it has never heard of is
still excluded when nothing in its province could possibly be in range, which
is honest rather than lucky. The dashboard says what is being enforced, because
a distance the bot applies itself appears nowhere in the link.

## When the schedule lets you down

GitHub fires cron late, early, twice, or not at all. Measured here: a gap of
4 hours 46 minutes on a schedule asking for one run every thirty, and, the same
evening, two runs five minutes apart.

- A **second scheduled run** minutes after a successful one stands down without
  spending a request. Only a schedule is deduplicated — a run you asked for
  always happens, and `--force` overrides it either way.
- A **missed window** is measured and said out loud: "the last successful check
  was 4.8 hours ago, not 30 minutes; the schedule dropped 9 runs."
- **Silence is watched by something else.** A watcher cannot report its own
  absence — the run that would tell you is the run that is not happening — so a
  separate hourly job reads the state file and raises the alarm when no check
  has succeeded for three hours. Once per silence, not once an hour.

## The first time each thing really happens

`EVENTS.md` is a ledger of the first genuine price drop, price rise, removal,
relisting, and call-for-price car naming a figure — with the before and after
and what was delivered about each. It is written by a job that only reads what
the watcher has already stored: it never scrapes, never notifies and never
writes state, so nothing in it can hold up a check.

It exists because the run counters could not see two events that had already
happened: a change on a car your rules hide goes through a path that records
it but classifies nothing, so a real $399 price drop and a real call-for-price
car naming $175,895 were both invisible in every number the bot printed.

## Upgrading from v1

Nothing to do. On the first run:

- `seen_listings.json` is read, so the cars you already knew about are **not**
  re-announced.
- The `SEARCH_URL` secret is imported as your first search if it is still set.
- `python -m autotrader migrate` rebuilds the old `archives/` folders into the
  dashboard, recovering the real price, odometer, colour and photos that v1
  never recorded.

### What was wrong with v1

Kept here because these are the failure modes worth not repeating.

| | v1 | Now |
|---|---|---|
| Saving state | Once, at the very end of a successful run. One bad listing lost every id and re-notified everything. | Written in a `finally` block, atomically, every run. |
| A failing channel | An unwrapped Twilio call ended the run. | Every channel is isolated; a failure is reported, and the alert is retried next run. |
| Messages | One email **and** one SMS per car. | One digest per run. |
| Price drops | Not detected at all. | Tracked per car, confirmed against the listing page before alerting. |
| Photos | Saved every `<img src>`, which was 400 manufacturer logos and no cars. | Real photo URLs from schema.org data. |
| Repository size | 9.6 MB of raw HTML for 50 cars, growing forever. | Metadata by default, with a retention policy. |
| When it broke | Nothing. It failed for a month unnoticed. | Warns you after 3 failed runs in a row, or as soon as a page loads but parses nothing. |
| Getting started | Seven secrets before the first alert. | Configures itself; needs no token at all. |
| Trusting the parser | Never checked. | Grades its own first run and refuses to record anything that looks wrong. |
| Request volume | Unbounded: every listing cost 16 extra requests. | Per-run budget, capped detail lookups, jittered pacing. |
| Two runs at once | Interleaved writes to the same file. | A single-run lock, plus a workflow concurrency group. |
| Identifying itself | `User-Agent: AutoTraderBot/1.0`. | An ordinary browser UA, paced requests, retries with backoff. |
| Searches | One, buried in a repository secret. | As many as you like, pasted in. |

## Proving it, on purpose

Two things run the bot harder than the schedule does, both started by
committing a marker file rather than by clicking anything:

* **`soak.json`** — `{"cycles": 24, "interval_minutes": 7}` runs the real bot
  against the real site that many times, committing after each cycle and
  appending a row to `SOAK.md`: what it saw, what changed, which strategy read
  each search, what it cost. The workflow deletes the file when it finishes.
  This is what a fixture cannot tell you — the first soak found that
  autotrader.ca rotates which results surface, which was inventing removals.
* **`.capture-raw`** — asks the next run to save the live search page itself,
  gzipped, under `diagnostics/`. That is how a parser strategy gets rewritten
  against real markup instead of a guess at it. The marker deletes itself.

`tests/test_chaos.py` does the opposite: it breaks things on purpose — a
half-written state file, invalid JSON in the config, a page truncated
mid-transfer, a channel whose credentials were revoked — and checks the run
finishes, somebody is told through a channel that still works, and the next
clean run is normal again without anyone helping.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

697 tests. They run against real pages AutoTrader actually served — ten
captured listing pages in `tests/fixtures/listings/` and captured results
pages in `tests/fixtures/` — not against mocks.

The suites worth knowing about:

| File | Covers |
|---|---|
| `test_invariants.py` | The bot's own bookkeeping: ownership, every car accounted for, counts that reconcile, a scope change treated as a baseline, an owed alert that survives. |
| `test_chaos.py` | Deliberate damage — corrupt state, invalid config, a truncated page, revoked credentials, a half-working parse — and whether it degrades, alerts, and recovers unaided. |
| `test_live_data.py` | Behaviour against the real payload a live run returned: price drops, removals, relistings, call-for-price, overlapping searches, a rotating result window. |
| `test_platform_2026.py` | The AutoScout24 migration, and that all four parse strategies still score on the current markup. |
| `test_parser_hardening.py` | Zero results, one result, pagination, call-for-price, a broken primary strategy, markup nothing understands, hostile input. Every case asserts graceful degradation. |
| `test_failure_modes.py` | Corrupt and truncated state, a full disk, a rate-limited channel, an interrupt mid-run, clock skew across quiet hours, duplicate ids, a listing that vanishes and returns. |
| `test_budget.py` | The request budget is a hard stop, retries are billed, pacing is jittered. |
| `test_doctor.py` | Every problem `doctor` reports, and that it never sends a message. |

### A note on what is and is not verified against the live site

**Listing detail pages** were always validated against real captured pages.
**Search-results pages** could not be fetched from the machine this was built
on, so their markup was inferred — which is how two of the four parse
strategies came to score zero for a month without anyone noticing. A live run
has since captured one (`python -m autotrader capture --raw`) and the fixtures
are cut from it, so all four are now checked against markup the site really
served.

That history is why there are four independent strategies, why `doctor --live`
exists, why the Status tab shows every strategy's score rather than only the
winner, and why a page that returns HTTP 200 but parses nothing is treated as
a failure rather than an empty search.

## Note

This scrapes a public website for personal use. Keep `scraping.delay_ms`
reasonable and the schedule sane; hammering the site is both rude and the
fastest way to get blocked.

## Licence

MIT.
