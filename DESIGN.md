# Design

An instrument panel for one car search. Not a product with users — a panel
with a reader, who is checking whether anything moved and whether the thing
watching is still working.

That decides everything below. Chrome is quiet, data is loud. The page is
dense on purpose: a car search is a comparison task, and comparison needs
things close enough together to compare. Nothing decorative.

## The one rule

**Every pixel is either a number, a label for a number, or a control.**
If it is none of those it should not be there. No hero images, no
illustrations, no empty-state mascots, no gradients, no decorative rules.

## Type

One family for prose, one for figures. Figures are tabular everywhere, with
no exceptions — a column of prices that shifts as digits change is a column
you cannot scan.

| Token | Size | Weight | Used for |
|---|---|---|---|
| `--t-display` | 30px / 1.05 | 560 | The one dominant figure on a detail view |
| `--t-figure` | 21px / 1.1 | 560 | The price on a card. One per card, nothing else at this size |
| `--t-h1` | 19px / 1.2 | 560 | View title |
| `--t-h2` | 15px / 1.25 | 560 | Section heading, card title |
| `--t-body` | 14px / 1.45 | 400 | Prose, descriptions |
| `--t-small` | 13px / 1.4 | 400 | Secondary facts, table cells |
| `--t-micro` | 11px / 1.2 | 520, +0.06em, uppercase | Labels only. Never a sentence |

Four weights exist: 400, 520, 560, 620. 620 appears only in the wordmark and
the dominant figure. Anything that needs emphasis inside prose gets 520 and a
colour step, not bold.

Prose never exceeds 68 characters per line.

## Space

A 4px grid. `--s1: 4px` through `--s10: 64px` — 4, 8, 12, 16, 20, 24, 32, 40,
52, 64. Nothing between the steps.

Gutters: 16px at phone, 24px at tablet, 32px at desktop, applied once on one
wrapper. Vertical rhythm inside a card is 8px between related lines, 16px
between groups, never both at once on the same edge.

## Shape and depth

- Radius: `--r-sm 4px` on controls and chips, `--r-md 8px` on cards and
  sheets. Tables and list rows are square. Nothing is a pill except a filter
  chip, which genuinely is one.
- **No shadow on anything that sits in the page.** Separation comes from a
  hairline (`--line`) and a one-step background change. There is exactly one
  shadow token, `--shadow-overlay`, and it is only for things that float over
  the page: the detail sheet and the command bar.
- Borders are not the default. A card is a background step. A border appears
  only where two surfaces of the same colour meet, or to carry a semantic
  rule (see below).

## Colour

Neutrals carry the interface; colour carries meaning and nothing else. Both
themes are defined as the same token names so no component knows which it is
in.

Semantic colours and the only places each may appear:

| Meaning | Token | Where it may be used |
|---|---|---|
| New to you | `--ev-new` | Event rule, event dot, unread marker |
| Price drop | `--ev-drop` | Event rule, the delta figure, the down glyph |
| Price rise | `--ev-rise` | Event rule, the delta figure, the up glyph |
| Removed | `--ev-gone` | Event rule, status label. Card content goes muted |
| Relisted | `--ev-back` | Event rule, status label |
| Now priced | `--ev-priced` | Event rule, the new figure |
| Hidden by a rule | *(none)* | Muted text + the rule quoted. Never coloured |
| Stale data | `--warn` | Header state, staleness banner |
| Failing | `--bad` | Header state, error text, invariant list |

Rules for use:

1. A semantic colour appears as **a 2px left rule, a 6px dot, or the figure
   itself**. Never as a filled badge background — filled badges make every
   row shout at the same volume.
2. Never colour running prose. A sentence explaining a price drop is body
   colour; the `−$2,400` inside it is `--ev-drop`.
3. Green means cheaper, not "good" or "success". Amber means dearer, not
   "warning". Red is reserved for the bot being broken, and nothing else —
   a car being expensive is not an error.
4. Contrast: every text/background pair meets AA (4.5:1 for body, 3:1 for
   ≥19px and for the semantic figures). Checked, not assumed.

## Motion

Purposeful only. There is no animation that exists to be noticed.

| Token | Duration | Curve | Used for |
|---|---|---|---|
| `--m-state` | 110ms | `cubic-bezier(.2,0,0,1)` | Hover, press, chip toggle |
| `--m-enter` | 180ms | `cubic-bezier(.2,0,0,1)` | A view or sheet arriving |
| `--m-number` | 220ms | `cubic-bezier(.2,0,0,1)` | A figure counting to a new value |

Nothing exceeds 220ms. Nothing delays interaction — a control is usable on
the frame it appears.

- A new item animates **once, on arrival**, keyed by id. Re-rendering the
  same list does not re-animate it.
- A price change animates the number, never the card.
- `prefers-reduced-motion: reduce` sets every duration to 0 and removes every
  transform. Not "shortened" — removed.

## Loading and feedback

- Initial load shows a **skeleton in the shape of the content**, never a
  spinner. The skeleton has the same metrics as the real thing so nothing
  moves when it is replaced.
- A save shows optimistic state immediately and an inline confirmation in
  place. No toast. A toast covers the thing you just changed.
- An error appears where the thing that failed is, with what failed and what
  to do about it. Never a generic banner.

## Numbers

- Prices, years, odometer, distance, counts: tabular, and right-aligned in
  any column where two of them are compared. Left-aligned when they stand
  alone in a sentence.
- One dominant figure per card. Everything else on the card is `--t-small`.
- Units are `--t-micro` and sit next to the figure without competing:
  `142,500` then `km`, not `142,500 km` at one size.
- A derived number always shows its basis. "$31/1000km" is meaningless
  without the odometer beside it; "12% under median" is meaningless without
  the sample size.

## States

Every view designs all of these. None falls out by accident.

1. **First ever load** — nothing has run. Explain what happens next.
2. **Empty** — genuinely nothing matches.
3. **Empty because of a filter** — name the filter, offer one click to loosen.
4. **Stale** — the bot has not checked recently. Header goes amber, every
   figure is suffixed with when it was true.
5. **Failing** — the page looks wrong on purpose: header red, the real error
   shown, not a generic one.
6. **Partial** — some searches worked. Say which did not.
7. **Image failed** — a real placeholder with the car's year and model, not
   an empty box.
8. **Offline** — served from cache, says so and says how old.

## Terminology

Fixed vocabulary, used identically everywhere including notifications:

- **check** — one run of the bot. Not "scan", "poll", "update".
- **listing** — one car. Not "vehicle", "result", "item".
- **hidden** — excluded by one of your rules. Not "filtered out".
- **gone** — no longer on the site. Not "sold", which is not known.
- **back** — gone, then listed again. Not "relisted" in UI copy.
- **asking** — the current price. Not "cost".

Sentence case everywhere, including buttons and headings. No Title Case. No
terminal full stops on labels; full stops on sentences.
