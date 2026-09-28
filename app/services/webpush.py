"""Web Push — sinyalleri dogrudan telefona bildirim olarak gonderir.

NEDEN: Telegram grubu haber ozeti, RSI raporu ve analizlerle dolup tasiyor;
kullanici kritik sinyali gurultunun icinde kaciriyor. Push ayri bir kanal:
yalnizca gercekten bakilmasi gereken sey buradan gelir.

iOS NOTU: iPhone'da Web Push YALNIZCA Safari'den "Ana Ekrana Ekle" ile
yuklenmis PWA'larda calisir (iOS 16.4+). Tarayici sekmesinde acikken
calismaz. HTTPS zorunludur — servis calisani guvenli baglam ister.

ANAHTARLAR: VAPID cifti ilk kullanimda uretilip app_settings'e yazilir.
Anahtar degisirse mevcut TUM abonelikler gecersiz olur, bu yuzden bir kez
uretilir ve bir daha dokunulmaz.

SIFRELEME: pywebpush yerine webpush_crypto (kendi RFC 8291 uygulamamiz).
Sebebi orada anlatiliyor — kisaca pywebpush'un bagimliligi http-ece guncel
setuptools ile kurulamiyor.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Dict, List, Optional

from .. import db
from ..config import settings

log = logging.getLogger("vortex.push")

try:
    import httpx

    from . import webpush_crypto as crypto
    AVAILABLE = True
    UNAVAILABLE_REASON = ""
except Exception as exc:  # noqa: BLE001
    AVAILABLE = False
    UNAVAILABLE_REASON = f"{type(exc).__name__}: {exc}"

VAPID_SETTING = "vapid_keys"
DEFAULT_TTL = 3600


# --------------------------------------------------------------------------- #
# VAPID anahtarlari
# --------------------------------------------------------------------------- #
def _generate_keys() -> Dict[str, str]:
    return crypto.generate_vapid_keys()


def keys() -> Optional[Dict[str, str]]:
    """VAPID ciftini dondurur; yoksa uretip kalici olarak saklar."""
    if not AVAILABLE:
        return None
    stored = db.get_setting(VAPID_SETTING)
    if isinstance(stored, dict) and stored.get("private") and stored.get("public"):
        return stored
    generated = _generate_keys()
    db.set_setting(VAPID_SETTING, generated)
    log.info("VAPID anahtar cifti uretildi ve saklandi")
    return generated


def public_key() -> Optional[str]:
    k = keys()
    return k["public"] if k else None


def status() -> Dict[str, Any]:
    row = db.query_one("SELECT COUNT(*) c FROM push_subscriptions")
    return {
        "available": AVAILABLE,
        "reason": UNAVAILABLE_REASON,
        "public_key": public_key(),
        "subscriptions": int(row["c"]) if row else 0,
    }


# --------------------------------------------------------------------------- #
# Abonelikler
# --------------------------------------------------------------------------- #
def save_subscription(user_id: Optional[int], sub: Dict[str, Any],
                      user_agent: str = "") -> bool:
    endpoint = sub.get("endpoint")
    keys_obj = sub.get("keys") or {}
    p256dh, auth = keys_obj.get("p256dh"), keys_obj.get("auth")
    if not (endpoint and p256dh and auth):
        return False
    db.execute(
        "INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,user_agent,created_at) "
        "VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, "
        "user_id=excluded.user_id, fail_count=0",
        (user_id, endpoint, p256dh, auth, user_agent[:300], db.now_ms()))
    return True


def remove_subscription(endpoint: str) -> None:
    db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def subscriptions(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    if user_id is None:
        return db.query("SELECT * FROM push_subscriptions")
    return db.query("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,))


# --------------------------------------------------------------------------- #
# Gonderim
# --------------------------------------------------------------------------- #
async def _send_one(client: "httpx.AsyncClient", sub: Dict[str, Any],
                    payload: bytes, vapid: Dict[str, str],
                    ttl: int, urgency: str) -> Optional[str]:
    """Tek abonelige gonderir. Donen deger: hata sebebi veya None (basarili)."""
    try:
        body = crypto.encrypt(payload, sub["p256dh"], sub["auth"])
        auth = crypto.vapid_header(sub["endpoint"], vapid["private"],
                                   vapid["public"], f"mailto:{settings.push_contact}")
        resp = await client.post(
            sub["endpoint"], content=body,
            headers={
                "Authorization": auth,
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": str(ttl),
                "Urgency": urgency,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:120]}"

    if resp.status_code in (200, 201, 202, 204):
        return None
    # 404/410 = abonelik artik gecersiz (uygulama silinmis, izin geri alinmis).
    # Temizlenmezse her gonderimde bosa deneme yapariz.
    if resp.status_code in (404, 410):
        remove_subscription(sub["endpoint"])
        return "expired"
    return f"HTTP {resp.status_code}: {resp.text[:120]}"


async def send(title: str, body: str, *, url: str = "/", tag: str = "vortex",
               user_id: Optional[int] = None, urgency: str = "high",
               ttl: int = DEFAULT_TTL, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Tum (veya bir kullanicinin) aboneliklerine bildirim gonderir."""
    if not AVAILABLE:
        return {"ok": False, "error": f"push katmani yuklenemedi ({UNAVAILABLE_REASON})", "sent": 0}
    vapid = keys()
    if not vapid:
        return {"ok": False, "error": "VAPID anahtari uretilemedi", "sent": 0}
    subs = subscriptions(user_id)
    if not subs:
        return {"ok": False, "error": "kayitli abonelik yok", "sent": 0}

    payload = json.dumps({"title": title, "body": body, "url": url,
                          "tag": tag, "data": data or {}},
                         ensure_ascii=False).encode("utf-8")

    async with httpx.AsyncClient(timeout=15.0) as client:
        results = await asyncio.gather(*[
            _send_one(client, dict(s), payload, vapid, ttl, urgency) for s in subs
        ], return_exceptions=True)
    results = [r if not isinstance(r, Exception) else str(r) for r in results]

    sent = sum(1 for r in results if r is None)
    errors = [str(r) for r in results if r is not None]
    now = db.now_ms()
    for sub, res in zip(subs, results):
        if res is None:
            db.execute("UPDATE push_subscriptions SET last_ok=?, fail_count=0 WHERE endpoint=?",
                       (now, sub["endpoint"]))
        elif res != "expired":
            db.execute("UPDATE push_subscriptions SET fail_count=fail_count+1 WHERE endpoint=?",
                       (sub["endpoint"],))
    if errors:
        log.warning("Push gonderim hatalari: %s", errors[:3])
    return {"ok": sent > 0, "sent": sent, "total": len(subs), "errors": errors[:5]}
