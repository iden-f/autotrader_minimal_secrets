# Real market events

The first time each of these actually happened on autotrader.ca, as
opposed to in a replayed payload. Written by a job that only reads what
the watcher has already stored - it never scrapes and never notifies,
so nothing here can hold up a check.

_Last looked: 2026-09-10T08:04:40+00:00_

| Event | First seen | Car | Before | After | What was delivered |
|---|---|---|---|---|---|
| a price came down | 2026-09-10T01:48:53 | [BMW M5 M Carbon Exterior | Advance Driver Assist](https://www.autotrader.ca/offers/bmw-m5-m-carbon-exterior-advance-driver-assist-electric-grey-cat_ma13gr202609-32dc5029-f764-4c3e-95fe-da915a01ffdf) | $139,798 | $139,399 | deliberately quiet: hidden by your rules: price $139,399 above maximum $100,000 |
| a price went up | _still waiting_ | | | | |
| a car left the market | _still waiting_ | | | | |
| a car came back | _still waiting_ | | | | |
| a call-for-price car named a figure | 2026-09-09T22:52:05 | [AutoTrader listing 339aa830-e262-4886-9ab1-15319df7e984](https://www.autotrader.ca/offers/bmw-m5-touring-m-performance-carbon-fibre-body-kit-gas-electric-hybrid-grey-cat_ma13gr202609-339aa830-e262-4886-9ab1-15319df7e984) | — | $175,895 | deliberately quiet: hidden by your rules: year 2026 above maximum 2023 |

## A price came down

- **When:** 2026-09-10T01:48:53+00:00
- **Car:** BMW M5 M Carbon Exterior | Advance Driver Assist (`32dc5029-f764-4c3e-95fe-da915a01ffdf`)
- **What changed:** $399 off ($139,798 to $139,399)
- **Delivered:** deliberately quiet: hidden by your rules: price $139,399 above maximum $100,000
- **The run that saw it:** 2026-09-10T01:48:53+00:00 — ok True, listings_seen 206, new 37, requests_made 37; sent via ntfy: sent - 35 change(s)

## A call-for-price car named a figure

- **When:** 2026-09-09T22:52:05+00:00
- **Car:** AutoTrader listing 339aa830-e262-4886-9ab1-15319df7e984 (`339aa830-e262-4886-9ab1-15319df7e984`)
- **What changed:** listed with no price on 2026-09-09, then asked $175,895
- **Delivered:** deliberately quiet: hidden by your rules: year 2026 above maximum 2023
- **The run that saw it:** 2026-09-09T22:42:48+00:00 — ok True, listings_seen 19, requests_made 2; sent via nothing
