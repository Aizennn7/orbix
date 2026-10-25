/**
 * Orbix Service Worker - Offline-first PWA for mobile & laptop
 * Caches app shell and provides offline fallback with local SQLite/IndexedDB
 */
const CACHE_NAME = 'orbix-v0.4.0';
const APP_SHELL = [
  '/',
  '/static/styles.css',
  '/static/mobile.css',
  '/static/laptop.css',
  '/static/app.js',
  '/static/app-mobile.js',
  '/static/manifest.json',
  '/login',
  '/static/login.css'
];

// Install - cache app shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(APP_SHELL.map(url => new Request(url, {cache: 'reload'}))).catch(err => {
        console.warn('[SW] App shell cache failed, continuing:', err);
      });
    })
  );
  self.skipWaiting();
});

// Activate - clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      );
    })
  );
  self.clients.claim();
});

// Fetch strategy: Network-first for API, Cache-first for static
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  
  // API calls - network first, fallback to cache if offline
  if (url.pathname.startsWith('/api/') || url.pathname === '/healthz') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // Cache successful API responses for offline
          if (response.ok && event.request.method === 'GET') {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => {
          // Offline fallback - try cache
          return caches.match(event.request).then(cached => {
            if (cached) return cached;
            // Return offline JSON for API
            return new Response(JSON.stringify({
              status: 'offline',
              message: 'Device offline - using local database',
              offline: true
            }), {
              headers: {'Content-Type': 'application/json'},
              status: 200
            });
          });
        })
    );
    return;
  }

  // Static assets - cache first
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => {
        // Offline fallback page
        if (event.request.headers.get('accept')?.includes('text/html')) {
          return caches.match('/');
        }
      });
    })
  );
});

// Background sync for signals when back online
self.addEventListener('sync', event => {
  if (event.tag === 'orbix-sync-candles') {
    event.waitUntil(syncCandles());
  }
});

async function syncCandles() {
  // Will sync local SQLite/IndexedDB with server when online
  const clients = await self.clients.matchAll();
  clients.forEach(client => {
    client.postMessage({type: 'SYNC_REQUIRED'});
  });
}

// Push notifications for signals (Web Push)
self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'Orbix Signal';
  const options = {
    body: data.body || `${data.side || 'NO SIGNAL'} ${data.pair || 'EUR/USD'}`,
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    tag: 'orbix-signal',
    data: data,
    vibrate: [200, 100, 200]
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow('/#dashboard')
  );
});
