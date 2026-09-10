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

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

240 tests. They run against the real listing pages captured in `archives/`, so
the parser is checked against what AutoTrader actually served — not a mock.

The suites worth knowing about:

| File | Covers |
|---|---|
| `test_parser_hardening.py` | Zero results, one result, pagination, call-for-price, a broken primary strategy, markup nothing understands, hostile input. Every case asserts graceful degradation. |
| `test_failure_modes.py` | Corrupt and truncated state, a full disk, a rate-limited channel, an interrupt mid-run, clock skew across quiet hours, duplicate ids, a listing that vanishes and returns. |
| `test_budget.py` | The request budget is a hard stop, retries are billed, pacing is jittered. |
| `test_doctor.py` | Every problem `doctor` reports, and that it never sends a message. |

### A note on what is and is not verified against the live site

**Listing detail pages** are validated against 50 real captured pages.
**Search-results pages** could not be fetched from the machine this was built
on, so their markup is inferred. That is why there are four independent parse
strategies, why `doctor --live` exists, and why a page that returns HTTP 200
but parses nothing is treated as a failure rather than an empty search.

## Note

This scrapes a public website for personal use. Keep `scraping.delay_ms`
reasonable and the schedule sane; hammering the site is both rude and the
fastest way to get blocked.

## Licence

MIT.
