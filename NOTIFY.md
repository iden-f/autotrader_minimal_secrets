# Where your alerts go

Your watcher sends new listings and price drops to a private **ntfy** topic.
No account, no token, nothing to sign up for.

## Get them on your phone

1. Install **ntfy** — [iOS](https://apps.apple.com/app/ntfy/id1625396347) ·
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) ·
   [F-Droid](https://f-droid.org/packages/io.heckel.ntfy/)
2. Tap **+** to subscribe to a topic.
3. Enter exactly:

   ```
   autotrader-xdyhmhtn2zh9kfcfmeak
   ```

That is it. The next time the bot finds something, your phone buzzes.

## Or just open it in a browser

<https://ntfy.sh/autotrader-xdyhmhtn2zh9kfcfmeak>

Leave the tab open and messages appear live.

## Worth knowing

ntfy topics are not secret by design — **anyone who knows this topic name can
read your alerts**. The name is long and random, so nobody will guess it, but
it is written in this file, and this repository is public. The alerts only
contain public AutoTrader listings, so there is little to leak; if that still
bothers you, either make the repository private, or switch to a channel with
real authentication:

- **Telegram** — set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as repository
  secrets and it takes over automatically. See [SETUP.md](SETUP.md).
- **Discord / Slack** — set `DISCORD_WEBHOOK_URL` or `SLACK_WEBHOOK_URL`.
- **Email** — set `GMAIL_USER` and `GMAIL_APP_PASSWORD`.

Adding any of those does not switch ntfy off. To stop ntfy, set
`notifications.channels.ntfy.enabled` to `false` in `config.json`, or turn it
off in the dashboard's Settings tab.

## Change the topic

Pick a new one in the dashboard, or:

```bash
python -m autotrader setup --new-topic
```

*This file is written by the bot. Editing it changes nothing — the topic lives
in `config.json`.*
