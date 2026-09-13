# Real market events

The first time each of these actually happened on autotrader.ca, as
opposed to in a replayed payload. Written by a job that only reads what
the watcher has already stored - it never scrapes and never notifies,
so nothing here can hold up a check.

_Last looked: 2026-09-13T07:08:54+00:00_

| Event | First seen | Car | Before | After | What was delivered |
|---|---|---|---|---|---|
| a price came down | 2026-09-10T01:48:53 | [BMW M5 M Carbon Exterior | Advance Driver Assist](https://www.autotrader.ca/offers/bmw-m5-m-carbon-exterior-advance-driver-assist-electric-grey-cat_ma13gr202609va3044mt15084-32dc5029-f764-4c3e-95fe-da915a01ffdf) | $139,798 | $139,399 | deliberately quiet: hidden by your rules: price $139,399 above maximum $120,000 |
| a price went up | 2026-09-11T09:10:25 | [BMW M5 CARBON ROOF & INTERIOR I HUD](https://www.autotrader.ca/offers/bmw-m5-carbon-roof-interior-i-hud-electric-white-cat_ma13gr202609va3044mt15084-0b85b6f4-df54-4ddb-b9b1-757c18e4a233) | $130,880 | $132,800 | deliberately quiet: hidden by your rules: price $132,800 above maximum $120,000 |
| a car left the market | 2026-09-10T08:12:03 | [BMW M5 Competition, One Owner, Two Sets Premium Pkg, Adva](https://www.autotrader.ca/offers/bmw-m5-competition-one-owner-two-sets-premium-pkg-adva-gasoline-black-cat_ma13gr202609tr17957-32dccae4-3921-4773-a293-1c9d7f3f7873) | active | gone | delivered at 2026-09-10T08:12:04+00:00 |
| a car came back | 2026-09-12T07:15:43 | [BMW M5 Competition, Ultimate Package!! M Drivers Pkg!! Fu](https://www.autotrader.ca/offers/bmw-m5-competition-ultimate-package-m-drivers-pkg-fu-gasoline-grey-cat_ma13gr202609va3044mt15083tr17957-727d36cb-6045-44ea-bb97-7e3c022e2674) | gone | active | deliberately quiet: hidden by your rules: Calgary, AB is 680 km from V6N 3B5, beyond the 250 km you asked for (this car was announced at 2026-09-10T02:15:46+00:00, before that rule applied) |
| a car came back inside your rules | _still waiting_ | | | | |
| a call-for-price car named a figure | 2026-09-09T22:52:05 | [AutoTrader listing 339aa830-e262-4886-9ab1-15319df7e984](https://www.autotrader.ca/offers/bmw-m5-touring-m-performance-carbon-fibre-body-kit-gas-electric-hybrid-grey-cat_ma13gr202609va2324mt15084-339aa830-e262-4886-9ab1-15319df7e984) | — | $175,895 | deliberately quiet: hidden by your rules: year 2026 above maximum 2023 |

## A price came down

- **When:** 2026-09-10T01:48:53+00:00
- **Car:** BMW M5 M Carbon Exterior | Advance Driver Assist (`32dc5029-f764-4c3e-95fe-da915a01ffdf`)
- **What changed:** $399 off ($139,798 to $139,399)
- **Delivered:** deliberately quiet: hidden by your rules: price $139,399 above maximum $120,000
- **The run that saw it:** 2026-09-10T01:48:53+00:00 — ok True, listings_seen 206, new 37, requests_made 37; sent via ntfy: sent - 35 change(s)

## A price went up

- **When:** 2026-09-11T09:10:25+00:00
- **Car:** BMW M5 CARBON ROOF & INTERIOR I HUD (`0b85b6f4-df54-4ddb-b9b1-757c18e4a233`)
- **What changed:** $1,920 more ($130,880 to $132,800)
- **Delivered:** deliberately quiet: hidden by your rules: price $132,800 above maximum $120,000
- **The run that saw it:** 2026-09-11T09:10:25+00:00 — listings_seen 204, removed 2, requests_made 15; sent via ntfy: sent - 2 change(s)

## A car left the market

- **When:** 2026-09-10T08:12:03+00:00
- **Car:** BMW M5 Competition, One Owner, Two Sets Premium Pkg, Adva (`32dccae4-3921-4773-a293-1c9d7f3f7873`)
- **What changed:** last seen 2026-09-10T02:59:59+00:00
- **Delivered:** delivered at 2026-09-10T08:12:04+00:00
- **The run that saw it:** 2026-09-10T07:46:27+00:00 — ok True, listings_seen 204, requests_made 12; sent via nothing

## A car came back

- **When:** 2026-09-12T07:15:43+00:00
- **Car:** BMW M5 Competition, Ultimate Package!! M Drivers Pkg!! Fu (`727d36cb-6045-44ea-bb97-7e3c022e2674`)
- **What changed:** written off, then listed again
- **Delivered:** deliberately quiet: hidden by your rules: Calgary, AB is 680 km from V6N 3B5, beyond the 250 km you asked for (this car was announced at 2026-09-10T02:15:46+00:00, before that rule applied)
- **The run that saw it:** 2026-09-12T07:15:43+00:00 — ok True, listings_seen 209, requests_made 12; sent via nothing

## A call-for-price car named a figure

- **When:** 2026-09-09T22:52:05+00:00
- **Car:** AutoTrader listing 339aa830-e262-4886-9ab1-15319df7e984 (`339aa830-e262-4886-9ab1-15319df7e984`)
- **What changed:** listed with no price on 2026-09-09, then asked $175,895
- **Delivered:** deliberately quiet: hidden by your rules: year 2026 above maximum 2023
- **The run that saw it:** 2026-09-09T22:42:48+00:00 — ok True, listings_seen 19, requests_made 2; sent via nothing
