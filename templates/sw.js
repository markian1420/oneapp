/*
 * Service worker: make the app usable on one bar of signal.
 *
 * Lives in templates/ rather than static/ and is served at the site root by
 * config.urls. A worker fetched from /static/ can only control /static/, so it
 * would never see a page navigation - which is the only thing worth caching.
 *
 * The whole point of this app is being opened at a petrol station or in a wet
 * market, which is exactly where a connection is worst. Three strategies,
 * chosen by what the request is for:
 *
 *   - App shell and vendored libraries: cache first. They change only on
 *     deploy, and waiting on the network for a stylesheet you already have is
 *     the difference between instant and unusable.
 *   - Pages: network first, falling back to the last copy seen. A stale page
 *     is far better than a dinosaur, and prices are labelled with their date
 *     everywhere, so an old page cannot silently look current.
 *   - Map tiles and the places API: network only. A stale tile is confusing
 *     and a cached JSON viewport would show pins that are not there.
 */

const VERSION = 'oneapp-v1';
const SHELL = `${VERSION}-shell`;
const PAGES = `${VERSION}-pages`;

// Everything needed to render something useful with no network at all.
const SHELL_ASSETS = [
  '/static/dist/app.css',
  '/static/vendor/leaflet/leaflet.css',
  '/static/vendor/leaflet/leaflet.js',
  '/static/vendor/htmx/htmx.min.js',
  '/static/img/icon.svg',
  '/static/manifest.webmanifest',
  '/offline/',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL)
      // addAll rejects the whole install if any single file 404s, which would
      // leave no worker at all. Failures are tolerated individually so a
      // renamed asset degrades one resource rather than the entire cache.
      .then((cache) => Promise.allSettled(
        SHELL_ASSETS.map((url) => cache.add(url))
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => !key.startsWith(VERSION))
            .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

function isShellAsset(url) {
  return url.pathname.startsWith('/static/');
}

function isTileOrLiveData(url) {
  return (
    url.hostname.endsWith('tile.openstreetmap.org')
    || url.pathname.endsWith('.json')
    || url.pathname.endsWith('places.json')
  );
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Anything that changes server state must never be served from a cache, and
  // must never be replayed from one either.
  if (request.method !== 'GET') return;

  // Never cache the admin or the sign-in flow: a cached authenticated page
  // served after sign-out would be a real leak.
  if (url.pathname.startsWith('/admin') || url.pathname.startsWith('/login')) {
    return;
  }

  if (isTileOrLiveData(url)) return;   // straight to the network

  if (isShellAsset(url)) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        const copy = response.clone();
        caches.open(SHELL).then((cache) => cache.put(request, copy));
        return response;
      }))
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(PAGES).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request)
          .then((hit) => hit || caches.match('/offline/')))
    );
  }
});
