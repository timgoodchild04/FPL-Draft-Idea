// Service worker: makes Branksbowl installable and load instantly offline.
// The app *shell* (page, CSS, JS, icons) is cached; API calls always go to the
// network so league data is never served stale.
const CACHE = "branksbowl-v1";
const SHELL = [
  "/",
  "/static/styles.css",
  "/static/app.js",
  "/static/favicon-192.png",
  "/static/favicon-512.png",
  "/manifest.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;         // never touch POST/DELETE
  if (url.origin !== location.origin) return;      // only our own origin
  if (url.pathname.startsWith("/api/")) return;    // data: always live from network

  // App shell / static: serve from cache instantly, refresh in the background.
  e.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(e.request).then((cached) => {
        const network = fetch(e.request)
          .then((res) => {
            if (res && res.status === 200) cache.put(e.request, res.clone());
            return res;
          })
          .catch(() => cached);
        return cached || network;
      })
    )
  );
});
