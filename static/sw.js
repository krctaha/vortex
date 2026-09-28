/* VORTEX servis calisani.
 *
 * Iki isi var:
 *   1. Push bildirimlerini alip gostermek
 *   2. Bildirime dokununca uygulamayi acmak / one getirmek
 *
 * Kasitli olarak ONBELLEKLEME YAPMIYOR. Bu bir fiyat terminali; bayat veri
 * gostermek hicbir veri gostermemekten daha tehlikeli. Cevrimdisi calisma
 * istemiyoruz, sadece bildirim istiyoruz.
 */
const VERSION = 'vortex-sw-1';

self.addEventListener('install', (event) => {
  self.skipWaiting();                  // yeni surum hemen devralsin
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    payload = { title: 'VORTEX', body: event.data ? event.data.text() : '' };
  }

  const title = payload.title || 'VORTEX';
  const options = {
    body: payload.body || '',
    icon: '/static/img/icon-192.png',
    badge: '/static/img/icon-192.png',
    tag: payload.tag || 'vortex',
    renotify: true,
    data: { url: payload.url || '/', ...(payload.data || {}) },
    vibrate: [80, 40, 80],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      // Uygulama zaten aciksa yeni sekme acma, mevcut olani one getir.
      for (const client of list) {
        if ('focus' in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
      return undefined;
    })
  );
});
