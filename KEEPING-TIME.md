# Keeping the bot running

GitHub's scheduler does not reliably fire this repository's cron, and the
allowance it draws on is not the constraint people assume. Both of those are
measurements, below, and the options are ranked against them.

## What is actually true, as of 13 September

**This repository's Actions do not draw on the included allowance.** Three
independent lines of evidence:

1. It is public (`iden-f/autotrader_minimal_secrets`) and every job in it runs
   on `ubuntu-latest`. GitHub does not bill standard runners on a public
   repository. `tests/test_budget.py` asserts every `runs-on:` stays on that
   list, because a *larger* runner is billed on a public repository like any
   other.
2. `GET /actions/runs/{id}/timing` returns `billable.UBUNTU.total_ms: 0` with
   `jobs: 1` on every run sampled, including runs after the account's
   allowance hit 100%.
3. The account's own billing reconciles without this repository: of ~6,700
   gross minutes, 3,000 drew on the allowance and came from two private
   repositories; the remaining ~3,700 exempt minutes are the public ones.

**An exhausted allowance does not stop it.** Scheduled runs at 16:35, 17:23
and 18:55 UTC on 13 September all succeeded, after the allowance was gone. A
`$0` Actions spending limit does not change this: a spending limit caps
*billable* usage, and there is none here to cap.

**The schedule is the weak part, not the money.** Measured over 10.5 hours of
genuine silence on the night of 13-14 September - nothing pushed, nothing
dispatched by hand - GitHub's cron filled **2 of 5 two-hour slots, 40%**, with
a longest gap of **4h42m**. The histogram is `[1, 0, 0, 1, 0]`. That is the
honest number for GitHub's scheduler alone on this repository, and it is below
the 60% a watch like this needs.

**A second cron offset helps less than it should.** GitHub fired 5 of the 10
firings the two offsets asked for - and all 5 landed inside just 2 of the 5
windows:

| Firing | Window | What happened |
|---|---|---|
| 22:34Z | 0 | stood down (a check 6 min earlier) |
| 00:08Z | 0 | **checked** |
| 00:22Z | 0 | stood down |
| 04:50Z | 3 | **checked** |
| 05:23Z | 3 | stood down |

Windows 1, 2 and 4 got nothing at all. GitHub drops *whole windows*, not
individual firings, so `11` and `41` are not two independent chances at a
window - they arrive as a pair or not at all. A timer on a different cadence
is the fix; a third offset is not.

## Options, ranked

Each is marked for whether it survives an exhausted allowance (**A**) and
whether it needs anything of yours to stay switched on (**M** = a machine).

### 1. Two cron offsets in one window — *in place, A: survives, M: none*

`'11 */2 * * *'` and `'41 */2 * * *'`. GitHub drops individual firings rather
than whole schedules, so a second offset inside the same window is a second
chance at the same slot. 30 minutes apart is inside the 90-minute
deduplication floor, so if the first is served the second costs a job-minute
and no requests.

Cheapest thing that helps, already running, and it does not fix the case where
GitHub drops both.

### 2. An external timer calling `repository_dispatch` — *A: survives, M: none (hosted)*

The workflow already accepts `repository_dispatch: types: [check]`. Anything
that can make one authenticated POST can drive it, and the bot deduplicates
it exactly like a scheduled run, so an over-eager timer costs nothing.

This is the only option that removes GitHub's scheduler from the path while
keeping the work on free runners. **Setup is one paste — see below.**

### 3. Your own machine on a cron — *A: survives, M: needs the machine on*

Same POST from `launchd`/`cron`/Task Scheduler. Free, entirely yours, and it
keeps time only while the machine is awake. Good as a second timer beside (2),
poor as the only one.

### 4. Move the check off Actions entirely — *A: survives, M: none, but a rewrite*

The check is Python with `requests` and `beautifulsoup4`, and it writes back to
the repository. A free serverless tier that runs Python on a schedule
(Deno/Val Town are JS; Cloudflare Workers' Python is a different runtime with
no `requests`) would mean reimplementing the fetch layer and giving something
else write access to the repo. Worth it only if Actions itself becomes
unavailable.

### 5. A pacemaker job that holds a runner — *rejected, do not revive*

A job that sleeps and dispatches works, and it cost up to sixteen hours of
runner a day. It is free here for the same reason everything else is, which is
exactly the argument that turned out to be the wrong way to decide.
`tests/test_workflows.py` fails if one comes back.

## Option 2, end to end — three steps

Ten minutes. Step 2 tells you whether step 1 worked, so you cannot get halfway
and not know.

### Step 1 — make a token

github.com → your avatar → **Settings** → scroll to **Developer settings** at
the bottom of the left column → **Personal access tokens** → **Fine-grained
tokens** → **Generate new token**.

| Field | What to put |
|---|---|
| Token name | anything — `autotrader timer` |
| Expiration | your choice. **The timer stops the day it expires**; 1 year is reasonable |
| Repository access | **Only select repositories** → pick `autotrader_minimal_secrets` |
| Repository permissions → **Contents** | **Read and write** |
| Every other permission | leave at **No access** |

Contents is the only one. `repository_dispatch` is filed under Contents rather
than Actions in GitHub's permission model, which is the thing everyone gets
wrong — if you grant Actions and not Contents it will not work.

Press **Generate token**. Copy the `github_pat_…` string now; GitHub will not
show it again.

> **Honestly:** the permission above is what GitHub's documentation specifies.
> I could not re-check it from where this was written — `docs.github.com` is
> blocked by this sandbox's network proxy, and so is the dispatch endpoint
> itself (`repository_dispatch is not permitted for this session type`). Worse,
> the proxy *rewrites* GitHub's replies: an obviously invalid token came back
> `200`. So no status code in this document was verified against real GitHub.
> That is exactly why step 2 is a script that reads the real answer instead of
> a table you would have to trust.

### Step 2 — prove the token works

From a clone of this repository:

```sh
GITHUB_TOKEN=github_pat_... sh scripts/keep-time.sh --from my-mac
```

**What you should see:**

```
Asking iden-f/autotrader_minimal_secrets for a check (as "my-mac")...
OK. GitHub accepted it (HTTP 204, which is the success code - there is
no reply body, and that is correct).

Look at https://github.com/iden-f/autotrader_minimal_secrets/actions within
about ten seconds. A run called "Check AutoTrader" should be there, marked
repository_dispatch. ...
```

Anything else and the script tells you what is wrong and how to fix it — a
missing permission, a token the repository cannot be seen with, an expired
token, no network. It interprets whatever GitHub actually replies, including
codes it does not recognise, in which case it prints GitHub's own message so
you have something to search for. `tests/test_keep_time.py` exercises every
one of those branches with a stubbed response, and asserts the event type and
repository in the script still match `watch.yml` — so this cannot rot into
pointing somewhere that no longer listens.

No clone handy? The script is one file with no dependencies beyond `curl`:

```sh
curl -sO https://raw.githubusercontent.com/iden-f/autotrader_minimal_secrets/main/scripts/keep-time.sh
GITHUB_TOKEN=github_pat_... sh keep-time.sh --from my-mac
```

### Step 3 — put it on a timer

Pick **one**. Both send the identical request.

**cron-job.org** — free, hosted, no card, keeps running when your laptop
sleeps. This is the one to choose if you are not sure.

| Field | Value |
|---|---|
| Title | `autotrader` |
| URL | `https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches` |
| Schedule | **Every hour**, at minute **25** |
| Request method | `POST` |
| Headers | `Authorization: Bearer github_pat_...`<br>`Accept: application/vnd.github+json`<br>`X-GitHub-Api-Version: 2022-11-28`<br>`Content-Type: application/json` |
| Request body | `{"event_type":"check","client_payload":{"from":"cron-job.org"}}` |

**Your Mac or a Linux box** — `crontab -e`, then one line (adjust the path to
wherever you cloned this):

```
25 * * * * GITHUB_TOKEN=github_pat_... sh ~/autotrader_minimal_secrets/scripts/keep-time.sh --cron --from my-mac
```

`--cron` makes it silent on success and loud on failure, so cron mails you only
when the timer stops working rather than every hour.

**Hourly against a two-hourly schedule, deliberately.** Asking more often than
you need costs nothing and doubles the chance of landing one: the bot's own
90-minute floor means a firing that finds a recent check exits in about fifteen
seconds having made zero requests. Minute 25 sits away from GitHub's own :11
and :41.

### Step 4 — confirm it is the thing keeping time

Give it a few hours, then open the dashboard's **Status** tab.

**What you should see:** a **Keeping time** tile reading `my-mac` or
`cron-job.org`, with a line under it like *"3 slots from cron-job.org (an
outside timer) and 1 slot from GitHub's schedule"*. The line at the very top of
the page stops saying "none of it scheduled".

If **Keeping time** still says *Your pushes* or *Not recorded*, the timer is
not reaching GitHub. Re-run step 2 by hand — the script will say why.

## Undoing it

Delete the cron entry (or the cron-job.org job), then revoke the token at
Settings → Developer settings → Personal access tokens. Nothing in the
repository needs changing: `repository_dispatch` is a door that simply stops
being knocked on.

## What this does NOT fix

The bot still depends on GitHub Actions to run the check itself. An outside
timer removes GitHub's *scheduler* from the path, not GitHub's *runners*. If
Actions is down or the repository's Actions are disabled, nothing here helps —
and the dashboard will say so, because the Status tab's "Last good check" will
age and the coverage figure will fall.
