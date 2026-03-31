const CACHE = 'teleglas-v1';
const STATIC = [
  '/dashboard/',
  '/dashboard/static/manifest.json',
  '/dashboard/static/icon-192.png',
  '/dashboard/static/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Network first for API/WS, cache fallback for static
  if (e.request.url.includes('/ws') || e.request.url.includes('tradingview')) {
    return;
  }
  e.respondWith(
    fetch(e.request).then(r => {
      const clone = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, clone));
      return r;
    }).catch(() => caches.match(e.request))
  );
});
