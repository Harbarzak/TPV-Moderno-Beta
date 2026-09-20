/**
 * Service worker mínimo de la PWA móvil (fase 13).
 *
 * Regla de oro (§4: el WS/REST acelera, nunca bloquea): la API JAMÁS se
 * cachea — toda petición /api/ va a la red y si el servidor no está, el
 * cliente muestra su error normal (los datos de negocio nunca mienten).
 * Solo se cachea la shell (assets estáticos) para que la app arranque sin
 * dar error de red mientras el servidor responde, y navegaciones caen a la
 * shell cacheada si no hay LAN en ese instante.
 */

const CACHE = 'tpv-mobile-v1';
// Rutas relativas a la ubicación del SW: el servidor puede publicar la PWA
// bajo /app/movil (fase Instalador) o en la raíz en desarrollo; la API sigue
// filtrándose por /api/ (absoluta, vive en la raíz del servidor).
const SHELL = ['./', './index.html', './manifest.webmanifest', './icons/icon-192.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return; // escrituras: siempre red, sin caché

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return; // la API nunca se sirve de caché

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put('./index.html', copy));
          return response;
        })
        .catch(() => caches.match('./index.html')),
    );
    return;
  }

  // Assets: stale-while-revalidate.
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    }),
  );
});
