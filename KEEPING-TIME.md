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

**The schedule is the weak part, not the money.** Over the 14 hours after the
two-hourly schedule went live: 6 of 7 slots covered, but only **2 of 7 by the
schedule**. The rest came from pushes and from an external dispatcher that has
since stopped. Longest gap 2h57m.

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

## Option 2, end to end — four steps, in order

You need a token that can do one thing, and a timer. Roughly ten minutes.

### Step 1 — make the token

github.com → Settings → Developer settings → Personal access tokens →
**Fine-grained tokens** → Generate new token.

| Field | Value |
|---|---|
| Repository access | **Only select repositories** → `autotrader_minimal_secrets` |
| Repository permissions → **Contents** | **Read and write** |
| Everything else | leave at *No access* |
| Expiration | your call; the timer stops working the day it expires |

**Contents: write is the whole list.** `repository_dispatch` is documented
under Contents, not Actions, which is the one thing people get wrong here. Do
not grant Actions, Workflows, or Administration — none of them is needed and
each one widens what a leaked token could do.

*What you should see:* a token starting `github_pat_`. Copy it now; GitHub
will not show it again.

### Step 2 — prove the token works, before automating it

One paste, with your token in place of `TOKEN`:

```sh
curl -sS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches \
  -d '{"event_type":"check","client_payload":{"from":"my-mac"}}' \
  -w '\nHTTP %{http_code}\n'
```

*What you should see:* `HTTP 204` and no body. Within about ten seconds a
**Check AutoTrader** run appears at
`github.com/iden-f/autotrader_minimal_secrets/actions`, with
*repository_dispatch* under its title.

| Instead you got | It means |
|---|---|
| `404` | the token cannot see the repository — wrong repo selected in step 1 |
| `403` | the token lacks **Contents: write** |
| `422` | the JSON body is malformed — check the quoting |

`client_payload.from` is what makes the dashboard able to name your timer.
Use anything short and recognisable: `my-mac`, `cron-job.org`, `pi`.

### Step 3 — put it on a timer

Pick one. They send the identical request.

**cron-job.org** — free, hosted, no card, survives your laptop being shut:

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches` |
| Method | `POST` |
| Schedule | **every hour at minute 25** |
| Headers | `Authorization: Bearer TOKEN`<br>`Accept: application/vnd.github+json`<br>`X-GitHub-Api-Version: 2022-11-28` |
| Body | `{"event_type":"check","client_payload":{"from":"cron-job.org"}}` |

**Your Mac** — `crontab -e`, one line:

```
25 * * * * curl -sS -X POST -H "Accept: application/vnd.github+json" -H "Authorization: Bearer TOKEN" -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches -d '{"event_type":"check","client_payload":{"from":"my-mac"}}' >/dev/null 2>&1
```

**Hourly, against a two-hourly schedule, on purpose.** Asking more often than
you need is free and doubles the chance of landing one. It cannot make the bot
scrape more often: `health.min_interval_minutes` is 90, and a firing that
finds a check younger than that exits in about fifteen seconds having made
zero requests. Minute 25 is chosen to sit away from GitHub's own :11 and :41.

*What you should see:* two or three **Check AutoTrader** runs an hour, most of
them lasting ~15 seconds and doing nothing. That is the design working — the
short ones are the duplicates being refused.

### Step 4 — confirm it is the thing keeping time

Open the dashboard's **Status** tab after a few hours.

*What you should see:* a **Keeping time** tile naming your timer —
"cron-job.org (an outside timer)" — with a breakdown underneath like
*"3 slots from cron-job.org (an outside timer) and 1 slot from the schedule"*.
The header at the top of the page stops saying "none of it scheduled".

If **Keeping time** still names *a push to the repository*, the timer is not
reaching GitHub: re-run step 2 by hand and check the token has not expired.

## Undoing it

Delete the cron entry, and revoke the token at Settings → Developer settings.
Nothing in the repository needs changing — `repository_dispatch` is an open
door that simply stops being knocked on.
