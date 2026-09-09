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

## Setup

### 1. Fork this repository

Use the **Fork** button. Make it private if you like
(Settings → General → Danger Zone → Change visibility).

### 2. Choose how you want to be told

All of these are free except SMS. Pick one; add more later.

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
> scheduled workflows on a repository with no activity for 60 days. This bot
> commits its results on every run that finds something, which normally keeps
> it alive — but if a run has been failing for weeks, nothing gets committed and
> the schedule can be switched off. That is exactly what happened to v1.

## The dashboard

`docs/index.html` is a single self-contained page: every car found, its price
history, your searches and their health, and all the settings.

- **Locally** — `python -m autotrader ui` opens it and can save settings
  straight back to `config.json`.
- **On the web** — Settings → Pages → deploy from `main` / `/docs`. It becomes
  read-only there (a static page cannot write to your repository), so it offers
  *Copy config.json* and *Download* instead. Pages is free on public
  repositories; a private repository needs a paid plan.

## Commands

```bash
pip install -r requirements.txt

python -m autotrader add "<paste a search link>"   # watch a search
python -m autotrader list                          # what is being watched
python -m autotrader run                           # check once
python -m autotrader run --dry-run                 # ...changing nothing
python -m autotrader doctor                        # is anything misconfigured?
python -m autotrader doctor --live                 # ...and does the site load?
python -m autotrader test-notify                   # send yourself a sample
python -m autotrader ui                            # dashboard and settings
python -m autotrader setup                         # first-run wizard
python -m autotrader prune --dry-run               # what archives would go
python -m autotrader migrate                       # import v1 data
```

`doctor` is the one to reach for when something looks wrong — it checks your
searches, your channels and your saved data, and says what is missing.

## Settings

Non-secret settings live in `config.json`, which the dashboard edits for you.
Secrets never go in it.

| Setting | Default | What it does |
|---|---|---|
| `scraping.max_pages` | 3 | Result pages per search. It stops early once a page adds nothing. |
| `scraping.delay_ms` | 1200 | Pause between requests. Raise it if you ever get blocked. |
| `scraping.enrich_details` | `true` | Read each new car's own page for the exact price, odometer and photos. |
| `notifications.price_drop_min_pct` / `_abs` | 1% / $250 | A drop must clear **both** to be worth a message. |
| `notifications.quiet_hours` | off | Hold alerts overnight. They arrive in the next run afterwards — nothing is lost. |
| `filters.*` | empty | Extra rules on top of the link: price, year, odometer, keywords, sellers. |
| `archive.mode` | `metadata` | `off`, `metadata`, or `full` (also stores the ~200 KB page). |
| `health.alert_after_failures` | 3 | Failed runs in a row before it warns you. |

## How it reads the site

AutoTrader changes its markup. Rather than one selector that breaks silently,
each page is read four ways and the best result wins:

1. **`jsonld`** — schema.org data. Most reliable; gives exact price, odometer,
   colour, drivetrain and the real photo URLs.
2. **`embedded_json`** — the front-end state blob, for JavaScript-rendered pages.
3. **`anchors`** — listing links plus their result card.
4. **`regex`** — listing URLs pulled from the raw HTML, as a last resort.

Whichever won is shown on the dashboard's Status tab, so you can see the more
reliable ones stop working before the bot stops finding cars.

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
| When it broke | Nothing. It failed for a month unnoticed. | Warns you after 3 failed runs in a row. |
| Identifying itself | `User-Agent: AutoTraderBot/1.0`. | An ordinary browser UA, paced requests, retries with backoff. |
| Searches | One, buried in a repository secret. | As many as you like, pasted in. |

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

The tests run against real listing pages captured in `archives/`, so the
parser is checked against what AutoTrader actually served — not a mock.

## Note

This scrapes a public website for personal use. Keep `scraping.delay_ms`
reasonable and the schedule sane; hammering the site is both rude and the
fastest way to get blocked.

## Licence

MIT.
