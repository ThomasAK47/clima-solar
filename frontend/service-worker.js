/**
 * Clima Solar — Service Worker
 * Estratégia: cache-first para assets estáticos, network-first para API.
 * Modo offline: exibe os últimos dados recebidos com banner de aviso.
 */

const CACHE_NAME  = 'clima-solar-v1';
const BASE        = new URL('./', self.location.href).href;

const STATIC_ASSETS = [
  BASE,
  BASE + 'index.html',
  BASE + 'manifest.json',
  BASE + 'icons/icon-192.png',
  BASE + 'icons/icon-512.png',
];

// ── Install: pré-cache dos assets estáticos ───────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: remove caches antigos ──────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const url = event.request.url;

  // Requisições à API Railway — network-first, sem cache (dados sempre frescos)
  if (url.includes('railway.app') || url.includes('localhost')) {
    event.respondWith(
      fetch(event.request)
        .catch(() => new Response(
          JSON.stringify({ _offline: true }),
          { headers: { 'Content-Type': 'application/json' } }
        ))
    );
    return;
  }

  // Assets estáticos — cache-first
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
        }
        return response;
      });
    })
  );
});
