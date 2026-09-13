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

## Option 2, end to end

You need a fine-grained token that can do one thing, and a free timer.

**Step 1 — make the token.** github.com → Settings → Developer settings →
Personal access tokens → Fine-grained tokens → Generate new token.

- Repository access: **Only select repositories** → `autotrader_minimal_secrets`
- Permissions → Repository permissions → **Contents: Read and write**
  (this is what `repository_dispatch` requires; nothing else needs to be on)
- Expiration: whatever you are willing to rotate

**Step 2 — check it works.** One paste, with your token in place of `TOKEN`:

```sh
curl -sS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches \
  -d '{"event_type":"check"}' -w '%{http_code}\n'
```

`204` means it worked, and a run appears under Actions within seconds. `404`
means the token cannot see the repository; `403` means it lacks Contents:
write.

**Step 3 — put it on a timer.** Any of these; they are the same request.

*cron-job.org* (free, hosted, no card):
- URL: `https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches`
- Method: `POST`
- Headers: `Authorization: Bearer TOKEN`, `Accept: application/vnd.github+json`,
  `X-GitHub-Api-Version: 2022-11-28`
- Body: `{"event_type":"check"}`
- Schedule: every hour at minute 25 (odd minute, away from GitHub's own)

*Your Mac* — `crontab -e`:
```
25 * * * * curl -sS -X POST -H "Accept: application/vnd.github+json" -H "Authorization: Bearer TOKEN" -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/repos/iden-f/autotrader_minimal_secrets/dispatches -d '{"event_type":"check"}' >/dev/null
```

Hourly rather than two-hourly on purpose: the bot's own 90-minute floor means
an extra firing inside a window is a 15-second no-op, so asking twice as often
as you need costs nothing and doubles the chance of landing one.

**How you will know it worked.** The Status tab's coverage note names what
started the checks. An outside timer counts as a schedule there, so it should
read "N of N came from the schedule" rather than naming pushes.
