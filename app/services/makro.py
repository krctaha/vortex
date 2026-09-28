"""MAKRO VARLIKLAR — Nasdaq, altın, gümüş, dolar endeksi, petrol.

NEDEN AYRI BIR SERVIS
=====================
Kripto tek basina bir dunya degil. Altin yukselirken, dolar endeksi
sertlesirken ya da Nasdaq duserken BTC'nin davranisi degisiyor. Bu panel
o baglami ekrana getiriyor.

BU DOSYA YALNIZCA VERI KATMANI. Sinyal, skor, yon ya da tavsiye
uretmiyor — kullanicinin plani "bunun icin ayri bir motor yazacagiz".
Motor sonra bu katmanin uzerine oturacak; simdiden karar mantigi
karistirmak, motoru yazarken hangi kararin nereden geldigini
bulunamaz hale getirirdi.

KAYNAK MESELESI — ve neden IKI tane var
=======================================
Binance bu urunleri listelemiyor: USDS-M futures'ta ne endeks ne de
degerli maden var. Dolayisiyla mevcut binance istemcisi burada ise
yaramiyor ve DISARIDAN bir kaynak gerekiyor.

Iki ucretsiz kaynak tanimli ve sirayla deneniyor:
  1. Yahoo Finance chart API  (vadeli kontratlar: GC=F, SI=F, NQ=F)
  2. Stooq CSV                (spot/endeks: xauusd, xagusd, ^ndq)

Ikisi de anahtarsiz. Sirayla denenmesinin sebebi dayaniklilik degil
DURUSTLUK: biri cevap vermezse digeri denenir, IKISI DE vermezse panel
"veri bekleniyor" der. Hicbir kosulda tahmini ya da eski bir sayi
guncelmis gibi sunulmaz.

DOGRULANMADI — bilinmesi gereken sinir
======================================
Bu dosya yazilirken gelistirme konteyneri her iki kaynaga da cikamiyordu
(vekil sunucu 403). Yani ayristirici GERCEK bir cevapla sinanmadi;
alan adlari birden fazla olasiliga karsi yedekli okunuyor ve `teshis()`
her kaynagin ham cevabini rapor ediyor. Sunucuda bir kez calistirilip
hangi kaynagin dondugune bakilmali.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("vortex.makro")

try:
    import httpx
    KULLANILABILIR = True
except Exception as exc:  # noqa: BLE001
    KULLANILABILIR = False
    log.warning("httpx yok, makro katmani kapali: %s", exc)

# Fiyatlar saniyede bir degismiyor ve bu uc bir borsa degil; 60 saniye
# hem yeterince taze hem de kaynaklara nazik.
ONBELLEK_SN = 60.0
ZAMAN_ASIMI = 12.0
UA = "Mozilla/5.0 (compatible; VORTEX/4.4)"

# Varlik tanimi. `grup` yalnizca arayuzde siralama icin.
# Her varlikta IKI kaynak sembolu var; biri tutmazsa digeri denenir.
VARLIKLAR: List[Dict[str, Any]] = [
    {"kod": "NASDAQ", "ad": "Nasdaq 100", "grup": "endeks",
     "yahoo": "NQ=F", "stooq": "^ndq", "basamak": 2},
    {"kod": "SP500", "ad": "S&P 500", "grup": "endeks",
     "yahoo": "ES=F", "stooq": "^spx", "basamak": 2},
    {"kod": "XAU", "ad": "Altın (ons)", "grup": "maden",
     "yahoo": "GC=F", "stooq": "xauusd", "basamak": 2},
    {"kod": "XAG", "ad": "Gümüş (ons)", "grup": "maden",
     "yahoo": "SI=F", "stooq": "xagusd", "basamak": 3},
    {"kod": "DXY", "ad": "Dolar Endeksi", "grup": "makro",
     "yahoo": "DX-Y.NYB", "stooq": "dx.f", "basamak": 3},
    {"kod": "WTI", "ad": "Ham Petrol (WTI)", "grup": "makro",
     "yahoo": "CL=F", "stooq": "cl.f", "basamak": 2},
]

_onbellek: Dict[str, Any] = {"ts": 0.0, "veri": None}


def _sayi(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    # Stooq bos alanlari "N/D" ya da 0 dondurebiliyor; sifir fiyat
    # gecerli bir fiyat degil ve sessizce gecerse yuzde hesabi patlar.
    return v if v > 0 else None


# ------------------------------------------------------------------ #
# KAYNAK 1 — Yahoo Finance chart API
# ------------------------------------------------------------------ #
async def _yahoo(client: "httpx.AsyncClient", sembol: str) -> Dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}"
    r = await client.get(url, params={"interval": "1d", "range": "5d"},
                         headers={"User-Agent": UA})
    r.raise_for_status()
    d = r.json()
    sonuc = ((d.get("chart") or {}).get("result") or [None])[0]
    if not sonuc:
        raise ValueError("chart.result bos")
    m = sonuc.get("meta") or {}
    # Alan adlari surumden surume degisebiliyor; sirayla deniyoruz.
    fiyat = _sayi(m.get("regularMarketPrice"))
    onceki = (_sayi(m.get("chartPreviousClose"))
              or _sayi(m.get("previousClose"))
              or _sayi(m.get("regularMarketPreviousClose")))
    if fiyat is None:
        # Meta vermediyse son kapanislardan al.
        try:
            kap = [x for x in (sonuc["indicators"]["quote"][0]["close"] or [])
                   if x is not None]
            fiyat = _sayi(kap[-1]) if kap else None
            if onceki is None and len(kap) > 1:
                onceki = _sayi(kap[-2])
        except (KeyError, IndexError, TypeError):
            pass
    if fiyat is None:
        raise ValueError("fiyat okunamadi")
    return {"fiyat": fiyat, "onceki": onceki,
            "para": m.get("currency"), "borsa": m.get("exchangeName"),
            "durum": m.get("marketState"), "kaynak": "yahoo"}


# ------------------------------------------------------------------ #
# KAYNAK 2 — Stooq CSV
# ------------------------------------------------------------------ #
async def _stooq(client: "httpx.AsyncClient", sembol: str) -> Dict[str, Any]:
    r = await client.get("https://stooq.com/q/l/",
                         params={"s": sembol, "f": "sd2t2ohlcv",
                                 "h": "", "e": "csv"},
                         headers={"User-Agent": UA})
    r.raise_for_status()
    satirlar = list(csv.DictReader(io.StringIO(r.text)))
    if not satirlar:
        raise ValueError("csv bos")
    s = satirlar[0]
    fiyat = _sayi(s.get("Close"))
    acilis = _sayi(s.get("Open"))
    if fiyat is None:
        raise ValueError(f"kapanis okunamadi: {str(s)[:80]}")
    # Stooq onceki kapanisi vermiyor; gun ici degisim icin ACILIS
    # kullaniliyor. Bu, Yahoo'nun "onceki kapanisa gore" degisiminden
    # FARKLI bir olcu — hangisinin kullanildigi satirda yaziyor,
    # cunku iki farkli sayiyi ayni etiketle sunmak yaniltir.
    return {"fiyat": fiyat, "onceki": acilis, "para": None,
            "borsa": None, "durum": None, "kaynak": "stooq",
            "degisim_tabani": "acilis"}


KAYNAKLAR = (("yahoo", _yahoo), ("stooq", _stooq))


# ------------------------------------------------------------------ #
# TOPLAMA
# ------------------------------------------------------------------ #
async def _bir_varlik(client: "httpx.AsyncClient",
                      v: Dict[str, Any]) -> Dict[str, Any]:
    hatalar: List[str] = []
    for ad, fn in KAYNAKLAR:
        sembol = v.get(ad)
        if not sembol:
            continue
        try:
            ham = await fn(client, sembol)
        except Exception as exc:  # noqa: BLE001
            hatalar.append(f"{ad}: {type(exc).__name__}: {str(exc)[:70]}")
            continue
        fiyat, onceki = ham["fiyat"], ham.get("onceki")
        degisim = None
        if onceki and onceki > 0:
            degisim = round((fiyat - onceki) / onceki * 100.0, 2)
        return {
            "kod": v["kod"], "ad": v["ad"], "grup": v["grup"],
            "sembol": sembol, "fiyat": round(fiyat, v.get("basamak", 2)),
            "degisim": degisim,
            "degisim_tabani": ham.get("degisim_tabani", "onceki_kapanis"),
            "para": ham.get("para"), "borsa": ham.get("borsa"),
            "piyasa_durumu": ham.get("durum"),
            "kaynak": ham["kaynak"], "veri_yok": False,
        }
    # HICBIR KAYNAK VERMEDI. Tahmin yok, eski deger yok, sifir yok.
    return {"kod": v["kod"], "ad": v["ad"], "grup": v["grup"],
            "fiyat": None, "degisim": None, "veri_yok": True,
            "sebep": " | ".join(hatalar) or "kaynak tanımlı değil"}


async def ozet(taze: bool = False) -> Dict[str, Any]:
    """Butun makro varliklar. 60 saniye onbellekli."""
    simdi = time.monotonic()
    if not taze and _onbellek["veri"] and simdi - _onbellek["ts"] < ONBELLEK_SN:
        return {**_onbellek["veri"], "onbellek": True,
                "yas_sn": round(simdi - _onbellek["ts"], 1)}

    if not KULLANILABILIR:
        return {"ok": False, "sebep": "httpx yüklü değil", "satirlar": []}

    async with httpx.AsyncClient(timeout=ZAMAN_ASIMI, follow_redirects=True) as c:
        satirlar = await asyncio.gather(
            *(_bir_varlik(c, v) for v in VARLIKLAR))

    veri = {
        "ok": True,
        "satirlar": list(satirlar),
        "okunan": sum(1 for s in satirlar if not s["veri_yok"]),
        "toplam": len(satirlar),
        "guncelleme_ms": int(time.time() * 1000),
    }
    # BASARISIZ TURU ONBELLEGE YAZILMIYOR.
    # Yazsaydik gecici bir ag hatasi 60 saniye boyunca "veri yok"
    # ekranini kilitlerdi; oysa bir sonraki istek duzelebilirdi.
    if veri["okunan"]:
        _onbellek.update(ts=simdi, veri=veri)
    return {**veri, "onbellek": False, "yas_sn": 0}


async def teshis() -> Dict[str, Any]:
    """Her kaynagi TEK TEK dener ve ham sonucu bildirir.

    "Neden veri gelmiyor" sorusunu tahminle degil olcumle cevaplamak
    icin. Bu katman gelistirme ortamindan dogrulanamadi (vekil sunucu
    her iki kaynagi da engelliyordu); sunucuda bu ucun bir kez
    calistirilmasi gerekiyor.
    """
    if not KULLANILABILIR:
        return {"ok": False, "sebep": "httpx yüklü değil"}
    ornek = VARLIKLAR[2]          # altin: iki kaynakta da tanimli
    out: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=ZAMAN_ASIMI, follow_redirects=True) as c:
        for ad, fn in KAYNAKLAR:
            t0 = time.monotonic()
            kayit: Dict[str, Any] = {"kaynak": ad, "sembol": ornek.get(ad)}
            try:
                kayit["sonuc"] = await fn(c, ornek[ad])
                kayit["ok"] = True
            except Exception as exc:  # noqa: BLE001
                kayit["ok"] = False
                kayit["hata"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            kayit["sure_ms"] = int((time.monotonic() - t0) * 1000)
            out.append(kayit)
    return {"ok": any(k["ok"] for k in out), "denemeler": out,
            "not": "Bu katman geliştirme ortamından doğrulanamadı; "
                   "sunucudaki ilk gerçek cevap burada görünür."}
