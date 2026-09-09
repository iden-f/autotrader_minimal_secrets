# Setup

**You can skip all of this.** The bot configures itself on its first run and
sends alerts to <https://ntfy.sh/autotrader-2q592ak9gnx7sqrere5r> — no account, no token, nothing to sign up for. See
[NOTIFY.md](NOTIFY.md).

What follows is for when you want something else: a channel with real
authentication, a different search, or the dashboard on the web.

Throughout, `YOUR-REPO` means `github.com/iden-f/autotrader_minimal_secrets`
(or your fork).

---

## 1. Telegram instead of ntfy (optional, ~4 minutes)

ntfy already works and needs nothing. Telegram is worth the four minutes if you
want alerts only you can read: an ntfy topic is readable by anyone who knows
its name, and yours is committed to a public repository.

Adding the two secrets below is all it takes — Telegram takes over on the next
run, and you can turn ntfy off in the dashboard's Settings tab.

### 1a. Create the bot

1. Open Telegram. In the search bar, type **`@BotFather`** and open the account
   with the blue tick.
2. Tap **Start** (or send `/start`).
3. Send **`/newbot`**.
4. BotFather asks for a *name*. Type anything: `AutoTrader Watch`.
5. BotFather asks for a *username*. It must end in `bot` and be unique, e.g.
   `iden_autotrader_watch_bot`. If it is taken, add digits.
6. BotFather replies with:

   ```
   Use this token to access the HTTP API:
   8123456789:AAH2x-abcdefGHIJKLmnopQRSTuvwxYZ1234
   ```

   That long string is **`TELEGRAM_BOT_TOKEN`**. Copy it now — the whole thing,
   including the digits before the colon.

### 1b. Find your chat id

The bot cannot message you until you message it first.

1. In BotFather's reply, tap the **`t.me/your_bot_name`** link, then **Start**.
2. Send it any message — `hello` will do.
3. In a browser, open this URL, pasting your token where shown:

   ```
   https://api.telegram.org/bot<PASTE_TOKEN_HERE>/getUpdates
   ```

   Note there is **no** space and no `<>` — it reads
   `https://api.telegram.org/bot8123456789:AAH2x-.../getUpdates`.

4. You will get JSON. Find `"chat":{"id":123456789,`. That number — including a
   leading `-` if present — is **`TELEGRAM_CHAT_ID`**.

   *Empty result `{"ok":true,"result":[]}`?* You did not send the bot a message,
   or you sent it before opening the URL and Telegram already cleared it. Send
   another message and reload.

### 1c. Store both as repository secrets

1. Go to **`YOUR-REPO` → Settings** (top tab bar, far right).
2. Left sidebar → **Secrets and variables** → **Actions**.
3. Green button **New repository secret**.
4. Name: `TELEGRAM_BOT_TOKEN` — Secret: the token — **Add secret**.
5. **New repository secret** again. Name: `TELEGRAM_CHAT_ID` — Secret: the
   number — **Add secret**.

You should now see both listed under *Repository secrets*. GitHub will never
show you the values again, which is fine — nothing needs to read them back.

### 1d. Test it

**Actions** tab → **Check AutoTrader** (left sidebar) → **Run workflow** →
**Run workflow**. Wait ~40 seconds, refresh, open the run, and read the
**Check for new listings** step. It should list `telegram` under the channels
it is using.

Locally, if you have the repo checked out:

```bash
export TELEGRAM_BOT_TOKEN=8123456789:AAH2x-...
export TELEGRAM_CHAT_ID=123456789
python -m autotrader doctor        # checks the token AND the chat id, sends nothing
python -m autotrader test-notify   # sends one sample message
```

### Alternatives

| Instead of Telegram | Secret to add | Where to get it |
|---|---|---|
| **ntfy** (no account at all) | none | Install the ntfy app → **+** → invent a topic nobody could guess, e.g. `m5-watch-k39fjq2b` → subscribe. Put that topic in Settings (step 3), not in secrets. |
| **Discord** | `DISCORD_WEBHOOK_URL` | Server → **Server Settings** → **Integrations** → **Webhooks** → **New Webhook** → pick a channel → **Copy Webhook URL**. |
| **Slack** | `SLACK_WEBHOOK_URL` | api.slack.com/apps → **Create New App** → **From scratch** → **Incoming Webhooks** → toggle **On** → **Add New Webhook to Workspace**. |
| **Email** | `GMAIL_USER`, `GMAIL_APP_PASSWORD` | myaccount.google.com/security → turn on **2-Step Verification** → then myaccount.google.com/apppasswords → name it `AutoTrader` → **Create** → copy the 16 characters. **Not** your normal password. |

Anything you add switches itself on. You can have several at once.

---

## 2. The old SEARCH_URL secret

**Already handled.** The first run copies that secret into `config.json` and
sets a flag; after that the secret is never read again, so leaving it in place
cannot cause a surprise later. The imported search carries a note saying so.

You can delete it whenever you like: **Settings** → **Secrets and variables** →
**Actions** → `SEARCH_URL` → trash icon. Nothing depends on it.

The rest of this section is only useful if you want to change the search.

### 2a. Find out what your current `SEARCH_URL` is

GitHub will not show it to you. Recover it from a run instead:

1. **Actions** → **Check AutoTrader** → **Run workflow** → **Run workflow**.
2. Open the run → **Check for new listings** step.
3. The log prints each search it is watching, including the URL it came from.

If that fails, just build a fresh one: go to autotrader.ca, set up the search
you want, and copy the address bar.

### 2b. Add it as a real search

Easiest, no install:

1. **Actions** tab → **Add a search** (left sidebar).
2. **Run workflow** → paste the link into **Paste the autotrader.ca search
   link** → optionally name it → **Run workflow**.
3. It validates the link, adds it to `config.json`, and commits. Refresh the
   repo and open `config.json` to see it.

Or locally:

```bash
python -m autotrader add "https://www.autotrader.ca/cars/bmw/m5/?rcp=25&..."
python -m autotrader list
```

Or in the dashboard: **Searches** tab → paste → **Watch this search**.

### 2c. Delete the old secret

**Settings** → **Secrets and variables** → **Actions** → `SEARCH_URL` → trash
icon → **Delete secret**. Optional: the bot stopped reading it after the first
run.

---

## 3. Re-enable the schedule

**Your bot's workflow is currently switched off.** On 2026-01-08, after weeks
of failing runs, `run_bot.yml` was disabled. GitHub remembers that against the
**file path**, so renaming the workflow inside the file would not have brought
it back.

This branch therefore moves it to a new path — `.github/workflows/watch.yml` —
which registers as a new workflow and is **enabled by default**. So after
merging, there is usually nothing to do.

### Check it took

1. **Actions** tab.
2. In the left sidebar you should see **Check AutoTrader**.
3. Open it. If you see a banner reading *"This workflow was disabled
   manually"*, click **Enable workflow**.
4. The old **Run Autotrader Bot** entry may linger in the sidebar for a while.
   Ignore it; its file is gone.

### Confirm it actually runs

Scheduled runs do not start immediately, and GitHub drops them under load.
Force one now: **Check AutoTrader** → **Run workflow** → **Run workflow**.

A green tick means it worked. Open the run and read the log — it prints how
many listings it found and which channels it notified.

> **Why every 30 minutes and not 15?** GitHub queues `schedule` jobs on a
> best-effort basis. The old `*/15` never actually ran every 15 minutes — the
> real gaps in your history were 25 to 50 minutes. Asking for less waste gets
> you the same coverage. Change the `cron:` line in `watch.yml` if you disagree.

### If it goes quiet again

GitHub disables scheduled workflows on repositories with **60 days of no
activity**. This bot commits its results whenever something changes, which
normally keeps the repo active — but a bot that is failing commits nothing.
That is the trap that caught v1.

You now get told: after 3 failed runs in a row, the bot messages you on every
channel you have configured. If you ever *stop* hearing from it entirely,
check **Actions** first.

---

## 4. Turn on the dashboard (optional)

A page showing every car found, its price history, and the health of each
search.

### Locally — always works, and can save settings

```bash
pip install -r requirements.txt
python -m autotrader ui
```

Opens `http://127.0.0.1:8765`. Changes you make in **Settings** save straight
to `config.json`.

### On the web — GitHub Pages

1. **Settings** tab → left sidebar → **Pages**.
2. Under **Build and deployment** → **Source**, choose **Deploy from a branch**.
3. **Branch**: `main`. **Folder**: `/docs`. Click **Save**.
4. Wait 1–2 minutes. The page reloads with *"Your site is live at
   `https://iden-f.github.io/autotrader_minimal_secrets/`"*.

> **Free plan?** Pages only serves **public** repositories on GitHub Free. If
> this repo is private, either make it public (check first that nothing
> sensitive is committed — `config.json` holds no secrets, but look anyway),
> or just use the local UI above. Nothing else depends on Pages.

The published page is **read-only**: a static page cannot write to your repo.
Editing settings there gives you **Copy config.json**, which you paste over the
file in GitHub's web editor.

---

## 5. The bot checks itself

On its first real run against the live site, the bot grades its own parsing and
tells you the verdict — over your alert channel, in the Actions run summary,
and as a `validation-report.md` artifact on the run. It reports which strategy
won, how many listings it read, and a sample to compare against the site.

If the parse looks wrong — only the regex fallback worked, almost nothing has a
price, prices outside any plausible range — **it records nothing at all** and
fails the run loudly, rather than archiving garbage you would later have to
unpick. The next run simply tries again.

That check runs until one run succeeds. After that it stays quiet.

## 6. Verify it yourself

```bash
python -m autotrader doctor
```

Checks your config file, your searches, which channels are configured, whether
their credentials actually work (without sending anything), your stored data,
and free disk. Every problem it finds comes with the fix.

Then the important one:

```bash
python -m autotrader doctor --live
```

This fetches autotrader.ca for real and shows exactly what the parser made of
it:

```
  BMW M5 near Toronto
  https://www.autotrader.ca/cars/bmw/m5/?rcp=50&...
  HTTP 200 - 412 KB in 890 ms
  Strategy results
    jsonld            25 listing(s) <-- used
    embedded_json      0 listing(s)
    anchors           25 listing(s)
    regex             25 listing(s)
OK   25 listing(s) via 'jsonld'
  Sample parse (eyeball these against the site)
    2021 BMW M5 Competition
      id         13166607
      price      $109,999   (from the detail page)
      odometer   52,000 km
      where      Winnipeg, Manitoba
      photos     12
      url        https://www.autotrader.ca/a/bmw/m5/winnipeg/manitoba/19_13166607_/
```

**Compare those numbers against the live site in a browser.** This is the one
check worth doing by hand, because the search-results markup is the part that
was inferred rather than captured — see the risk note in the pull request.

What the strategy table tells you:

- **Several strategies scoring** — healthy. Plenty of fallback.
- **Only `regex` scoring** — it still works, but records will be sparse (no
  price or odometer from the results page; the detail lookup fills them in).
  Worth reporting.
- **Everything zero, and the site did not say "no results"** — the parser has
  fallen behind. A real run treats this as a failure and alerts you.

Finally, a full rehearsal that changes nothing and sends nothing:

```bash
python -m autotrader run --dry-run
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no channel is configured` | Secrets not set, or set on the wrong repo | Settings → Secrets and variables → **Actions** (not Codespaces, not Dependabot) |
| Telegram: `chat id ... is wrong` | Never messaged the bot, or wrong number | Message the bot, reload `getUpdates`, re-copy the `id` |
| Gmail: `Gmail rejected those credentials` | Using your normal password | Create an App Password; needs 2-Step Verification on first |
| `the page loaded but no listings could be read` | Markup changed, or an anti-bot page | `doctor --live` to see the strategy table |
| `served an anti-bot page` | Too many requests | Raise `scraping.delay_ms` to 3000, lower `scraping.max_pages` |
| `this run has used its allowance of N requests` | Budget hit; not an error | Raise `scraping.request_budget`, or lower `max_pages` / `enrich_limit` |
| Workflow never runs on its own | Disabled, or repo inactive 60 days | Actions → **Check AutoTrader** → **Enable workflow** |
| Nothing at all for weeks | The bot may be dead | Actions tab, then `doctor --live` |
| `another run ... holds .autotrader.lock` | Two runs at once | Wait; or delete `.autotrader.lock` if no run is active |

## What is safe to commit

`config.json` holds **no secrets** — only preferences, and whether a channel is
switched on. Tokens and passwords are read from the environment (GitHub
secrets) and are never written to any file the bot creates.

The dashboard's `docs/data.json` contains listings and settings, never
credentials. If you make the repo public for Pages, both are safe; the thing
to check is your own commit history.
