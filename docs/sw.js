/* Offline from the last published check.
 *
 * The shell is cached on install so the app opens with no network at all.
 * data.json is network-first: stale cars shown as fresh would be worse than
 * no cars, so the network gets first refusal and the cache is the fallback -
 * and app.js says plainly when it fell back.
 */
const SHELL = 'atw-shell-v1';
const DATA = 'atw-data-v1';
const FILES = ['./', './index.html', './app.js', './manifest.webmanifest', './icon.svg'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  if (url.pathname.endsWith('data.json') || url.pathname.endsWith('events.json')) {
    e.respondWith(
      fetch(e.request).then(res => {
        const copy = res.clone();
        caches.open(DATA).then(c => c.put(e.request, copy));
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
});
