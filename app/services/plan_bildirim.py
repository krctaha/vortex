"""PLAN BILDIRIMI — tarama yeni kurulum buldukca telefona push.

NEDEN VAR
---------
Kullanicinin sikayeti "sinyal bulamiyorum" degil, "GIRISLERI KACIRIYORUM"
idi. Kurulum ekrana bakmadigi saatlerde olusuyor, o gorene kadar fiyat
gidiyor. Bu, otomatik emir acmadan cozulen bir sorun: haber ulastiginda
karari yine insan veriyor.

BU BIR EMIR DEGIL
-----------------
Bildirimin metni bir plani OZETLIYOR: giris, stop ve bes kademe hedef.
"Al" demiyor. Seviyeler zaten arayuzde duruyor; buradaki tek katma deger
ZAMANINDA HABER VERMEK.

TEKRAR KAPISI NEDEN ZORUNLU
---------------------------
Arka plan taramasi periyodik doner ve ayni kurulum saatlerce listede
kalir. Filtresiz gondermek gunde yuzlerce bildirim demek; bir sure sonra
kullanici kanali komple susturur ve bildirim OLUR. Uc bagimsiz kapi var:
  1. sembol basina bekleme suresi (tekrar_saat)
  2. tarama basina azami adet (tarama_azami)
  3. gun basina azami adet (gunluk_azami)

DURUM NEREDE TUTULUYOR
----------------------
app_settings icinde tek bir JSON. Sunucu yeniden baslarsa gecmis
kaybolmasin diye bellekte degil diskte: aksi halde her deploy sonrasi
ayni kurulumlar yeniden bildirilirdi.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .. import db, runtime
from . import webpush

log = logging.getLogger("vortex.plan_bildirim")

DURUM_ANAHTAR = "plan_bildirim_durumu"
# Bildirim govdesi mobilde ~4 satir gosterilir; fazlasi kirpilir.
GOVDE_AZAMI = 350


# --------------------------------------------------------------------- #
# Bicimlendirme
# --------------------------------------------------------------------- #
def _fiyat(x: Optional[float]) -> str:
    """Fiyati buyuklugune gore anlamli basamakla yazar.

    Sabit basamak sayisi burada ise yaramaz: BTC'de 4 hane gereksiz,
    PEPE'de 4 hane bilginin tamamini siler.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}".replace(",", ".")
    if a >= 100:
        return f"{v:.2f}"
    if a >= 1:
        return f"{v:.3f}"
    if a >= 0.01:
        return f"{v:.5f}"
    return f"{v:.8f}".rstrip("0")


def metin(p: Dict[str, Any]) -> Dict[str, str]:
    """Plandan bildirim basligi ve govdesi uretir.

    Kisa tutuluyor cunku kilit ekraninda okunacak. Sıra "once ne
    riske ediyorum, sonra ne kazanabilirim": stop hedeflerden ONCE
    yaziliyor.
    """
    sym = str(p.get("symbol") or "").replace("USDT", "")
    yon = str(p.get("yon") or "")
    ok = "🟢" if yon == "LONG" else "🔴"
    rr = p.get("rr")
    baslik = f"{ok} {sym} {yon} · {rr}R"

    b = p.get("boyut") or {}
    hedefler = p.get("hedef_detay") or []
    tp = " / ".join(_fiyat(h.get("fiyat")) for h in hedefler[:5]) or "—"

    satirlar = [
        f"Giriş {_fiyat(p.get('giris'))}",
        f"Stop {_fiyat(p.get('stop'))} (%{p.get('stop_r_yuzde')})",
        f"TP {tp}",
    ]
    if b.get("riske_edilen") is not None:
        satirlar.append(f"Risk {b.get('riske_edilen')}$ · {b.get('kaldirac')}x")

    gz = (p.get("giris_zamani") or {}).get("durum")
    etiket = {"simdi": "ŞİMDİ", "bekle": "BEKLE", "gec": "GEÇ KALINDI"}.get(gz or "")
    if etiket:
        satirlar[-1] = satirlar[-1] + f" · {etiket}"

    # Uyari varsa TEK satirla ekleniyor: gizlemek yanlis olurdu ama
    # bildirimi uzatmak da okunmamasina yol acar.
    uyarilar = p.get("uyarilar") or []
    if uyarilar:
        satirlar.append(f"⚠ {uyarilar[0]}")

    return {"baslik": baslik, "govde": "\n".join(satirlar)[:GOVDE_AZAMI]}


# --------------------------------------------------------------------- #
# Durum (gonderim gecmisi)
# --------------------------------------------------------------------- #
def _durum() -> Dict[str, Any]:
    d = db.get_setting(DURUM_ANAHTAR)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("son", {})      # symbol -> ts_ms
    d.setdefault("gun", "")      # YYYY-MM-DD
    d.setdefault("gun_sayi", 0)
    return d


def _kaydet(d: Dict[str, Any]) -> None:
    # Gecmis sonsuza kadar buyumesin: bir haftadan eski kayitlar silinir.
    sinir = int(time.time() * 1000) - 7 * 86_400_000
    d["son"] = {k: v for k, v in d["son"].items() if int(v or 0) >= sinir}
    db.set_setting(DURUM_ANAHTAR, d)


def _bugun() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def durum_ozeti() -> Dict[str, Any]:
    d = _durum()
    cfg = runtime.get().get("plan_bildirim", {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "yon": cfg.get("yon", "LONG"),
        "min_rr": cfg.get("min_rr", 1.8),
        "zamanlama": cfg.get("zamanlama", "simdi"),
        "bugun_gonderilen": int(d["gun_sayi"]) if d["gun"] == _bugun() else 0,
        "gunluk_azami": cfg.get("gunluk_azami", 12),
        "bekleyen_semboller": len(d["son"]),
        "abonelik": webpush.status().get("subscriptions", 0),
    }


# --------------------------------------------------------------------- #
# Secim
# --------------------------------------------------------------------- #
def _uygun_mu(p: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """Plan bildirime uygunsa None, degilse ELEME SEBEBI doner.

    Sebep dondurmesinin nedeni teshis: "neden bildirim gelmedi" sorusu
    kullanicinin en sik soracagi sey ve tahminle cevaplanmamali.
    """
    if not p.get("var_mi"):
        return "kurulum yok"
    yon = str(cfg.get("yon", "LONG")).upper()
    if yon in ("LONG", "SHORT") and p.get("yon") != yon:
        return f"yön {p.get('yon')} (süzgeç {yon})"
    try:
        if float(p.get("rr") or 0) < float(cfg.get("min_rr", 1.8)):
            return f"rr {p.get('rr')} < {cfg.get('min_rr')}"
    except (TypeError, ValueError):
        return "rr okunamadı"
    if cfg.get("zamanlama", "simdi") == "simdi":
        gz = (p.get("giris_zamani") or {}).get("durum")
        if gz and gz != "simdi":
            return f"zamanlama {gz}"
    if cfg.get("btc_catismasini_atla", True):
        if (p.get("btc") or {}).get("durum") == "carpisiyor":
            return "BTC çatışması"
    if not (p.get("hedef_detay") or []):
        return "hedef yok"
    return None


def secim(planlar: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gonderilecekleri ve elenenleri sebepleriyle birlikte dondurur.

    Gonderim YAPMAZ — bu ayrim test edilebilirlik icin: secim mantigi
    push katmanina hic dokunmadan sinanabiliyor.
    """
    cfg = runtime.get().get("plan_bildirim", {})
    d = _durum()
    if d["gun"] != _bugun():
        d["gun"], d["gun_sayi"] = _bugun(), 0

    kalan_gun = int(cfg.get("gunluk_azami", 12)) - int(d["gun_sayi"])
    kalan_tarama = int(cfg.get("tarama_azami", 3))
    bekleme_ms = int(cfg.get("tekrar_saat", 6)) * 3_600_000
    now = int(time.time() * 1000)

    gonderilecek: List[Dict[str, Any]] = []
    elenen: List[Dict[str, str]] = []

    for p in planlar:
        sym = str(p.get("symbol") or "")
        sebep = _uygun_mu(p, cfg)
        if sebep:
            elenen.append({"symbol": sym, "sebep": sebep})
            continue
        son = int(d["son"].get(sym) or 0)
        if son and now - son < bekleme_ms:
            kalan_dk = int((bekleme_ms - (now - son)) / 60000)
            elenen.append({"symbol": sym, "sebep": f"tekrar kapısı ({kalan_dk} dk)"})
            continue
        if kalan_gun <= 0:
            elenen.append({"symbol": sym, "sebep": "günlük sınır doldu"})
            continue
        if kalan_tarama <= 0:
            elenen.append({"symbol": sym, "sebep": "tarama başına sınır"})
            continue
        gonderilecek.append(p)
        kalan_gun -= 1
        kalan_tarama -= 1

    return {"gonderilecek": gonderilecek, "elenen": elenen, "durum": d}


# --------------------------------------------------------------------- #
# Gonderim
# --------------------------------------------------------------------- #
async def bildir(planlar: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Yeni kurulumlari push olarak gonderir.

    Hicbir hatasi tarama akisini kesmemeli: bildirim gonderilemedigi icin
    tarama sonucunun kaybolmasi, sorunu buyutmek olurdu. Cagiran taraf da
    bu yuzden bunu try icinde cagiriyor.
    """
    cfg = runtime.get().get("plan_bildirim", {})
    if not cfg.get("enabled", True):
        return {"ok": True, "gonderildi": 0, "sebep": "kapalı"}
    if not planlar:
        return {"ok": True, "gonderildi": 0, "sebep": "plan yok"}

    s = secim(planlar)
    if not s["gonderilecek"]:
        return {"ok": True, "gonderildi": 0, "sebep": "uygun plan yok",
                "elenen": s["elenen"][:8]}

    d = s["durum"]
    now = int(time.time() * 1000)
    gonderildi = 0
    hatalar: List[str] = []

    for p in s["gonderilecek"]:
        m = metin(p)
        sym = str(p.get("symbol") or "")
        try:
            r = await webpush.send(
                m["baslik"], m["govde"],
                url=f"/workspace?symbol={sym}",
                # tag sembol basina: ayni sembolun eski bildirimi
                # yenisiyle DEGISIR, ust uste yigilmaz.
                tag=f"plan-{sym}",
                data={"symbol": sym, "yon": p.get("yon"),
                      "giris": p.get("giris"), "stop": p.get("stop")},
            )
        except Exception as exc:  # noqa: BLE001
            hatalar.append(f"{sym}: {type(exc).__name__}")
            continue
        if r.get("ok"):
            gonderildi += 1
            d["son"][sym] = now
            d["gun_sayi"] = int(d.get("gun_sayi", 0)) + 1
        else:
            hatalar.append(f"{sym}: {r.get('error') or 'gönderilemedi'}")

    if gonderildi:
        _kaydet(d)
        log.info("Plan bildirimi: %d gonderildi", gonderildi)
    if hatalar:
        log.warning("Plan bildirim hatalari: %s", hatalar[:3])
    return {"ok": True, "gonderildi": gonderildi,
            "elenen": s["elenen"][:8], "hatalar": hatalar[:5]}
