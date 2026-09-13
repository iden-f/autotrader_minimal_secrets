/* Offline from the last published check, and able to notice a newer build.
 *
 * BUILD is rewritten on every publish with a hash of the files below. That is
 * the whole update mechanism, and it is deliberately the boring one: a browser
 * re-fetches sw.js on navigation, installs a new worker when its bytes differ,
 * and a new worker re-fetches everything it caches. Nothing here depends on
 * noticing a change by hand.
 *
 * The version this replaced tried to be clever - serve the cached shell, fetch
 * it again behind the response, compare the text, update the cache if it
 * differed. Measured against a real browser with a real file change, it picked
 * up nothing: not on the next load, not on the one after. An installed app
 * would have run whatever app.js it first saw forever, through every deploy.
 *
 * Two rules the page depends on:
 *
 *   1. Data is never served from cache while the network can answer. A car
 *      that sold yesterday shown as available today is worse than no page at
 *      all, so data.json is network-first and a cache hit carries a header
 *      saying so, which is how the page knows to say "Offline" rather than
 *      drawing a three-day-old market as today's.
 *
 *   2. The shell is cache-first, because correctness there comes from BUILD
 *      rather than from revalidation.
 */
const BUILD = '146b152e7bba';
const SHELL = `atw-shell-${BUILD}`;
const DATA = `atw-data-${BUILD}`;
const FILES = ['./', './index.html', './app.js', './manifest.webmanifest',
               './icon.svg'];

/* cache: 'reload' so the browser's own HTTP cache cannot hand a new worker the
   bytes the old one was using. Without it the install is not an update. */
const fresh = path => new Request(path, { cache: 'reload' });

self.addEventListener('install', event => {
  event.waitUntil(Promise.all([
    caches.open(SHELL).then(cache => cache.addAll(FILES.map(fresh))),
    // The data too, into its own cache.
    //
    // The page's first fetch of data.json happens before this worker controls
    // the page, so it never passes through the fetch handler below - and a
    // phone that installed the app and then lost signal got "Could not load
    // the data" rather than the copy it was promised. Best effort: a failure
    // here must not fail the install, or a first visit during an outage
    // leaves the app with no worker at all.
    caches.open(DATA)
      .then(cache => cache.addAll([fresh('./data.json')]))
      .catch(() => null),
  ]).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(k => k !== SHELL && k !== DATA)
        .map(k => caches.delete(k))))
      .then(() => self.clients.claim()));
});

/* Network first. A cache hit is marked, because a page that cannot tell the
   difference will draw a three-day-old market as today's. */
async function data(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(DATA);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const hit = await caches.match(request);
    if (hit) {
      const headers = new Headers(hit.headers);
      headers.set('X-From-Cache', '1');
      return new Response(await hit.blob(), {
        status: hit.status, statusText: hit.statusText, headers,
      });
    }
    // Never resolve respondWith with undefined - that fails the request with
    // a TypeError the page cannot tell apart from a parse error.
    return new Response(JSON.stringify({ offline: true }), {
      status: 503,
      headers: { 'Content-Type': 'application/json', 'X-From-Cache': '0' },
    });
  }
}

async function shell(request) {
  const hit = await caches.match(request, { cacheName: SHELL });
  if (hit) return hit;
  try {
    return await fetch(request);
  } catch (err) {
    return new Response(
      'This page is not available offline yet. Open it once with a connection.',
      { status: 503, headers: { 'Content-Type': 'text/plain' } });
  }
}

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.endsWith('data.json') || url.pathname.endsWith('events.json')) {
    event.respondWith(data(event.request));
    return;
  }
  event.respondWith(shell(event.request));
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'skip-waiting') self.skipWaiting();
});
