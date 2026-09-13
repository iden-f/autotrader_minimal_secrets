/* AutoTrader Watch - the reading end.
 *
 * No framework and no build step: the page is published to GitHub Pages by a
 * bot that has no node in its runner, and every dependency would be one more
 * thing that can be down when you open this on a phone in a car park.
 *
 * Everything here reads docs/data.json, which the bot rewrites after every
 * check. Nothing is computed here that the bot could have computed once -
 * see autotrader/insight.py - because the browser has one car's worth of
 * context and the bot has the whole history.
 */
'use strict';

const VIEWS = [
  { id: 'feed', label: 'Feed' },
  { id: 'listings', label: 'Listings' },
  { id: 'market', label: 'Market' },
  { id: 'searches', label: 'Searches' },
  { id: 'status', label: 'Status' },
];

const KIND = {
  new:        { label: 'New',        group: 'New to the market',  flag: 'new',    rule: 'Appeared on the site' },
  price_drop: { label: 'Price drop', group: 'Price drops',        flag: 'drop',   rule: 'Asking price came down' },
  price_rise: { label: 'Price rise', group: 'Price rises',        flag: 'rise',   rule: 'Asking price went up' },
  priced:     { label: 'Now priced', group: 'Now priced',         flag: 'priced', rule: 'Call-for-price car named a figure' },
  removed:    { label: 'Gone',       group: 'Gone from the site', flag: 'gone',   rule: 'No longer on the site' },
  relisted:   { label: 'Back',       group: 'Back on the market', flag: 'back',   rule: 'Listed again after going' },
  qualified:  { label: 'In range',   group: 'Back inside your rules', flag: 'back',
                rule: 'A rule of yours stopped hiding it' },
  photos:     { label: 'Photos',     group: 'Can be looked at now', flag: 'new',
                rule: 'First photos on a listing that had none' },
  seller:     { label: 'Seller',     group: 'Changed hands', flag: 'back',
                rule: 'Moved between a private seller and a dealer' },
};
// Ahead of "new": a car crossing back into your rules is the only moment you
// will ever hear about it, where a new listing will still be there tomorrow.
const KIND_ORDER = ['price_drop', 'qualified', 'new', 'priced', 'price_rise',
                    'relisted', 'seller', 'photos', 'removed'];

/* No sort ranks a missing value as the worst value.
   `?? Infinity` put the two "call for price" cars at the bottom of a list
   headed "cheapest", which is the page asserting something about them that
   their own rows say it cannot know. Rows with nothing to rank are separated
   out and labelled instead. */
const SORTS = [
  { id: 'newest',   label: 'Newest first',       absent: 'no first-seen date',
    get: l => { const t = Date.parse(l.first_seen); return Number.isFinite(t) ? -t : null; } },
  { id: 'price',    label: 'Asking, low first',  absent: 'no asking price',
    get: l => l.price ?? null },
  { id: 'priced',   label: 'Asking, high first', absent: 'no asking price',
    get: l => l.price == null ? null : -l.price },
  // There was a "Best $/1000km" sort here, then a "Cheapest per 1,000 km"
  // one. Both were an odometer sort with a dollar sign on it: measured over
  // the 26 cars in this watch, its rank correlates 0.97 with "most
  // kilometres first" and 0.73 with "cheapest first". It crowned a 269,000 km
  // 2010 X3 at $2,150 and put the freshest M4 in the list last. No threshold
  // fixes that - the metric is the odometer - so the sort is gone.
  //
  // A defensible replacement exists and is not offered yet: rank by
  // comparables.pct, once enough cars carry one. On this dataset that is
  // none, and a "best value" sort that can rank nothing is worse than no
  // sort at all.
  { id: 'year',     label: 'Newest year',        absent: 'no model year',
    get: l => l.year ? -l.year : null },
  { id: 'km',       label: 'Lowest odometer',    absent: 'no odometer reading',
    get: l => l.mileage_km ?? null },
  { id: 'distance', label: 'Closest',            absent: 'no distance from you',
    get: l => l.distance_km ?? null },
  { id: 'days',     label: 'Longest listed',     absent: 'no first-seen date',
    get: l => l.days_listed == null ? null : -l.days_listed },
];

/* Split, then sort. A row whose value the sort cannot read is not a row at the
   bottom of the ranking - it is a row outside it, and the page says which.
   The tail is ordered newest-first, which is the page's own default and makes
   no claim about the metric that could not be read. */
function ranked(rows, sort) {
  const has = [], absent = [];
  for (const l of rows) (sort.get(l) == null ? absent : has).push(l);
  has.sort((a, b) => {
    const x = sort.get(a), y = sort.get(b);
    return x === y ? 0 : (x < y ? -1 : 1);
  });
  absent.sort((a, b) => (Date.parse(b.first_seen) || 0) - (Date.parse(a.first_seen) || 0));
  return { has, absent };
}

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const app = {
  data: null,
  view: 'feed',
  offline: false,
  loadError: null,
  q: '',
  sort: 'newest',
  chip: 'all',
  search: 'all',
  showHidden: false,
  lastSeen: store.get('lastSeen', null),
  seenIds: new Set(store.get('seenIds', [])),
  freshIds: new Set(),
  draft: null,
};

/* ----------------------------------------------------------------- format */
const money = n => (n === null || n === undefined || n === '') ? '—'
  : '$' + Math.round(n).toLocaleString('en-CA');
const signed = n => (n > 0 ? '+' : '−') + '$' + Math.abs(Math.round(n)).toLocaleString('en-CA');
const daysListed = d => d === 0 ? 'listed today'
  : d === 1 ? 'listed yesterday' : `${d} days listed`;
const km = n => (n === null || n === undefined) ? null : num(n);
/* Every number a person reads, grouped the same way. A bare toLocaleString()
   with no locale is the browser's locale, which is not this page's - so the
   same figure rendered "3,000" in one tile and "3.000" in the next on a
   German phone. */
const num = n => (n === null || n === undefined || n === '') ? '—'
  : Math.round(n).toLocaleString('en-CA');
/* One pluraliser, for the same reason. There were two: one closed over inside
   the market view and one open-coded in slotWord, and they disagreed about
   whether the count was grouped - "1000 cars" beside "1,000 km". */
const plural = (n, word) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

function when(iso) {
  const t = Date.parse(iso);
  if (!t) return '';
  const mins = (Date.now() - t) / 60000;
  if (mins < 1) return 'just now';
  if (mins < 60) return `${Math.round(mins)}m ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)}h ago`;
  return new Date(t).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
}
function stamp(iso) {
  const t = Date.parse(iso);
  return t ? new Date(t).toLocaleString('en-CA',
    { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
}
/* A placeholder that is a car, rather than a grey rectangle or the title
   printed a second time under the title. */
const CAR_GLYPH = `<svg width="34" height="22" viewBox="0 0 34 22" fill="none" aria-hidden="true">
  <path d="M3 15h28M6 15l2.4-7A3 3 0 0111.3 6h11.4a3 3 0 012.9 2l2.4 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="10" cy="17.5" r="2.4" stroke="currentColor" stroke-width="1.6"/>
  <circle cx="24" cy="17.5" r="2.4" stroke="currentColor" stroke-width="1.6"/></svg>`;

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* AutoTrader titles arrive as pipe-delimited dealer shouting:
   "BMW M5 4dr Sdn|STAGE 2|SAFETY CERTIFIED". The first segment is the car;
   the rest is a sales pitch that makes every card look the same. */
/* "12 of 12 half-hours" on a bot that checks every two hours.
   The word was written into four separate strings when the schedule happened
   to be half-hourly, and stayed there when it stopped being. */
/* "2.3h" and "24h" and "the last 24 hours" were three shapes for one idea. */
/* "re-read about every 120 minutes" is not how anyone says it. One function
   for a duration given in minutes, used wherever the schedule is described. */
function every(minutes) {
  const m = Number(minutes) || 0;
  if (m < 60) return `${m} minutes`;
  if (m % 60) return `${Math.round(m / 6) / 10} hours`;
  const h = m / 60;
  return h === 1 ? 'hour' : `${h} hours`;
}

function hours(n) {
  const h = Number(n) || 0;
  if (h < 1) return `${Math.round(h * 60)} minutes`;
  const rounded = h < 10 ? Math.round(h * 10) / 10 : Math.round(h);
  return `${rounded} hour${rounded === 1 ? '' : 's'}`;
}

function slotWord(cov, one_only) {
  const mins = (cov && cov.expected_interval_minutes) || 30;
  const one = mins === 30 ? 'half-hour'
    : mins === 60 ? 'hour'
    : mins % 60 === 0 ? `${mins / 60}-hour slot`
    : `${mins}-minute slot`;
  return one_only === false ? one : one + 's';
}

/* One name for the ratio, and one gate on the comparison.

   Both were written twice. The card said "/1000km" and the specification
   table said "Per 1,000 km" for the same number; the card showed the
   comparison only when `notable` was set and the sheet showed it whenever a
   percentage existed, so a car could carry a figure in the sheet and nothing
   on its card and the reader had no way to tell that from no figure at all.

   `notable` is gone. A percentage now needs twelve comparables to exist, and
   a figure that took twelve cars to earn is worth printing whether or not it
   is dramatic. Silence on a card means one thing: this car has no cohort
   big enough. The sheet says which. */
const PER_KM = 'per 1,000 km';

function comparableSays(cmp, l) {
  if (!cmp) return null;
  if (cmp.pct !== undefined) {
    const under = cmp.pct < 0, n = Math.round(Math.abs(cmp.pct));
    return {
      tone: under ? 'drop' : '',
      badge: `${n}% ${under ? 'under' : 'over'} the median of ${num(cmp.sample)}`,
      sentence: `${n}% ${under ? 'under' : 'over'} the median ${money(cmp.median)} `
        + `of ${plural(cmp.sample, 'comparable')} — ${esc(cmp.cohort || 'cars like it')}, `
        + `each within a third of this car's odometer.`,
    };
  }
  if (cmp.rank !== undefined) {
    return {
      tone: '',
      badge: `${ordinal(cmp.rank)} cheapest of ${num(cmp.of)}`,
      sentence: esc(cmp.why_not) + '.',
    };
  }
  return { tone: '', badge: null, sentence: cmp.why_not ? esc(cmp.why_not) + '.' : null };
}

const ordinal = n => (n % 100 >= 10 && n % 100 <= 20) ? `${n}th`
  : `${n}${ ({ 1: 'st', 2: 'nd', 3: 'rd' })[n % 10] || 'th' }`;

function carName(l) {
  const head = String(l.title || '').split('|')[0].trim();
  // A title that already opens with the year gets no second one. The names
  // the bot composes for cars whose card carried none start "2025 BMW M4",
  // and this happily made that "2025 2025 BMW M4".
  const year = (l.year && !head.startsWith(String(l.year))) ? `${l.year} ` : '';
  return (year + (head || [l.make, l.model].filter(Boolean).join(' '))).trim() || 'Listing';
}
function carExtras(l) {
  return String(l.title || '').split('|').slice(1)
    .map(s => s.trim()).filter(s => s && s.length < 40);
}

/* ----------------------------------------------------------------- helpers */
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const live = () => (app.data?.listings || []).filter(l => l.status === 'active');
const DAY = 86400000;
const arrivedRecently = l => Date.parse(l.first_seen) > Date.now() - DAY;
const visible = () => live().filter(l => !l.filtered);
const byId = id => (app.data?.listings || []).find(l => String(l.id) === String(id));

function lastEventOf(l) {
  const h = l.price_history || [];
  if (l.status === 'gone') return 'removed';
  if (h.length >= 2) {
    const a = h[h.length - 2]?.price, b = h[h.length - 1]?.price;
    if (a && b && b !== a) return b < a ? 'price_drop' : 'price_rise';
  }
  if (app.data && Date.parse(l.first_seen) > Date.now() - 36e5 * 24) return 'new';
  return null;
}
function priceMove(l) {
  const h = (l.price_history || []).filter(p => p.price);
  if (h.length < 2) return null;
  const was = h[h.length - 2].price, now = h[h.length - 1].price;
  return now === was ? null : { was, now, delta: now - was };
}

/* ----------------------------------------------------------------- trust */
function trustState() {
  const d = app.data;
  if (!d) return { state: 'ok', text: 'Loading…' };
  const run = d.last_run || {};

  // Offline outranks everything else this function can say. "Checked 3 days
  // ago" with no other context reads as "the bot is broken" when the truth
  // may be that the phone has no signal, and the two want opposite actions.
  if (app.offline) {
    const age = when(run.at || d.generated_at);
    return {
      state: 'stale',
      text: `Offline · ${age}`,
      alarm: {
        level: 'warn',
        text: `You are offline. This is the copy your phone saved, and the `
            + `last check in it ran ${age}. Nothing here has been re-read `
            + `since - cars may have sold or changed price.`,
        detail: `Saved copy published ${stamp(d.generated_at)}.`,
      },
    };
  }
  const cov = d.coverage || {};
  // Age of the last *check*, not of the file. They differ whenever the file
  // is rewritten without a check having happened, and the question being
  // asked here is about the site, not about the publish step.
  const ageMin = (Date.now() - Date.parse(run.at || d.generated_at)) / 60000;
  const expected = cov.expected_interval_minutes || 30;
  const failing = run.ok === false || (run.errors || []).length > 0;
  const stale = ageMin > expected * 3;
  // Coverage this poor means cars can arrive and go between checks, which is
  // worth an amber light even when the most recent check was a minute ago.
  const thin = cov.pct !== undefined && cov.pct < 50;

  // Nothing has ever run. That is not a fault, it is a fresh install, and
  // reporting it as "0% coverage" tells somebody who has just set this up
  // that it is already broken.
  if (!run.at) {
    return {
      state: 'stale',
      text: 'Not checked yet',
      alarm: {
        level: 'warn',
        text: 'No check has run yet. The first one records everything already on the site as a starting point rather than announcing all of it at you, so expect the feed to stay quiet until something actually changes.',
        detail: '',
      },
    };
  }

  if (failing) {
    return {
      state: 'bad',
      text: `Last check failed ${when(run.at)}`,
      alarm: {
        level: 'bad',
        text: 'The last check did not finish cleanly, so what you are looking at may be out of date.',
        detail: (run.errors || [])[0] || (run.invariants || [])[0] || '',
      },
    };
  }
  if (stale) {
    return {
      state: 'stale',
      text: `Checked ${when(run.at)}`,
      alarm: {
        level: 'warn',
        text: `No check has landed for ${Math.round(ageMin / 60)} hours, against one expected every ${expected} minutes. Cars may have come and gone since.`,
        // slots_covered, not successful: the percentage beside it is the
        // share of half-hour slots that had a check, and pairing it with the
        // number of runs printed "79.2% - 51 of 48 expected checks" on a page
        // whose Status tab said 38 of 48 two screens away. Both numbers were
        // true and the sentence was not.
        detail: (cov.pct !== undefined && !cov.too_short)
          ? `Coverage over the last ${hours(cov.window_hours)}: ${cov.pct}% — ${cov.slots_covered ?? cov.successful} of ${cov.expected} ${slotWord(cov)} had a check.` : '',
      },
    };
  }
  if (thin && !cov.too_short) {
    return {
      state: 'stale',
      text: `Checked ${when(run.at)}`,
      alarm: {
        level: 'warn',
        // slots_covered, to agree with the Status card and the strip. Using
        // the raw check count here said "22 of 48" beside a card reading
        // "17 of 48", which is one number too many for a page whose whole
        // argument is that its numbers can be trusted.
        text: `Only ${cov.pct}% of the last ${hours(cov.window_hours)} were watched — ${cov.slots_covered ?? cov.successful} of ${cov.expected} ${slotWord(cov)} had a check. A car can be listed and sold between checks at this rate.`,
        detail: cov.longest_gap_minutes
          ? `Longest gap: ${(cov.longest_gap_minutes / 60).toFixed(1)} hours.` : '',
      },
    };
  }
  return { state: 'ok', text: `Checked ${when(run.at)}` };
}

/* A newer build is cached and will run on the next load. Say so once. */
let versionBannerUp = false;
function newVersionReady() {
  if (versionBannerUp) return;
  versionBannerUp = true;
  const bar = el('div', 'newver');
  bar.setAttribute('role', 'status');
  const text = el('span', '', 'A newer version of this page is ready.');
  const btn = el('button', 'btn', 'Reload');
  btn.type = 'button';
  btn.addEventListener('click', () => location.reload());
  bar.appendChild(text);
  bar.appendChild(btn);
  document.body.appendChild(bar);
}

function renderTrust() {
  const t = trustState();
  store.set('lastTrust', { state: t.state, text: t.text, alarm: t.alarm || null });
  const wrap = document.getElementById('trust');
  wrap.dataset.state = t.state;
  document.getElementById('trust-text').textContent = t.text;
  const cov = app.data?.coverage;
  document.getElementById('trust-cov').innerHTML = cov
    ? `· <b class="num">${cov.pct}%</b> covered` : '';

  const alarm = document.getElementById('alarm');
  if (t.alarm) {
    alarm.hidden = false;
    alarm.dataset.level = t.alarm.level;
    document.getElementById('alarm-text').textContent = t.alarm.text;
    const detail = document.getElementById('alarm-detail');
    detail.innerHTML = t.alarm.detail ? `<code>${esc(t.alarm.detail)}</code>` : '';
  } else {
    alarm.hidden = true;
  }
}

/* ----------------------------------------------------------------- tabs */
function renderTabs() {
  const host = document.getElementById('tabs');
  const unread = app.data ? feedEvents().filter(e => isUnread(e)).length : 0;
  host.innerHTML = '';
  for (const v of VIEWS) {
    const b = el('button', 'tab');
    b.type = 'button';
    b.dataset.viewLink = v.id;
    b.setAttribute('role', 'link');
    if (app.view === v.id) b.setAttribute('aria-current', 'page');
    let n = '';
    if (v.id === 'feed' && unread) n = `<span class="tab__n num" data-unread="1">${unread}</span>`;
    else if (v.id === 'listings' && app.data) n = `<span class="tab__n num">${visible().length}</span>`;
    else if (v.id === 'searches' && app.data) n = `<span class="tab__n num">${(app.data.searches || []).length}</span>`;
    else n = '<span class="tab__n num"></span>';
    b.innerHTML = `<span>${v.label}</span>${n}`;
    b.addEventListener('click', () => go(v.id));
    host.appendChild(b);
  }
}

function go(view, opts = {}) {
  app.view = view;
  if (!opts.silent) location.hash = `#/${view}`;
  for (const s of document.querySelectorAll('.view')) s.hidden = s.dataset.view !== view;
  renderTabs();
  render();
  if (opts.focus !== false) document.getElementById('main').focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
}

/* ----------------------------------------------------------------- feed */
function feedEvents() {
  return (app.data?.events || []);
}
const isUnread = e => !app.lastSeen || e.at > app.lastSeen;

/* "a, b and c" - not "a and b and c", which is what joining three names with
   " and " gives you, and what this said the day a second and third search
   arrived. */
function andList(items) {
  const parts = items.filter(Boolean);
  if (parts.length <= 1) return parts[0] || '';
  return parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1];
}

/* Shown once, on a browser that has never opened this before. It answers the
   four questions a person has on first sight and then never appears again -
   an explainer you have to dismiss twice is worse than no explainer. */
function welcome() {
  const d = app.data;
  const box = el('section', 'state measure');
  box.style.marginBottom = 'var(--s6)';
  const searches = (d.searches || []).map(s => s.name);
  const cov = d.coverage || {};
  box.innerHTML = `
    <h2>This is watching ${searches.length} search${searches.length === 1 ? '' : 'es'} on autotrader.ca</h2>
    <p>${esc(andList(searches) || 'nothing yet')} — ${visible().length} cars live
       right now, re-read about every ${every(cov.expected_interval_minutes || 30)}.</p>
    <p><b>Alerts</b> go to ${(d.notify?.active || []).join(', ') || 'nowhere yet — no channel is switched on'}.
       <b>Feed</b> is what changed since you last looked. <b>Status</b> says whether
       the bot itself is healthy, and the dot beside the title up there says it at a glance.</p>
    <p>Add a search by pasting its link on the Searches tab.</p>`;
  const b = el('button', 'btn btn--primary', 'Got it');
  b.type = 'button';
  b.addEventListener('click', () => {
    store.set('welcomed', true);
    app.firstVisit = false;
    renderFeed();
    document.getElementById('main').focus({ preventScroll: true });
  });
  box.appendChild(b);
  return box;
}

function renderFeed() {
  const host = document.querySelector('[data-view="feed"]');
  host.innerHTML = '';
  const head = el('div', 'view__head');
  head.classList.add('measure');
  head.innerHTML = `<h1 id="feed-h">What changed</h1>
    <p>Everything that has happened to a car you are watching, newest first — including
       cars your rules hide, which the run counters never counted.</p>`;
  host.appendChild(head);
  if (app.firstVisit && !store.get('welcomed', false)) {
    host.appendChild(welcome());
  }

  if (app.firstVisit && store.get('welcomed', false)) {
    const p = el('p', 'why measure');
    p.innerHTML = `<b>First visit.</b> Everything already on the site is recorded as a
      starting point rather than announced at you. From now on this shows only what
      has changed since you last looked.`;
    host.appendChild(p);
  }

  const events = feedEvents();
  if (!events.length) {
    // Three different nothings, and saying the wrong one is the page lying
    // about its own state: no check has run; a check ran and found no cars at
    // all (a brand new search, or one just swapped in); cars are being watched
    // and none of them has done anything yet.
    const noCarsAtAll = !(app.data.listings || []).length;
    host.appendChild(emptyState(
      !app.data.last_run ? 'Nothing has been checked yet'
        : noCarsAtAll ? 'No cars found yet'
        : 'Nothing has changed yet',
      !app.data.last_run
        ? 'The first check has not run. When it does, everything already on the site is recorded as a starting point — you will hear about what changes after that, not about the back catalogue.'
        : noCarsAtAll
        ? 'The searches have not turned up a car yet. That is either a narrow search or a new one — the Searches tab says which, and how many listings each one read last time.'
        : 'Every car the searches found was already there when the bot started watching. This fills up as prices move and cars come and go.'));
    return;
  }

  const unread = events.filter(isUnread);
  if (unread.length) {
    const b = el('div', 'bar');
    const btn = el('button', 'btn btn--primary', `Mark ${unread.length} as seen`);
    btn.type = 'button';
    btn.addEventListener('click', () => {
      app.lastSeen = new Date().toISOString();
      store.set('lastSeen', app.lastSeen);
      renderTabs(); renderFeed();
    });
    b.appendChild(btn);
    host.appendChild(b);
  }

  const groups = new Map();
  for (const e of events) {
    if (!groups.has(e.kind)) groups.set(e.kind, []);
    groups.get(e.kind).push(e);
  }
  for (const kind of KIND_ORDER) {
    const rows = groups.get(kind);
    if (!rows || !rows.length) continue;
    const sec = el('section', 'section measure');
    const n = rows.filter(isUnread).length;
    sec.innerHTML = `<div class="section__head">
        <h2>${KIND[kind].group}</h2>
        <span class="count num">${n === rows.length ? `${rows.length} new to you`
          : n ? `${rows.length} · ${n} new to you` : rows.length}</span>
      </div>`;
    const list = el('ul', 'feed');
    let markerDone = false;
    // Cars your rules keep come first within the group, then the hidden ones.
    // This sliced the first 60 by recency, and with 57 hidden cars arriving in
    // the same minute as the 26 you can buy, twenty of yours fell off the end
    // of a list whose whole job is to show you what changed.
    const ordered = [...rows.filter(e => !e.filtered), ...rows.filter(e => e.filtered)];
    for (const e of ordered.slice(0, 60)) {
      if (!markerDone && !isUnread(e) && rows.some(isUnread)) {
        const m = el('li'); m.innerHTML = `<div class="marker">Seen before this</div>`;
        list.appendChild(m); markerDone = true;
      }
      list.appendChild(eventRow(e));
    }
    if (ordered.length > 60) {
      const more = el('li', 'note', `and ${rows.length - 60} older`);
      more.style.padding = 'var(--s3) var(--s4)';
      list.appendChild(more);
    }
    sec.appendChild(list);
    host.appendChild(sec);
  }
}

function eventRow(e) {
  const li = el('li');
  const b = el('button', 'ev' + (isUnread(e) ? ' ev--unread' : ''));
  b.type = 'button';
  b.dataset.kind = e.kind;

  let fig = '', sub = [];
  if (e.kind === 'price_drop' || e.kind === 'price_rise') {
    const cls = e.kind === 'price_drop' ? 'drop' : 'rise';
    fig = `<span class="ev__fig num ${cls}">${signed(e.delta)}</span>`;
    sub.push(`<span class="ev__was num">${money(e.old_price)}</span>`);
    sub.push(`<span class="num">${money(e.new_price)}</span>`);
  } else if (e.kind === 'priced') {
    fig = `<span class="ev__fig num">${money(e.new_price)}</span>`;
    sub.push('was call for price');
  } else if (e.price) {
    fig = `<span class="ev__fig num">${money(e.price)}</span>`;
  } else if (e.kind === 'new') {
    // A car with no price is a dealer withholding one, and the row said
    // nothing at all about it - a title and the word "alerted".
    fig = '<span class="ev__fig">Call for price</span>';
  }
  if (e.filtered) sub.push(`<span>hidden — ${esc(e.filter_reason || 'a rule of yours')}</span>`);
  else if (e.delivery?.state === 'sent') sub.push('<span>alerted</span>');
  else if (e.delivery?.state === 'queued') sub.push('<span>queued, not sent yet</span>');
  else if (e.delivery?.state === 'quiet') sub.push(`<span>${esc(e.delivery.text)}</span>`);

  // carName, not a second copy of it. There were two, they were the same
  // line, and when one learned not to print "2025 2025 BMW M4" the other
  // carried on doing it - on the Feed, which is the view people read most.
  const name = carName(e);
  // Named by its own content, with the kind word carried in a visually
  // hidden span rather than an aria-label.
  //
  // The aria-label version failed WCAG 2.5.3: it said "Price drop: 2018 BMW
  // M5. $66,888 $65,888 alerted" while the card visibly read "28h ago … 2018
  // BMW M5 … −$1,000 … $66,888 $65,888 alerted". Someone driving this by
  // voice reads what is on screen and says it, and no phrase they can see
  // matches the name the button answers to. Building the name out of the
  // content instead means the two cannot disagree.
  b.innerHTML =
    `<span class="sr">${KIND[e.kind].label}.</span>
     <time class="ev__when" datetime="${esc(e.at)}">${when(e.at)}</time>
     <span class="ev__title">${esc(name)}</span>${fig}
     <span class="ev__sub">${sub.join('')}</span>`;
  b.addEventListener('click', () => openSheet(e.listing_id));
  li.appendChild(b);
  return li;
}

/* ----------------------------------------------------------------- listings */
function listingPool() {
  let rows = (app.data?.listings || []);
  if (app.search !== 'all') rows = rows.filter(l => l.search_id === app.search);

  const chip = app.chip;
  if (chip === 'all') rows = rows.filter(l => l.status === 'active' && (app.showHidden || !l.filtered));
  else if (chip === 'drops') rows = rows.filter(l => l.status === 'active' && priceMove(l)?.delta < 0);
  else if (chip === 'new') rows = rows.filter(l => l.status === 'active' && !l.filtered && arrivedRecently(l));
  else if (chip === 'unpriced') rows = rows.filter(l => l.status === 'active' && l.unpriced && !l.filtered);
  else if (chip === 'gone') rows = rows.filter(l => l.status === 'gone');
  else if (chip === 'hidden') rows = rows.filter(l => l.status === 'active' && l.filtered);
  else if (chip === 'private') rows = rows.filter(l => l.status === 'active'
    && !l.filtered && l.seller_type === 'private');
  else if (chip === 'mine') rows = rows.filter(l => marks.of(l.id).shortlisted);
  else if (chip === 'dropped') rows = rows.filter(l => marks.of(l.id).dismissed);

  // A car you said you were not interested in is not deleted - it is kept and
  // findable under its own chip. It just stops competing for attention in the
  // list you actually scroll.
  if (chip !== 'dropped') rows = rows.filter(l => !marks.of(l.id).dismissed);

  const q = app.q.trim().toLowerCase();
  if (q) {
    rows = rows.filter(l => [l.title, l.location, l.color, l.trim, l.seller, l.model]
      .filter(Boolean).join(' ').toLowerCase().includes(q));
  }
  const sort = SORTS.find(s => s.id === app.sort) || SORTS[0];
  return { sort, ...ranked(rows, sort) };
}

function renderListings() {
  const host = document.querySelector('[data-view="listings"]');
  host.innerHTML = '';
  const head = el('div', 'view__head');
  head.innerHTML = `<h1 id="listings-h">Listings</h1>
    <p>Every car the searches have turned up. Cars your rules hide are kept and
       explained rather than dropped — the number you can click on beats the number
       that quietly omits.</p>`;
  host.appendChild(head);

  const bar = el('div', 'bar');
  bar.innerHTML = `
    <label class="field">
      <span class="sr">Filter listings</span>
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" style="flex:none;color:var(--text-3)">
        <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" stroke-width="1.4"/>
        <path d="M9.5 9.5L13 13" stroke="currentColor" stroke-width="1.4"/></svg>
      <input type="search" id="q" placeholder="Colour, city, trim, seller…" value="${esc(app.q)}">
    </label>
    <label class="sr" for="sort">Sort by</label>
    <select id="sort">${SORTS.map(s =>
      `<option value="${s.id}"${s.id === app.sort ? ' selected' : ''}>${s.label}</option>`).join('')}</select>
    <label class="sr" for="search-pick">Search</label>
    <select id="search-pick">
      <option value="all">All searches</option>
      ${(app.data.searches || []).map(s =>
        `<option value="${esc(s.id)}"${s.id === app.search ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}
    </select>`;
  host.appendChild(bar);

  const kept = l => !marks.of(l.id).dismissed;
  // Within the selected search, not across all of them. "Live 26" sat above a
  // grid of 5 whenever a single search was picked, and the chip you clicked
  // then showed a different number again.
  const mine = l => app.search === 'all' || l.search_id === app.search;
  const counts = {
    all: live().filter(l => mine(l) && !l.filtered && kept(l)).length,
    private: live().filter(l => mine(l) && !l.filtered && kept(l)
      && l.seller_type === 'private').length,
    mine: (app.data.listings || []).filter(l => mine(l) && marks.of(l.id).shortlisted).length,
    dropped: (app.data.listings || []).filter(l => mine(l) && marks.of(l.id).dismissed).length,
    drops: live().filter(l => mine(l) && priceMove(l)?.delta < 0).length,
    new: live().filter(l => mine(l) && !l.filtered && arrivedRecently(l)).length,
    unpriced: live().filter(l => mine(l) && l.unpriced && !l.filtered).length,
    hidden: live().filter(l => mine(l) && l.filtered).length,
    gone: (app.data.listings || []).filter(l => mine(l) && l.status === 'gone').length,
  };
  const chips = el('div', 'chips');
  chips.setAttribute('role', 'group');
  chips.setAttribute('aria-label', 'Filter by state');
  for (const [id, label] of [['all', 'Live'], ['new', 'New'], ['drops', 'Price drops'],
                             ['private', 'Private sellers'], ['mine', 'Shortlisted'],
                             ['unpriced', 'Call for price'], ['hidden', 'Hidden by a rule'],
                             ['gone', 'Gone'], ['dropped', 'Not interested']]) {
    // A filter that would return nothing is a control that does nothing,
    // sitting beside controls that do. That was the stated rule and it was
    // applied to three of the nine chips, so the row read "Price drops 0 ·
    // Call for price 0 · Gone 0" - three dead buttons wearing a zero.
    //
    // "Live" always stays: it is where the other chips send you back to, and
    // a listings page with no way back to the listings is worse than a zero.
    if (id !== 'all' && !counts[id] && app.chip !== id) continue;
    const c = el('button', 'chip');
    c.type = 'button';
    c.setAttribute('aria-pressed', app.chip === id ? 'true' : 'false');
    c.innerHTML = `${label}<span class="n num">${counts[id] ?? 0}</span>`;
    c.addEventListener('click', () => { app.chip = id; renderListings(); });
    chips.appendChild(c);
  }
  if (app.chip === 'all' && counts.hidden) {
    const btn = el('button', 'chip');
    btn.type = 'button';
    btn.setAttribute('aria-pressed', String(app.showHidden));
    btn.innerHTML = `Include hidden<span class="n num">${counts.hidden}</span>`;
    btn.addEventListener('click', () => { app.showHidden = !app.showHidden; renderListings(); });
    chips.appendChild(btn);
  }
  host.appendChild(chips);

  const pool = listingPool();
  const total = pool.has.length + pool.absent.length;
  if (!total) {
    host.appendChild(noResults(counts));
    return;
  }

  const grid = el('div', 'grid');
  let drawn = 0;
  for (const l of pool.has.slice(0, 300)) { grid.appendChild(card(l)); drawn++; }
  if (pool.absent.length && drawn < 300) {
    // Not "unsortable" - the reader did not ask about sorting, they asked for
    // the cheapest. This says which cars the question could not be asked of,
    // in the words of the thing that is missing.
    const split = el('p', 'grid__split');
    split.textContent = `${plural(pool.absent.length, 'car')} with ${pool.sort.absent}`;
    grid.appendChild(split);
    for (const l of pool.absent.slice(0, 300 - drawn)) { grid.appendChild(card(l)); drawn++; }
  }
  host.appendChild(grid);
  if (total > drawn) {
    host.appendChild(el('p', 'note', `Showing the first ${num(drawn)} of ${num(total)}.`));
  }
  bar.querySelector('#q').addEventListener('input', e => {
    app.q = e.target.value;
    const keep = document.activeElement === e.target;
    renderListings();
    if (keep) { const i = document.getElementById('q'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
  });
  bar.querySelector('#sort').addEventListener('change', e => { app.sort = e.target.value; renderListings(); });
  bar.querySelector('#search-pick').addEventListener('change', e => { app.search = e.target.value; renderListings(); });
}

/* Zero results, and specifically why - with the offending control offered
   back rather than a shrug. */
function noResults(counts) {
  const s = el('div', 'state');
  if (app.q) {
    s.innerHTML = `<h2>Nothing matches “${esc(app.q)}”</h2>
      <p>The text filter is applied to the title, city, colour, trim and seller.</p>`;
    const b = el('button', 'btn', 'Clear the text filter');
    b.type = 'button';
    b.addEventListener('click', () => { app.q = ''; renderListings(); });
    s.appendChild(b);
    return s;
  }
  if (app.chip === 'hidden' && !counts.hidden) {
    s.innerHTML = `<h2>Nothing is hidden right now</h2>
      <p>Every car the searches found passed your rules.</p>`;
    return s;
  }
  if (app.search !== 'all') {
    const sr = (app.data.searches || []).find(x => x.id === app.search);
    const why = sr?.health?.shut_out;
    s.innerHTML = `<h2>${esc(sr?.name || 'This search')} has nothing to show</h2>
      <p>${why ? `It read the site fine and every car was turned away: ${esc(why)}.`
                : 'It has not kept any cars yet.'}</p>`;
    const b = el('button', 'btn', 'Show all searches');
    b.type = 'button';
    b.addEventListener('click', () => { app.search = 'all'; renderListings(); });
    s.appendChild(b);
    return s;
  }
  // On the live chip with nothing live, "back to live listings" is a button
  // that does nothing to a page you are already on. There are two different
  // emptinesses here and only one of them has somewhere to go back to.
  if (app.chip === 'all') {
    s.innerHTML = `<h2>No cars yet</h2>
      <p>The searches have not turned up a car. The Searches tab says how many
         listings each one read last time it ran.</p>`;
    return s;
  }
  s.innerHTML = `<h2>Nothing here</h2><p>No car is in this state at the moment.</p>`;
  const b = el('button', 'btn', 'Back to live listings');
  b.type = 'button';
  b.addEventListener('click', () => { app.chip = 'all'; renderListings(); });
  s.appendChild(b);
  return s;
}

// h2, not h3. These sit directly under the view's h1 with nothing between,
// and a skipped level is how a screen reader user loses the outline.
function emptyState(title, body) {
  const s = el('div', 'state');
  s.innerHTML = `<h2>${esc(title)}</h2><p>${esc(body)}</p>`;
  return s;
}

// Reset per render: how many photos are worth blocking on. Six covers the
// first screen at every width this is designed for.
let eagerSlots = 0;

function shot(l, cls) {
  const box = el('div', cls || 'card__shot');
  // Ours first. The seller's CDN is a fallback rather than the source: it is
  // unreachable offline, unreachable from the installed app on a plane, and
  // it drops the picture the day the car is delisted.
  const src = l.thumb || (l.images || [])[0];
  // "No photo" under a card whose own footer says "12 photos" reads as a bug.
  // Three different things are being said here and they are not the same:
  // the seller published none, we have not copied one yet, or this car is
  // hidden and never will be.
  const fallback = (failedToLoad) => {
    const has = (l.images || []).length;
    // A fourth case, and the one that was being blamed on the bot: the file
    // exists, the bot copied it, and this device cannot reach it. Saying
    // "photo not copied yet" there is the page blaming itself for the
    // reader's aeroplane.
    const why = (failedToLoad && l.thumb) ? 'photo not loaded'
              : l.filtered ? 'not kept for hidden cars'
              : has ? 'photo not copied yet'
              : 'no photo';
    box.innerHTML = `<div class="shot__fallback">${CAR_GLYPH}
        <span>${why}</span>
      </div>`;
  };
  if (!src) { fallback(); return box; }
  const img = new Image();
  // The first screenful is what the page is judged on, and a lazy image
  // above the fold is a grey box that fills in after you have already looked
  // at it. Everything below stays lazy - there are two hundred of these.
  const eager = eagerSlots > 0;
  if (eager) eagerSlots -= 1;
  img.loading = eager ? 'eager' : 'lazy';
  img.fetchPriority = eager ? 'high' : 'low';
  img.decoding = 'async';
  img.width = 400; img.height = 300;
  img.alt = '';
  img.src = src;
  img.addEventListener('error', () => fallback(true), { once: true });
  box.appendChild(img);
  return box;
}

const sellerWord = kind => kind === 'private' ? 'private seller'
                        : kind === 'dealer' ? 'dealer' : '';

function card(l) {
  const b = el('button', 'card');
  b.type = 'button';
  if (l.status === 'gone') b.classList.add('card--gone');
  if (l.filtered) b.classList.add('card--hidden');
  if (app.freshIds.has(String(l.id))) b.classList.add('is-fresh');

  const move = priceMove(l);
  const kind = lastEventOf(l);
  const cmp = app.data.comparables?.[String(l.id)];

  if (!l.filtered) b.appendChild(shot(l));

  const body = el('div', 'card__body');
  let flag = '';
  if (kind) flag = `<span class="flag flag--${KIND[kind].flag}">${KIND[kind].label}</span>`;

  const price = l.unpriced
    ? `<b>Call for price</b>`
    : `<b class="num">${money(l.price)}</b>` +
      (move && move.delta < 0
        ? `<span class="card__was num">${money(move.was)}</span><span class="card__delta num drop">${signed(move.delta)}</span>`
        : move && move.delta > 0
        ? `<span class="card__was num">${money(move.was)}</span><span class="card__delta num rise">${signed(move.delta)}</span>`
        : '');

  const facts = [];
  if (l.mileage_km) facts.push(`<span class="num">${km(l.mileage_km)}<u> km</u></span>`);
  // money(), not a bare $ and Math.round: "$1113" sat next to "$69,000" on
  // eleven of twenty-six cards, the same currency formatted two ways.
  if (l.per_1000km) facts.push(`<span class="num">${money(l.per_1000km)}<u> ${PER_KM}</u></span>`);
  // "0 km away" reads as a missing value, not as "this one is in your city".
  if (l.distance_km !== undefined && l.distance_km !== null) {
    facts.push(l.distance_km < 1
      ? '<span>right here</span>'
      : `<span class="num">${km(l.distance_km)}<u> km away</u></span>`);
  }
  if (l.location) facts.push(`<span>${esc(l.location)}</span>`);

  const foot = [];
  const says = comparableSays(cmp, l);
  if (says?.badge) foot.push(`<span class="${says.tone}">${says.badge}</span>`);
  if (l.days_listed !== undefined) foot.push(`<span class="num">${daysListed(l.days_listed)}</span>`);
  if (l.photo_count) foot.push(`<span class="num">${l.photo_count} photo${
    l.photo_count === 1 ? '' : 's'}</span>`);
  // Said, not implied. A greyed price is a de-emphasis; it does not tell you
  // that alerts about this car are switched off, which is the thing you would
  // want to know before wondering why it has gone quiet.
  // Dealer or private, on the metadata line rather than among the facts.
  // A fourth item pushed the facts row over the card's width and orphaned
  // "dealer" onto a line of its own on every single card.
  if (l.seller_type) foot.push(`<span>${esc(sellerWord(l.seller_type))}</span>`);
  const yourMarks = marks.of(l.id);
  if (yourMarks.muted) foot.push('<span>muted — no alerts</span>');
  if (yourMarks.dismissed) foot.push('<span>not interested</span>');

  // Your marks, on the card. A shortlist you can only see by opening every
  // car one at a time is not a shortlist, and a note you wrote last week is
  // worth nothing if the list will not show it to you.
  const mine = yourMarks;
  if (mine.shortlisted) b.classList.add('card--mine');
  if (mine.dismissed) b.classList.add('card--dropped');
  if (mine.muted) b.classList.add('card--muted');

  body.innerHTML =
    `${flag}
     <div class="card__price">${price}</div>
     <div class="card__title">${esc(carName(l))}</div>
     <div class="facts">${facts.join('')}</div>` +
    (l.filtered ? `<p class="rule">Hidden: ${esc(l.filter_reason || 'a rule of yours')}</p>` : '') +
    (mine.note ? `<p class="yours">${esc(mine.note)}</p>` : '') +
    (foot.length ? `<div class="card__foot">${foot.join('')}</div>` : '');
  b.appendChild(body);
  b.setAttribute('aria-label',
    `${carName(l)}, ${l.unpriced ? 'call for price' : money(l.price)}` +
    (l.mileage_km ? `, ${km(l.mileage_km)} kilometres` : '') +
    (l.filtered ? `, hidden: ${l.filter_reason || 'a rule'}` : '') +
    (mine.shortlisted ? ', on your shortlist' : '') +
    (mine.muted ? ', muted' : '') +
    (mine.dismissed ? ', dismissed' : '') +
    (mine.note ? `. Your note: ${mine.note}` : ''));
  b.addEventListener('click', () => openSheet(l.id));
  return b;
}

/* ----------------------------------------------------------------- market */
// "base" is a claim the listing never made. A car with no trim in its title
// is a car with no trim in its title.
function sentence(text) {
  const t = String(text || '').trim();
  if (!t) return '';
  return t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? '' : '.');
}

/* Two or three prices, written out. A median of two numbers is one of the
   two numbers with a statistical word in front of it. */
function priceList(prices) {
  const all = (prices || []).map(money);
  if (!all.length) return '—';
  if (all.length === 1) return all[0];
  return all.slice(0, -1).join(', ') + ' and ' + all[all.length - 1];
}

/* Every table on this page, inside its own horizontal scroller. Five call
   sites built `el('table','tbl')` by hand and not one of them wrapped it, so
   the Status tab's parser ladder was 365px wide in a 358px column - spilling
   seven pixels under body{overflow-x:hidden}, invisible and unreachable. The
   sixth call site would have forgotten it too. */
function table(html) {
  const wrap = el('div', 'tblwrap');
  const t = el('table', 'tbl');
  t.innerHTML = html;
  wrap.appendChild(t);
  return wrap;
}

/* The trim buckets are the handful of words that move a price - Competition,
   Touring, CS, Carbon, LCI - and everything else. "base" is the everything
   else, and calling it "No trim named" was wrong about most of the cars in
   it: a car titled "xDrive30i Premium Enhanced Package" names a trim, just
   not one this groups on. */
const TRIM_NAMES = { cs: 'CS', lci: 'LCI', competition: 'Competition',
                    touring: 'Touring', carbon: 'Carbon' };

function trimLabel(name) {
  if (name === 'base' || !name) return 'Other';
  // Capitalising the first letter turned the CS bucket into "Cs", which is
  // not a car BMW makes.
  return TRIM_NAMES[name] || name.charAt(0).toUpperCase() + name.slice(1);
}

function renderMarket() {
  const host = document.querySelector('[data-view="market"]');
  host.innerHTML = '';
  const m = app.data.market || {};
  const head = el('div', 'view__head measure');
  // Listings counts what you can see; the market is the whole market. Two
  // different numbers under one word is how a dashboard loses trust, so the
  // difference is spelled out rather than left for the reader to notice.
  const hiddenHere = (app.data.listings || [])
    .filter(l => l.status === 'active' && l.filtered).length;
  head.innerHTML = `<h1 id="market-h">The market</h1>
    <p>What the cars say together, rather than what one says. The tiles count
       all ${m.live ?? 0} listings the searches returned${hiddenHere
         ? `, the ${hiddenHere} your rules hide included` : ''};
       the per-model figures below count only the ${(m.live ?? 0) - hiddenHere}
       you could actually buy, because a median of the cars a rule rejects is
       a market you are not shopping in. Everything carries how many cars it
       is drawn from.</p>`;
  host.appendChild(head);

  // The window first, because it decides how much of the rest to believe.
  if (m.window) {
    const w = el('p', 'why measure');
    w.innerHTML = m.window.thin
      ? `<b>Read this as a snapshot.</b> ${esc(m.window.note)}`
      : `<b>${m.window.cars_with_two_prices}</b> cars have been priced more than
         once over ${m.window.watching_days} days of watching.`;
    host.appendChild(w);
  }

  const v = m.velocity || {}, d = m.discounting || {};
  const st = m.still_listed_days || {}, lt = m.listed_days || {};
  const days = n => `${n}<small style="display:inline"> day${n === 1 ? '' : 's'}</small>`;
  // Three of these six are bounded by how long the bot has been watching
  // rather than by the market. Left unmarked, a two-day-old watch reports a
  // market that turns over in two days, which is a statement about the bot.
  const sinceWatch = v.window_is_the_watch;
  const stats = el('dl', 'stats');
  stats.innerHTML = `
    <div class="stat"><dt>${sinceWatch ? 'Arrived since watching began' : 'Arrived this week'}</dt>
      <dd class="num">${v.arrived_7d ?? '—'}</dd>
      ${sinceWatch ? `<dd class="stat__note">the watch is ${plural(st.watching_days ?? 0, 'day')}
        old, so that is all of them rather than this week's</dd>` : ''}</div>
    <div class="stat"><dt>${sinceWatch ? 'Left since watching began' : 'Left this week'}</dt>
      <dd class="num">${v.left_7d ?? '—'}</dd></div>
    <div class="stat"><dt>Still listed, median</dt>
      <dd class="num">${st.median == null ? '—'
        : (st.censored && !st.median) ? '—'
        : (st.censored ? '<small style="display:inline">at least </small>' : '') + days(st.median)}</dd>
      <dd class="stat__note">${
        st.median == null ? 'nothing to measure yet'
        : (st.censored && !st.median)
          ? 'every car here arrived after the watch on it started, so none of '
            + 'them has a measurable age yet'
        : st.censored ? 'nothing has been watched longer than this'
        : `longest ${days(st.longest ?? 0)}`}</dd></div>
    <div class="stat"><dt>Cars discounted</dt><dd class="num">${d.cars ?? 0}</dd>
      <dd class="stat__note">${d.total ? money(d.total) + ' off in total' : 'none yet'}</dd></div>
    <div class="stat"><dt>Listed before coming down</dt>
      <dd class="num">${lt.median == null ? '—' : days(lt.median)}</dd>
      <dd class="stat__note">from ${lt.n ?? 0} that came down${lt.biased_short
        ? ' — only short-lived ones can finish inside a watch this young' : ''}</dd></div>
    <div class="stat"><dt>Listed right now</dt><dd class="num">${m.live ?? 0}</dd>
      <dd class="stat__note">${hiddenHere
        ? `${(m.live ?? 0) - hiddenHere} live · ${hiddenHere} hidden · `
        : ''}${m.gone ?? 0} gone and kept</dd></div>`;
  host.appendChild(stats);
  if (lt.note) {
    host.appendChild(el('p', 'note measure',
      '“Listed before coming down” is ' + lt.note + '.'));
  }

  // One section per model. This was a single "asking price by year" chart
  // drawn across every car in state, which was fine while the watch held one
  // model and became nonsense the day it held three: a 2017 row reading
  // "median $51,972, from $15,980 to $62,999" was an ordinary X3 and an M3
  // averaged together, and a trim table said "competition, 23 cars" by
  // counting M4 Competitions and X3 M Competitions as the same thing.
  for (const row of Object.values(m.by_model || {})) {
    const sec = el('section', 'section measure');
    const count = row.n
      ? `${row.n} to buy${row.hidden ? ` · ${row.hidden} hidden` : ''}`
      : `none to buy · ${row.hidden} hidden`;
    sec.innerHTML = `<div class="section__head">
        <h2>${esc(row.label)}</h2><span class="count num">${count}</span></div>`;

    if (!row.n) {
      sec.appendChild(el('p', 'note', `Every ${row.label} the searches found is `
        + `outside your rules. The Listings tab says which rule, car by car.`));
      host.appendChild(sec);
      continue;
    }
    sec.appendChild(el('p', 'note', row.thin
      ? `${priceList(row.prices)} — ${row.n} car${row.n === 1 ? '' : 's'}, which is `
        + `too few for a median. The asking prices themselves are above.`
      : `Median ${money(row.median)}, ${money(row.low)} to ${money(row.high)}, `
        + `from ${row.n} cars.`));

    const years = Object.entries(row.by_year || {});
    const fat = years.filter(([, y]) => !y.thin);
    if (fat.length) sec.appendChild(rangeChart(fat));
    const thin = years.filter(([, y]) => y.thin);
    if (thin.length) {
      sec.appendChild(table(`<thead><tr><th>Year</th><th class="r">Cars</th>
          <th class="r">Asking</th></tr></thead><tbody>` +
        thin.map(([year, y]) =>
          `<tr><td>${esc(year)}</td><td class="r num">${y.n}</td>
            <td class="r num">${priceList(y.prices)}</td></tr>`).join('') + '</tbody>'));
    }

    const trims = Object.entries(row.by_trim || {}).filter(([, t]) => t.n > 1);
    if (trims.length > 1) {
      const trimTable = table(`<thead><tr><th>Trim</th><th class="r">Cars</th>
          <th class="r">Asking</th></tr></thead><tbody>` +
        trims.map(([name, r]) =>
          `<tr><td>${esc(trimLabel(name))}</td><td class="r num">${r.n}</td>
            <td class="r num">${r.thin ? priceList(r.prices) : money(r.median)}</td>
            </tr>`).join('') + '</tbody>');
      trimTable.style.marginTop = 'var(--s4)';
      sec.appendChild(trimTable);
      sec.appendChild(el('p', 'note',
        'Asking is a median where there are five or more of a trim, and the '
        + 'prices themselves below that. "Other" is every trim that is not '
        + 'one of the words that move a price.'));
    }
    host.appendChild(sec);
  }

  // Whether the deal score means anything, said either way.
  const check = app.data.score_check;
  if (check) {
    const sec = el('section', 'section measure');
    sec.innerHTML = `<div class="section__head"><h2>Is the deal score worth anything?</h2></div>
      <p class="note" style="margin-top:0">${esc(sentence(check.verdict))}</p>`;
    if (check.cheap_rate !== undefined) {
      sec.appendChild(table(`<thead><tr><th>Called</th><th class="r">Cars</th>
          <th class="r">Later cut the price</th></tr></thead><tbody>
        <tr><td>cheap for its kind</td><td class="r num">${check.called_cheap}</td>
          <td class="r num">${check.cheap_rate}%</td></tr>
        <tr><td>dear for its kind</td><td class="r num">${check.called_dear}</td>
          <td class="r num">${check.dear_rate}%</td></tr></tbody>`));
    }
    host.appendChild(sec);
  }

  const out = el('section', 'section measure');
  out.innerHTML = `<div class="section__head"><h2>Take it with you</h2></div>`;
  const bar = el('div', 'bar');
  for (const [label, make] of [['Listings as CSV', csvOfListings],
                               ['Everything as JSON', () => JSON.stringify(app.data, null, 1)]]) {
    const b = el('button', 'btn', label);
    b.type = 'button';
    b.addEventListener('click', () => download(label.includes('CSV') ? 'listings.csv' : 'autotrader.json', make()));
    bar.appendChild(b);
  }
  out.appendChild(bar);
  host.appendChild(out);
}

/* One row per year: the range as a bar, the median as a tick. A box plot
   without the jargon, and it degrades to a table on a phone. */
function rangeChart(years) {
  const all = years.flatMap(([, r]) => [r.low, r.high]);
  const min = Math.min(...all), max = Math.max(...all), span = (max - min) || 1;
  const wrap = el('div');
  for (const [year, r] of years) {
    const row = el('div');
    row.style.cssText = 'display:grid;grid-template-columns:48px 1fr auto;gap:var(--s3);align-items:center;padding:var(--s1) 0';
    const at = v => ((v - min) / span) * 100;
    // Full range as a hairline, middle half as the solid bar, median as the
    // tick. One $150,000 outlier otherwise squashes every other year into a
    // smudge and the chart stops saying anything.
    const q1 = r.q1 ?? r.low, q3 = r.q3 ?? r.high;
    row.innerHTML = `
      <span class="num" style="font-size:var(--t-small)">${esc(year)}</span>
      <span style="position:relative;height:16px;display:block">
        <span style="position:absolute;left:${at(r.low)}%;width:${Math.max(at(r.high) - at(r.low), 0.4)}%;top:7px;height:1px;background:var(--line-strong)"></span>
        <span style="position:absolute;left:${at(q1)}%;width:${Math.max(at(q3) - at(q1), 0.8)}%;top:5px;height:5px;border-radius:2px;background:var(--surface-3)"></span>
        <span style="position:absolute;left:${at(r.median)}%;top:1px;width:2px;height:14px;background:var(--text)"></span>
      </span>
      <span class="num" style="font-size:var(--t-small)">${money(r.median)}
        <u style="text-decoration:none;color:var(--text-3);font-size:var(--t-micro)"> n=${r.n}</u></span>`;
    row.setAttribute('role', 'img');
    row.setAttribute('aria-label',
      `${year}: ${r.n} cars, ${money(r.low)} to ${money(r.high)}, ` +
      `middle half ${money(q1)} to ${money(q3)}, median ${money(r.median)}`);
    wrap.appendChild(row);
  }
  return wrap;
}

function csvOfListings() {
  const cols = ['id', 'year', 'make', 'model', 'trim', 'price', 'mileage_km',
                'per_1000km', 'distance_km', 'days_listed', 'location',
                'province', 'seller', 'status', 'filtered', 'filter_reason',
                'first_seen', 'last_seen', 'url'];
  const cell = v => {
    const text = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  };
  return [cols.join(',')].concat(
    (app.data.listings || []).map(l => cols.map(c => cell(l[c])).join(','))
  ).join('\n');
}

function download(name, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ----------------------------------------------------------------- searches */
function renderSearches() {
  const host = document.querySelector('[data-view="searches"]');
  host.innerHTML = '';
  const head = el('div', 'view__head');
  head.innerHTML = `<h1 id="searches-h">Searches</h1>
    <p>What the bot is looking at, and the rules applied to what it finds. Editing a
       rule shows what it would keep before you commit to it.</p>`;
  host.appendChild(head);

  for (const s of (app.data.searches || [])) {
    const sec = el('section', 'section');
    const h = s.health || {};
    const bad = h.consecutive_failures > 0;
    const shutOut = h.shut_out;
    sec.innerHTML = `<div class="section__head">
        <h2 class="name">${esc(s.name)}</h2>
        <span class="count num">${s.counts?.active ?? 0} live · ${s.counts?.filtered ?? 0} hidden</span>
      </div>`;

    const kv = el('dl', 'kv');
    const area = s.area?.text;
    kv.innerHTML =
      `<dt>Watching</dt><dd>${esc(searchWords(s))}
         · <a href="${esc(s.url)}" rel="noopener" target="_blank">open on autotrader.ca</a></dd>` +
      (area ? `<dt>Area</dt><dd>${esc(area)} <span class="note" style="margin:0">— enforced here, because the site ignores it</span></dd>` : '') +
      `<dt>Last read</dt><dd>${h.last_ok
          ? `${stamp(h.last_ok)} · ${h.last_count || 0} listing${(h.last_count || 0) === 1 ? '' : 's'}`
          : 'never — this search has not been read yet'}</dd>` +
      (bad ? `<dt>Trouble</dt><dd class="err">${h.consecutive_failures} failure${
        h.consecutive_failures === 1 ? '' : 's'} in a row — ${esc(h.last_error || '')}</dd>` : '') +
      (shutOut ? `<dt>Note</dt><dd class="warnt">Reads fine, keeps nothing: ${esc(shutOut)}</dd>` : '');
    sec.appendChild(kv);
    sec.appendChild(rulesEditor(s));
    host.appendChild(sec);
  }

  host.appendChild(pasteALink());
}

/* The search as a sentence, from the parts of the link the bot understood.
   A person who pasted that link wants to know it was read correctly, which a
   verbatim copy of their own URL does not tell them. */
function searchWords(s) {
  const m = s.summary || {};
  const bits = [[m.make, m.model].filter(Boolean).join(' ') || 'any car'];
  if (m.year_min && m.year_max) bits.push(`${m.year_min}–${m.year_max}`);
  else if (m.year_min) bits.push(`${m.year_min} or newer`);
  else if (m.year_max) bits.push(`up to ${m.year_max}`);
  else bits.push('any year');
  if (m.price_max) bits.push(`under ${money(m.price_max)}`);
  if (m.mileage_max) bits.push(`under ${km(m.mileage_max)} km`);
  for (const chip of (m.chips || [])) bits.push(chip);
  return bits.join(' · ');
}

function rulesEditor(s) {
  const box = el('div');
  box.style.marginTop = 'var(--s4)';
  const f = { ...(s.rules?.filters || {}) };
  const id = s.id.replace(/[^a-z0-9]/gi, '');
  const box_ = [
    ['mx', 'Max asking', f.max_price, 'no ceiling'],
    ['y0', 'From year', f.min_year, 'any'],
    ['y1', 'To year', f.max_year, 'any'],
    ['km', 'Within km', f.max_distance_km, 'anywhere'],
  ];
  box.innerHTML = `
    <div class="bar">${box_.map(([k, label, value, hint]) => `
      <label class="labelled"><span>${label}</span>
        <span class="field"><input type="number" inputmode="numeric" id="${k}-${id}"
          placeholder="${hint}" value="${value ?? ''}"></span></label>`).join('')}
    </div>
    <p class="why" id="pv-${id}"></p>`;
  const ask = el('div', 'bar');
  ask.style.marginTop = 'var(--s3)';
  box.appendChild(ask);

  const preview = () => {
    const v = k => {
      const n = box.querySelector('#' + k + '-' + id).value.trim();
      return n === '' ? null : Number(n);
    };
    const rule = { max_price: v('mx'), min_year: v('y0'), max_year: v('y1'), max_distance_km: v('km') };
    const pool = (app.data.listings || []).filter(l => l.status === 'active' && l.search_id === s.id);
    // A car held out by a rule this editor does not show stays held out.
    //
    // There are eleven rules and four boxes. This preview evaluated only its
    // own four and counted the rest as passing, so a search carrying an
    // excluded-seller rule could be told "9 of 9 would pass" over a list in
    // which the bot was hiding two. `filter_rule` is the run's own verdict,
    // named by the config key that produced it, so the seven rules off
    // screen come from the bot rather than being guessed at here.
    const elsewhere = l => l.filtered && !(l.filter_rule in rule);
    const held = pool.filter(elsewhere);
    const kept = pool.filter(l => {
      if (elsewhere(l)) return false;
      if (rule.max_price !== null && l.price !== null && l.price > rule.max_price) return false;
      if (rule.min_year !== null && l.year && l.year < rule.min_year) return false;
      if (rule.max_year !== null && l.year && l.year > rule.max_year) return false;
      if (rule.max_distance_km !== null && l.distance_km !== null &&
          l.distance_km !== undefined && l.distance_km > rule.max_distance_km) return false;
      return true;
    });
    const changed = JSON.stringify(rule) !== JSON.stringify({
      max_price: f.max_price ?? null, min_year: f.min_year ?? null,
      max_year: f.max_year ?? null, max_distance_km: f.max_distance_km ?? null });
    box.querySelector('#pv-' + id).innerHTML = pool.length
      ? `<b>${num(kept.length)}</b> of the ${num(pool.length)} cars this search currently holds would pass`
        + (changed ? ' under the rule above.' : ' under the rule as saved.')
        + (held.length ? ` ${plural(held.length, 'car')} ${held.length === 1 ? 'is' : 'are'} held`
            + ' out by a rule this editor does not show, whatever you set here.' : '')
      : 'This search is not holding any cars to test the rule against.';

    ask.innerHTML = '';
    if (!changed) return;
    const instructions = Object.entries(rule)
      .filter(([k, v]) => v !== (f[k] ?? null))
      .map(([k, v]) => ({ action: 'set-rule', search: s.id, rule: k, value: v }));
    if (!instructions.length) return;
    ask.appendChild(askButton(
      `Apply this to ${s.name}`,
      `Change the rules on ${s.name}`, instructions,
      `Rules for ${s.name}. On today's ${num(pool.length)} cars this would keep ${num(kept.length)}.`));
    ask.appendChild(el('span', 'note',
      'Opens a GitHub issue with the change in it. A workflow applies it, says '
      + 'what it did, and runs a check — usually inside a minute.'));
  };
  box.addEventListener('input', preview);
  preview();
  return box;
}

/* Paste a link and see what it would watch, before committing to it. The
   page cannot write to the repository - it is a static file on Pages - so it
   reads the link back and hands over the one command that does. */
function pasteALink() {
  const sec = el('section', 'section');
  sec.innerHTML = `<div class="section__head"><h2>Add a search</h2></div>
    <p class="note" style="margin-top:0">Set up the search you want on autotrader.ca, then
       paste the address of the results page here.</p>
    <div class="bar">
      <label class="field" style="flex:1 1 320px"><span class="sr">Search link</span>
        <input type="url" id="paste" placeholder="https://www.autotrader.ca/cars/…"
          spellcheck="false" autocomplete="off"></label>
    </div>
    <div id="paste-out"></div>`;

  const out = sec.querySelector('#paste-out');
  sec.querySelector('#paste').addEventListener('input', e => {
    const raw = e.target.value.trim();
    if (!raw) { out.innerHTML = ''; return; }
    let url;
    try { url = new URL(raw); } catch {
      out.innerHTML = `<p class="why err">That is not a web address yet — it should
        start with <span class="mono">https://</span>.</p>`;
      return;
    }
    if (!/autotrader\.ca$/i.test(url.hostname.replace(/^www\./, ''))) {
      out.innerHTML = `<p class="why err">That is a link to
        <b>${esc(url.hostname)}</b>, not autotrader.ca.</p>`;
      return;
    }
    const q = url.searchParams;
    const bits = [];
    const seg = url.pathname.split('/').filter(Boolean);
    if (seg[0] === 'cars' && seg[1]) bits.push(seg.slice(1, 3).join(' ').toUpperCase());
    const yr = q.get('yRng');
    if (yr) bits.push(yr.replace('%2C', ',').replace(',', '–'));
    const pr = q.get('pRng');
    if (pr) bits.push('price ' + pr.replace(',', '–'));
    if (q.get('loc')) bits.push('near ' + q.get('loc'));
    out.innerHTML = `
      <p class="why"><b>Reads as:</b> ${bits.length ? esc(bits.join(' · ')) : 'every car on that page'}.
        The bot re-reads the link itself on every check, so anything it did not
        understand here is still applied by the site.</p>
      <p class="note">To add it, run this where the bot lives:</p>
      <p class="mono" style="background:var(--surface);padding:var(--s3);border-radius:var(--r-sm);
         overflow-x:auto;white-space:nowrap;margin:0">python -m autotrader add "${esc(raw)}"</p>`;
  });
  return sec;
}

/* ----------------------------------------------------------------- status */
function renderStatus() {
  const host = document.querySelector('[data-view="status"]');
  host.innerHTML = '';
  const d = app.data;
  const run = d.last_run || {};
  const cov = d.coverage || {};
  const h = d.health || {};

  const head = el('div', 'view__head');
  head.innerHTML = `<h1 id="status-h">Status</h1>
    <p>Whether the thing watching is working. The useful measure is not whether the
       last check passed — it is what share of the checks it was meant to make it made.</p>`;
  host.appendChild(head);

  const covTone = cov.too_short ? '' 
    : cov.pct >= 80 ? 'good' : cov.pct >= 40 ? 'warn' : 'bad';
  const stats = el('dl', 'stats');
  stats.innerHTML = `
    <div class="stat" data-tone="${covTone}"><dt>Coverage, ${hours(cov.window_hours || 24)}</dt>
      <dd class="num">${cov.too_short ? '—' : `${cov.pct ?? '—'}%`}</dd>
      <dd class="stat__note">${cov.too_short
        ? `measuring for ${hours(cov.window_hours)} so far, since the schedule `
          + `changed to one check every ${every(cov.expected_interval_minutes)}. `
          + `${cov.successful ?? 0} check${cov.successful === 1 ? '' : 's'} in that time.`
        : `${cov.slots_covered ?? cov.successful ?? 0} of ${cov.expected ?? 0} ${slotWord(cov)}`
          + `${cov.partial ? ` in the ${hours(cov.window_hours)} since the schedule changed` : ''}`
          + `${cov.complained ? ` · ${cov.complained} of ${cov.successful} checks complained` : ''}`
        }</dd></div>
    <div class="stat"><dt>Last good check</dt><dd>${when(run.at)}</dd>
      <dd class="stat__note">${stamp(run.at)}</dd></div>
    <div class="stat" data-tone="${cov.longest_gap_minutes > 180 ? 'warn' : ''}"><dt>Longest gap</dt>
      <dd class="num">${cov.longest_gap_minutes ? Math.round(cov.longest_gap_minutes / 60 * 10) / 10 : '—'}h</dd>
      <dd class="stat__note">between good checks</dd></div>
    <div class="stat"><dt>Requests last check</dt><dd class="num">${run.requests_made ?? '—'}</dd>
      <dd class="stat__note">budget ${h.budget?.limit ?? '—'}</dd></div>
    <div class="stat"><dt>Check took</dt><dd class="num">${run.duration_s ?? '—'}s</dd>
      <dd class="stat__note">${d.cost
        ? `${d.cost.checks} checks · ${d.cost.billed_minutes ?? d.cost.minutes} billed minutes in ${d.cost.window_hours}h`
        : ''}</dd></div>
    ${d.budget ? `<div class="stat" data-tone="${
        d.budget.state === 'stop' ? 'bad' : d.budget.state === 'over' ? 'warn' : ''}">
      <dt>Runner minutes this month</dt>
      <dd class="num">${num(d.budget.used)}${
        d.budget.charged
          ? `<small style="display:inline"> / ${num(d.budget.allowance)}</small>`
          : ''}</dd>
      <dd class="stat__note">${esc(d.budget.text)}</dd></div>` : ''}
    <div class="stat" data-tone="${h.accounted?.unexplained ? 'bad' : 'good'}"><dt>Unaccounted cars</dt>
      <dd class="num">${h.accounted?.unexplained ?? 0}</dd>
      <dd class="stat__note">${h.accounted?.delivered ?? 0} told, ${h.accounted?.quiet ?? 0} deliberately quiet</dd></div>`;
  host.appendChild(stats);

  // When the checks happened, not just how many. A percentage cannot tell a
  // schedule that is thin everywhere from one that is absent for six hours
  // and then fine, and only the second one loses you a car.
  if ((cov.slots || []).length) {
    const sec = el('section', 'section measure');
    sec.innerHTML = `<div class="section__head"><h2>When it checked</h2>
      <span class="count num">${cov.slots_covered ?? 0}/${cov.expected ?? 0}</span></div>`;
    const strip = el('div', 'slots');
    strip.setAttribute('role', 'img');
    strip.setAttribute('aria-label',
      `${cov.slots_covered ?? 0} of ${cov.expected ?? 0} ${slotWord(cov)} in the `
      + `last ${hours(cov.window_hours ?? 24)} had a check. `
      + `Longest gap ${Math.round((cov.longest_gap_minutes || 0) / 6) / 10} hours.`);
    const begin = Date.parse(cov.since);
    const step = (cov.expected_interval_minutes || 30) * 60000;
    cov.slots.forEach((v, i) => {
      const cell = el('i', 'slot');
      cell.dataset.state = v === 0 ? 'miss' : v === 1 ? 'ok' : 'warn';
      const at = new Date(begin + i * step);
      cell.title = `${at.toLocaleString('en-CA', { weekday: 'short', hour: '2-digit',
        minute: '2-digit' })} — ` + (v === 0 ? 'no check'
          : v === 1 ? 'checked' : 'checked, reported a problem');
      strip.appendChild(cell);
    });
    sec.appendChild(strip);
    const ends = el('div', 'slots__ends');
    ends.innerHTML = `<span>${when(cov.since)}</span><span>now</span>`;
    sec.appendChild(ends);
    sec.appendChild(el('p', 'note',
      `Each mark is ${cov.expected_interval_minutes || 30} minutes. `
      + `Longest gap ${Math.round((cov.longest_gap_minutes || 0) / 6) / 10} hours.`));
    host.appendChild(sec);
  }

  if ((run.invariants || []).length) {
    const s = el('section', 'section');
    s.innerHTML = `<div class="section__head"><h2>Bookkeeping failures</h2></div>` +
      `<ul class="note" style="padding-left:var(--s4)">` +
      run.invariants.map(v => `<li class="err">${esc(v)}</li>`).join('') + `</ul>`;
    host.appendChild(s);
  }

  // Run history as one mark per check. A gap reads as a gap.
  const runs = (d.runs || []).slice().reverse();
  if (runs.length) {
    const s = el('section', 'section');
    s.innerHTML = `<div class="section__head"><h2>Recent checks</h2>
      <span class="count num">${runs.length} kept</span></div>`;
    const tl = el('div', 'timeline');
    tl.setAttribute('role', 'img');
    tl.setAttribute('aria-label',
      `${runs.filter(r => r.ok).length} of the last ${runs.length} checks succeeded`);
    let prev = null;
    for (const r of runs) {
      const t = Date.parse(r.at);
      if (prev && t - prev > (cov.expected_interval_minutes || 30) * 60000 * 2) {
        const g = el('i'); g.dataset.gap = '1'; tl.appendChild(g);
      }
      const i = el('i');
      i.dataset.ok = r.ok ? '1' : '0';
      i.style.height = Math.max(16, Math.min(100, (r.duration_s || 10) * 2)) + '%';
      i.title = `${stamp(r.at)} — ${r.ok ? 'ok' : 'failed'}, ${r.listings_seen ?? 0} listings, ${r.duration_s ?? '?'}s`;
      tl.appendChild(i);
      prev = t;
    }
    s.appendChild(tl);
    s.appendChild(el('p', 'note',
      `One mark per check, oldest first; height is how long it took. A dotted line is a gap longer than two intervals — a stretch with no check in it at all.`));
    host.appendChild(s);
  }

  // Parser ladder: which rungs scored, not just which one won.
  const strat = h.strategies || {};
  if (Object.keys(strat).length) {
    const s = el('section', 'section');
    s.innerHTML = `<div class="section__head"><h2>Parser ladder</h2></div>`;
    const ladderHtml = `<thead><tr><th>Search</th><th>Winner</th><th>Working</th><th class="r">Scores</th></tr></thead><tbody>` +
      Object.entries(strat).map(([, v]) => {
        const order = v.order || [];
        const lad = order.map(n => `<i data-on="${(v.working || []).includes(n) ? 1 : 0}" title="${esc(n)}"></i>`).join('');
        const scores = order.map(n => `${n} ${v.scores?.[n] ?? 0}`).join(' · ');
        return `<tr><td>${esc(v.name)}</td><td class="mono">${esc(v.winner || '—')}</td>
          <td><span class="ladder" role="img" aria-label="${(v.working || []).length} of ${v.of} strateg${v.of === 1 ? 'y' : 'ies'} working">${lad}</span></td>
          <td class="r mono" style="font-size:var(--t-micro)">${esc(scores)}</td></tr>`;
      }).join('') + `</tbody>`;
    s.appendChild(table(ladderHtml));
    if ((h.drift || []).length) {
      s.appendChild(el('p', 'note warnt', 'Shape drift: ' + h.drift.join('; ')));
    }
    host.appendChild(s);
  }

  // Where alerts go, and whether they arrived.
  const s2 = el('section', 'section');
  s2.innerHTML = `<div class="section__head"><h2>Alerts</h2></div>`;
  const rows = Object.entries(d.channel_health || {});
  const active = (d.notify?.active || []);
  const channelsHtml = `<thead><tr><th>Channel</th><th>State</th><th>Last good</th></tr></thead><tbody>` +
    (active.length ? active.map(name => {
      const ch = (d.channel_health || {})[name] || {};
      const fails = ch.consecutive_failures || 0;
      return `<tr><td>${esc(d.channels?.[name]?.label || name)}</td>
        <td class="${fails ? 'err' : 'ok'}">${fails ? `${fails} failure${fails === 1 ? '' : 's'} in a row` : 'delivering'}</td>
        <td class="num">${ch.last_ok ? when(ch.last_ok) : '—'}</td></tr>`;
    }).join('') : `<tr><td colspan="3">No channel is switched on, so nothing is being sent.</td></tr>`) +
    `</tbody>`;
  s2.appendChild(table(channelsHtml));
  if (d.notify?.ntfy_url) {
    const p = el('p', 'note');
    p.innerHTML = `Your feed: <a href="${esc(d.notify.ntfy_url)}" rel="noopener">${esc(d.notify.ntfy_url)}</a>`;
    s2.appendChild(p);
  }
  host.appendChild(s2);
  void rows;
}

/* -------------------------------------------------------------- the ask
   A static page cannot write to the repository, and a phone should not have
   to carry a token. An issue is a write channel both of them already have:
   this composes one, prefilled, and a workflow on the other side applies it
   whole or refuses it whole and says why. */
// Two ways a phone can write to a repository without a token or a terminal.
// Committing a file works everywhere; opening an issue only works where the
// Issues feature is switched on - and on this repository it is not, which is
// how the change button spent its first day linking to a 404.
function askUrl(title, instructions, prose) {
  const repo = app.data?.repo;
  if (!repo) return null;
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
  const slug = String(title).toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '').slice(0, 40) || 'change';

  if (app.data?.repo_issues) {
    const body = [
      prose || 'Opened from the dashboard.', '',
      '```autotrader',
      JSON.stringify(instructions, null, 1),
      '```',
    ].join('\n');
    return `https://github.com/${repo}/issues/new?title=${encodeURIComponent(title)}`
         + `&body=${encodeURIComponent(body)}`;
  }

  // GitHub's web editor takes the filename and the contents in the URL, so
  // this opens a page with the change already typed and one button to press.
  // The file is JSON only - the prose goes in the commit message, which the
  // same page also prefills.
  return `https://github.com/${repo}/new/main`
       + `?filename=${encodeURIComponent(`control/${stamp}-${slug}.json`)}`
       + `&value=${encodeURIComponent(JSON.stringify(instructions, null, 1))}`
       + `&message=${encodeURIComponent(title)}`
       + `&description=${encodeURIComponent(prose || 'Opened from the dashboard.')}`;
}

function askButton(label, title, instructions, prose) {
  const url = askUrl(title, instructions, prose);
  if (!url) {
    const dead = el('button', 'btn', label);
    dead.type = 'button';
    dead.disabled = true;
    dead.title = 'This page does not know which repository it belongs to.';
    return dead;
  }
  const a = el('a', 'btn', label);
  a.href = url; a.target = '_blank'; a.rel = 'noopener';
  a.style.cssText = 'display:inline-flex;align-items:center;text-decoration:none';
  return a;
}

/* Your marks on a car. Kept in the browser for instant feedback and in the
   repository for permanence - the browser copy is what makes a tap feel like
   a tap, the repository copy is what survives a new phone. */
const marks = {
  all() { return store.get('marks', {}); },
  of(id) {
    const local = this.all()[String(id)] || {};
    const remote = (byId(id) || {}).you || {};
    return { ...remote, ...local };
  },
  set(id, patch) {
    const all = this.all();
    all[String(id)] = { ...(all[String(id)] || {}), ...patch };
    for (const [k, v] of Object.entries(patch)) if (!v) delete all[String(id)][k];
    store.set('marks', all);
  },
};

/* ----------------------------------------------------------------- sheet */
let lastFocus = null;

function openSheet(id) {
  const l = byId(id);
  const sheet = document.getElementById('sheet');
  const scrim = document.getElementById('scrim');
  const body = document.getElementById('sheet-body');
  lastFocus = document.activeElement;

  if (!l) {
    body.innerHTML = '';
    body.appendChild(emptyState('That listing is not in the published data',
      'It may have been dropped when the file was trimmed, or the link may be from an older alert.'));
  } else {
    document.getElementById('sheet-title').textContent = carName(l);
    body.innerHTML = '';
    body.appendChild(sheetBody(l));
  }
  sheet.hidden = false; scrim.hidden = false;
  requestAnimationFrame(() => { sheet.dataset.open = '1'; scrim.dataset.open = '1'; });
  document.body.style.overflow = 'hidden';
  document.getElementById('sheet-close').focus();
  if (l) history.replaceState(null, '', `#/listing/${encodeURIComponent(l.id)}`);
}

function closeSheet() {
  const sheet = document.getElementById('sheet');
  const scrim = document.getElementById('scrim');
  sheet.dataset.open = '0'; scrim.dataset.open = '0';
  document.body.style.overflow = '';
  const done = () => { sheet.hidden = true; scrim.hidden = true; };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) done();
  else setTimeout(done, 180);
  history.replaceState(null, '', `#/${app.view}`);
  if (lastFocus && lastFocus.isConnected) lastFocus.focus();
}

function sheetBody(l) {
  const frag = document.createDocumentFragment();
  const move = priceMove(l);
  const cmp = app.data.comparables?.[String(l.id)];
  const says = comparableSays(cmp, l);

  const gal = el('div', 'gallery');
  // Only the photos we hold a copy of. This was [l.thumb, ...l.images], so
  // every slide after the first was hotlinked from the seller's CDN: broken
  // offline, broken in the installed app, gone when the car is delisted, and
  // a grey box in every screenshot ever taken of this page - which is how it
  // survived so long.
  const imgs = (l.thumbs && l.thumbs.length ? l.thumbs
                : [l.thumb].filter(Boolean)).slice(0, 8);
  const published = l.photo_count || 0;
  if (imgs.length) {
    for (const src of imgs) {
      const box = el('div', 'shotbox');
      const img = new Image();
      img.loading = 'lazy'; img.decoding = 'async'; img.alt = '';
      img.width = 400; img.height = 300; img.src = src;
      img.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:var(--r-sm)';
      img.addEventListener('error', () => {
        box.innerHTML = `<div class="shot__fallback">${CAR_GLYPH}<span>photo not loaded</span></div>`;
      }, { once: true });
      box.appendChild(img);
      gal.appendChild(box);
    }
    gal.setAttribute('role', 'group');
    gal.setAttribute('aria-label',
      `${imgs.length} photo${imgs.length === 1 ? '' : 's'}`);
    frag.appendChild(gal);
    if (published > imgs.length) {
      const note = el('p', 'note');
      note.style.margin = '0 0 var(--s4)';
      note.innerHTML = `${imgs.length} of ${published} photos kept here. `
        + `<a href="${esc(l.url || '#')}" rel="noopener" target="_blank">See them all on autotrader.ca</a>`;
      frag.appendChild(note);
    }
  } else {
    const box = el('div', 'shotbox');
    box.style.cssText = 'width:100%;aspect-ratio:4/3;border-radius:var(--r-sm);margin-bottom:var(--s4)';
    box.innerHTML = `<div class="shot__fallback">${CAR_GLYPH}<b>${esc(carName(l))}</b><span>${l.filtered ? 'photos not kept for hidden cars' : 'no photo published'}</span></div>`;
    frag.appendChild(box);
  }

  const price = el('div');
  price.style.marginBottom = 'var(--s4)';
  price.innerHTML =
    `<div style="display:flex;align-items:baseline;gap:var(--s3);flex-wrap:wrap">
      <span class="num" style="font-size:var(--t-display);font-weight:560;letter-spacing:-.02em">
        ${l.unpriced ? 'Call for price' : money(l.price)}</span>
      ${move ? `<span class="card__was num">${money(move.was)}</span>
        <span class="num ${move.delta < 0 ? 'drop' : 'rise'}" style="font-weight:520">${signed(move.delta)}</span>` : ''}
    </div>` +
    (says?.sentence ? `<p class="note note--cmp">${says.sentence}</p>` : '');
  frag.appendChild(price);

  // Why you are seeing this, or why you did not hear about it.
  const why = el('p', 'why');
  if (l.filtered) {
    why.innerHTML = `<b>You were not told about this.</b> It is hidden by a rule on
      ${esc(l.search_name || 'this search')}: ${esc(l.filter_reason || 'a rule of yours')}.
      It is kept and tracked so a change to it is never silently lost.`;
  } else if (l.notified_at) {
    why.innerHTML = `<b>You were told about this</b> at ${stamp(l.notified_at)}, via the
      channels switched on at the time.`;
  } else if (l.quiet_reason) {
    why.innerHTML = `<b>Deliberately quiet.</b> ${esc(l.quiet_reason)}.`;
  } else {
    why.innerHTML = `<b>No delivery record.</b> That is a fault rather than a decision, and
      the next check's bookkeeping test should fail on it.`;
  }
  frag.appendChild(why);

  const hist = (l.price_history || []).filter(p => p.price);
  if (hist.length >= 2) {
    const s = el('section', 'section');
    s.innerHTML = `<div class="section__head"><h2>Asking price</h2>
      <span class="count num">${hist.length} observation${hist.length === 1 ? '' : 's'}</span></div>`;
    s.appendChild(sparkline(hist));
    host_append(s, frag);
  }

  const spec = el('section', 'section');
  spec.innerHTML = `<div class="section__head"><h2>Specification</h2></div>`;
  const kv = el('dl', 'kv');
  const pairs = [
    ['Year', l.year], ['Odometer', l.mileage_km ? `${km(l.mileage_km)} km` : null],
    // The one row that explains its own blank, because the blank has three
    // possible causes and two of them are about the ratio rather than the car.
    [`Asking ${PER_KM}`, l.per_1000km ? money(l.per_1000km)
      : l.per_1000km_why ? `not shown — ${l.per_1000km_why}` : null],
    ['Distance', (l.distance_km ?? null) === null ? null
      : l.distance_km < 1 ? `in ${esc(l.distance_from || 'your area')}`
      : `${km(l.distance_km)} km from ${esc(l.distance_from || 'home')}`],
    ['On the market', l.days_listed === undefined ? null : daysListed(l.days_listed)], ['Colour', l.color], ['Body', l.body],
    ['Transmission', l.transmission], ['Drivetrain', l.drivetrain], ['Fuel', l.fuel],
    ['Location', [l.location, l.province].filter(Boolean).join(', ')],
    ['Seller', l.seller], ['Watched by', l.search_name],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '');
  kv.innerHTML = pairs.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  spec.appendChild(kv);
  const extras = carExtras(l);
  if (extras.length) {
    spec.appendChild(el('p', 'note', 'Dealer copy: ' + extras.join(' · ')));
  }
  host_append(spec, frag);

  const events = (app.data.events || []).filter(e => String(e.listing_id) === String(l.id));
  if (events.length) {
    const s = el('section', 'section');
    s.innerHTML = `<div class="section__head"><h2>Its life so far</h2>
      <span class="count num">${events.length}</span></div>`;
    const ul = el('ul', 'life');
    for (const e of events) {
      const li = el('li');
      let what = KIND[e.kind]?.rule || e.kind;
      if (e.kind === 'price_drop' || e.kind === 'price_rise') {
        what = `${money(e.old_price)} → ${money(e.new_price)} <span class="num ${e.kind === 'price_drop' ? 'drop' : 'rise'}">${signed(e.delta)}</span>`;
      }
      // delivery.text is prose built in Python, and for a delivered car it
      // ends in a raw ISO-8601 timestamp - "sent 2026-09-13T05:40:23+00:00" -
      // printed directly under the same instant rendered as "Sep 13, 05:40".
      // The state and the timestamp travel separately; the sentence is built
      // here, where the formatter lives.
      const told = e.delivery?.state === 'sent' && e.delivery.at
        ? `Told you ${stamp(e.delivery.at)}`
        : (e.delivery?.text || '');
      li.innerHTML = `<time datetime="${esc(e.at)}">${stamp(e.at)}</time>
        <span>${what}<br><span class="note" style="margin:0">${esc(told)}</span></span>`;
      ul.appendChild(li);
    }
    s.appendChild(ul);
    host_append(s, frag);
  }

  const yours = el('div', 'bar');
  yours.style.marginTop = 'var(--s6)';
  const mine = marks.of(l.id);
  const name = carName(l);
  for (const [key, on, off, action, undo] of [
    ['shortlisted', 'On your shortlist', 'Shortlist', 'shortlist', 'unshortlist'],
    ['muted', 'Muted', 'Mute this car', 'mute-listing', 'unmute-listing'],
    // 'undismiss', not 'unshortlist'. The undo used to send the wrong action
    // entirely: it cleared a mark the car did not have, left the dismissal in
    // place, and reported success.
    ['dismissed', 'Dismissed', 'Not interested', 'dismiss', 'undismiss'],
  ]) {
    const active = !!mine[key];
    const b = el('button', 'chip', active ? on : off);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(active));
    b.addEventListener('click', () => {
      marks.set(l.id, { [key]: !active });
      // Instant here, permanent there: the tap lands now and the issue makes
      // it survive a new phone.
      const url = askUrl(
        `${active ? undo : action}: ${name}`,
        [{ action: active ? undo : action, listing: String(l.id) }],
        `${active ? undo : action} for ${name} — opened from the dashboard.`);
      if (url) window.open(url, '_blank', 'noopener');
      openSheet(l.id);
    });
    yours.appendChild(b);
  }
  frag.appendChild(yours);

  // A note. Why you skipped it, what the seller said on the phone, which one
  // this is among four silver Competitions - the things that make a shortlist
  // usable a week later.
  const noteBox = el('div', 'note-edit');
  noteBox.style.marginTop = 'var(--s3)';
  const field = el('textarea', 'field');
  field.rows = 2;
  field.maxLength = 400;
  field.placeholder = 'A note to yourself about this car…';
  field.value = mine.note || '';
  field.id = `note-${l.id}`;
  const label = el('label', 'note-edit__label', mine.note ? 'Your note' : 'Add a note');
  label.setAttribute('for', field.id);
  const save = el('button', 'chip', 'Save note');
  save.type = 'button';
  save.disabled = true;
  field.addEventListener('input', () => {
    save.disabled = field.value.trim() === (mine.note || '').trim();
  });
  save.addEventListener('click', () => {
    const text = field.value.trim().slice(0, 400);
    marks.set(l.id, { note: text });
    const url = askUrl(
      text ? `Note on ${name}` : `Clear the note on ${name}`,
      [{ action: 'note', listing: String(l.id), text }],
      `A note on ${name} — opened from the dashboard.`);
    if (url) window.open(url, '_blank', 'noopener');
    openSheet(l.id);
  });
  noteBox.appendChild(label);
  noteBox.appendChild(field);
  const noteBar = el('div', 'bar');
  noteBar.style.marginTop = 'var(--s2)';
  noteBar.appendChild(save);
  noteBox.appendChild(noteBar);
  frag.appendChild(noteBox);

  const go = el('div', 'bar');
  go.style.marginTop = 'var(--s3)';
  const a = el('a', 'btn btn--primary', 'Open on autotrader.ca');
  a.href = l.url; a.rel = 'noopener'; a.target = '_blank';
  a.style.cssText = 'display:inline-flex;align-items:center;text-decoration:none';
  go.appendChild(a);
  frag.appendChild(go);
  return frag;
}
function host_append(section, frag) { frag.appendChild(section); }

/* A price history worth looking at needs no library: it is at most twenty
   points and the only question is which way it went. */
function sparkline(hist) {
  const w = 100, h = 44, pad = 3;
  const prices = hist.map(p => p.price);
  const min = Math.min(...prices), max = Math.max(...prices);
  const span = (max - min) || 1;
  const pts = prices.map((p, i) => {
    const x = pad + (i / Math.max(1, prices.length - 1)) * (w - pad * 2);
    const y = h - pad - ((p - min) / span) * (h - pad * 2);
    return [x, y];
  });
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const area = `${d} L${pts[pts.length - 1][0].toFixed(1)} ${h} L${pts[0][0].toFixed(1)} ${h} Z`;
  const svg = el('div');
  svg.innerHTML =
    `<svg class="hist" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
       aria-label="Asking price from ${money(prices[0])} to ${money(prices[prices.length - 1])} over ${prices.length} observation${prices.length === 1 ? '' : 's'}">
      <path class="area" d="${area}"/><path d="${d}"/>
      <circle cx="${pts[pts.length - 1][0].toFixed(1)}" cy="${pts[pts.length - 1][1].toFixed(1)}" r="1.8"/>
    </svg>
    <div class="facts" style="justify-content:space-between;margin-top:var(--s2)">
      <span class="num">${money(min)} low</span><span class="num">${money(max)} high</span>
    </div>`;
  return svg;
}

/* ----------------------------------------------------------------- boot */
function skeleton() {
  const remembered = store.get('lastTrust', null);
  if (remembered) {
    document.getElementById('trust').dataset.state = remembered.state;
    document.getElementById('trust-text').textContent = remembered.text;
    if (remembered.alarm) {
      const alarm = document.getElementById('alarm');
      alarm.hidden = false;
      alarm.dataset.level = remembered.alarm.level;
      document.getElementById('alarm-text').textContent = remembered.alarm.text;
      document.getElementById('alarm-detail').textContent = remembered.alarm.detail || '';
    }
  }
  const host = document.querySelector('[data-view="feed"]');
  host.hidden = false;
  host.innerHTML = `<div class="view__head measure"><h1 id="feed-h">What changed</h1>
    <p>Everything that has happened to a car you are watching, newest first — including
       cars your rules hide, which the run counters never counted.</p></div>`;
  const sec = el('section', 'section measure');
  sec.innerHTML = `<div class="section__head"><h2>&nbsp;</h2></div>`;
  const list = el('ul', 'feed');
  for (let i = 0; i < 8; i++) {
    const li = el('li');
    li.innerHTML = `<div class="ev skel-row">
        <span class="skel__line" style="width:52px;height:9px;margin:0"></span>
        <span class="skel__line" style="width:${40 + (i % 4) * 12}%;height:12px;margin:0"></span>
        <span class="skel__line" style="width:64px;height:12px;margin:0"></span>
      </div>`;
    list.appendChild(li);
  }
  sec.appendChild(list);
  host.appendChild(sec);
}

function render() {
  if (!app.data) return;
  eagerSlots = 6;
  renderTrust();
  if (app.view === 'feed') renderFeed();
  else if (app.view === 'listings') renderListings();
  else if (app.view === 'market') renderMarket();
  else if (app.view === 'searches') renderSearches();
  else if (app.view === 'status') renderStatus();
}

function route() {
  const h = location.hash.replace(/^#\/?/, '');
  const [what, arg] = h.split('/');
  if (what === 'listing' && arg) {
    const view = app.view || 'feed';
    go(view, { silent: true, focus: false });
    openSheet(decodeURIComponent(arg));
    return;
  }
  if (VIEWS.some(v => v.id === what)) go(what, { silent: true, focus: false });
  else go('feed', { silent: true, focus: false });
}

async function boot() {
  renderTabs();
  skeleton();
  try {
    const res = await fetch('data.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error(`data.json responded ${res.status}`);
    // With a worker installed, an offline load still succeeds - the worker
    // answers it from cache with a 200. Without this header the page had no
    // way to tell that apart from a live fetch, and cheerfully drew a
    // three-day-old market as today's with no mention of the network at all.
    if (res.headers.get('X-From-Cache') === '1') app.offline = true;
    app.data = await res.json();
  } catch (err) {
    // Offline, or the file is not there yet. Say which, and what to do.
    try {
      const cached = await caches.match('data.json');
      if (cached) { app.data = await cached.json(); app.offline = true; }
    } catch { /* no cache API */ }
    if (!app.data) {
      app.loadError = err.message;
      document.querySelector('[data-view="feed"]').innerHTML = '';
      const s = emptyState('Could not load the data',
        `The page fetches data.json from alongside itself and got: ${err.message}. If this is a fresh install, nothing has been published yet — run a check and it will appear.`);
      document.querySelector('[data-view="feed"]').appendChild(s);
      renderTabs();
      return;
    }
  }

  if (app.lastSeen === null) {
    app.lastSeen = new Date().toISOString();
    store.set('lastSeen', app.lastSeen);
    app.firstVisit = true;
  }

  // Which ids are new to this browser, so a card animates once on arrival
  // rather than every time the list re-renders.
  const ids = (app.data.listings || []).map(l => String(l.id));
  for (const id of ids) if (!app.seenIds.has(id)) app.freshIds.add(id);
  app.seenIds = new Set(ids);
  store.set('seenIds', ids.slice(0, 1200));

  // A newer worker has taken over, which means a newer page is cached and
  // will run on the next load. Offered rather than applied: swapping the code
  // under someone mid-tap is worse than a banner they can ignore.
  //
  // Guarded on there having been a controller to begin with - on a first ever
  // visit the worker claims the page as it installs, and announcing "a newer
  // version is ready" to someone who has been here for four seconds is noise.
  if ('serviceWorker' in navigator) {
    const hadOne = !!navigator.serviceWorker.controller;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (hadOne) newVersionReady();
    });
  }

  renderTabs();
  route();
  window.addEventListener('hashchange', route);
  document.getElementById('sheet-close').addEventListener('click', closeSheet);
  document.getElementById('scrim').addEventListener('click', closeSheet);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.getElementById('sheet').dataset.open === '1') closeSheet();
    if (e.key === 'Tab' && document.getElementById('sheet').dataset.open === '1') {
      const f = document.getElementById('sheet').querySelectorAll(
        'a[href],button,input,select,[tabindex]:not([tabindex="-1"])');
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  // Push the sheet back down with a thumb. Only downward, only from the top
  // of its own scroll, so it never fights the content inside it.
  const sheet = document.getElementById('sheet');
  let startY = null;
  sheet.addEventListener('touchstart', e => {
    startY = sheet.scrollTop <= 0 ? e.touches[0].clientY : null;
  }, { passive: true });
  sheet.addEventListener('touchmove', e => {
    if (startY === null) return;
    const dy = e.touches[0].clientY - startY;
    if (dy > 0 && innerWidth < 700) sheet.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  sheet.addEventListener('touchend', e => {
    if (startY === null) return;
    const dy = (e.changedTouches[0].clientY - startY);
    sheet.style.transform = '';
    startY = null;
    if (dy > 110 && innerWidth < 700) closeSheet();
  });

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* file:// or no https */ });
  }
}

boot();
