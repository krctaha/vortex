/* VORTEX push kurulumu.
 *
 * iOS'ta bildirim izni yalnizca KULLANICI HAREKETI icinde istenebilir ve
 * yalnizca ana ekrana eklenmis PWA'da calisir. Bu yuzden otomatik degil,
 * Ayarlar'daki dugmeye basildiginda calisir.
 */
(function () {
  'use strict';

  const API = {
    status: '/api/push/status',
    subscribe: '/api/push/subscribe',
    unsubscribe: '/api/push/unsubscribe',
    test: '/api/push/test',
  };

  function b64ToUint8(base64) {
    const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4))
      .replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(padded);
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  const isStandalone = () =>
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  const isIOS = () =>
    /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

  /** Ortamin push destekleyip desteklemedigini, desteklemiyorsa NEDENINI dondurur. */
  function capability() {
    if (!('serviceWorker' in navigator)) return { ok: false, reason: 'Tarayıcı servis çalışanı desteklemiyor.' };
    if (!window.isSecureContext) return { ok: false, reason: 'HTTPS gerekiyor. Şu an güvenli bağlantı yok.' };
    if (!('PushManager' in window)) return { ok: false, reason: 'Tarayıcı push desteklemiyor.' };
    if (isIOS() && !isStandalone()) {
      return { ok: false, ios: true, reason:
        'iPhone\'da bildirim için uygulamayı ana ekrana eklemen gerekiyor: Safari\'de Paylaş → Ana Ekrana Ekle. Sonra uygulamayı ana ekrandan aç ve buraya tekrar gel.' };
    }
    return { ok: true };
  }

  async function register() {
    return navigator.serviceWorker.register('/sw.js', { scope: '/' });
  }

  async function currentSubscription() {
    if (!('serviceWorker' in navigator)) return null;
    const reg = await navigator.serviceWorker.getRegistration('/');
    if (!reg) return null;
    return reg.pushManager.getSubscription();
  }

  async function enable() {
    const cap = capability();
    if (!cap.ok) return { ok: false, error: cap.reason, ios: cap.ios };

    const info = await fetch(API.status).then((r) => r.json());
    if (!info.available || !info.public_key) {
      return { ok: false, error: 'Sunucu tarafı push hazır değil: ' + (info.reason || 'VAPID anahtarı yok') };
    }

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      return { ok: false, error: 'Bildirim izni verilmedi.' };
    }

    const reg = await register();
    await navigator.serviceWorker.ready;

    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64ToUint8(info.public_key),
      });
    }

    const res = await fetch(API.subscribe, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: sub.toJSON() }),
    }).then((r) => r.json());

    return res.ok ? { ok: true, subscriptions: res.subscriptions }
                  : { ok: false, error: 'Abonelik sunucuya kaydedilemedi.' };
  }

  async function disable() {
    const sub = await currentSubscription();
    if (!sub) return { ok: true };
    await fetch(API.unsubscribe, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint }),
    });
    await sub.unsubscribe();
    return { ok: true };
  }

  async function test() {
    return fetch(API.test, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }).then((r) => r.json());
  }

  async function state() {
    const cap = capability();
    const sub = cap.ok ? await currentSubscription() : null;
    return {
      supported: cap.ok,
      reason: cap.reason || '',
      ios: !!cap.ios,
      standalone: isStandalone(),
      subscribed: !!sub,
      permission: (window.Notification && Notification.permission) || 'default',
    };
  }

  window.VortexPush = { enable, disable, test, state, capability, isStandalone, isIOS };

  // Servis calisanini erkenden kaydet (izin istemeden) — boylece kullanici
  // dugmeye bastiginda hazir olur ve iOS'ta izin istegi gecikmez.
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => register().catch(() => {}));
  }
})();
