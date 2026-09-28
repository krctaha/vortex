"""MENTOR OTOMATIK TARAMA — kullanici sormadan piyasayi tarar ve kaydeder.

NEDEN VAR
---------
Mentor'un /review ucu yalnizca kullanici bir sembol yazdiginda calisiyordu.
Yani sistem, kullanicinin AKLINA GELEN coinleri degerlendiriyordu; aklina
gelmeyenleri hic gormuyordu. Bu iki sorun uretir:

  1. Kullanici calisirken telefona bakamiyor, o saatlerde hicbir sey
     kaydedilmiyor.
  2. Ornekleme TARAFLI oluyor: "tahminlerimiz ne kadar iyi" degil,
     "Taha'nin baktigi anlarda ne kadar iyi" sorusu olculuyor. Ayni hatayi
     RSI cagrilarinda bir kez yaptik ve orada da duzeltmek zorunda kaldik.

Bu modul evreni duzenli olarak tarar, DIKKATE DEGER olanlari mentor_reviews
tablosuna yazar ve kullanici hic sormasa bile sicil birikir.

NE YAPMAZ
---------
- Emir vermez. Para katmanina hic dokunmaz.
- "Al" / "sat" demez. Ne gordugunu ve neye dayandigini yazar.
- Her sembolu kaydetmez: kaydetmek icin ESIK vardir, yoksa gunde yuzlerce
  anlamsiz satir birikir ve karne gurultuyle dolar.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from .. import db, runtime

log = logging.getLogger("vortex.mentor.scan")

SOURCE = "otomatik"
_task: Optional[asyncio.Task] = None
_state: Dict[str, Any] = {
    "son_tarama": None, "son_sure_ms": None, "taranan": 0,
    "kaydedilen": 0, "son_hata": None, "calisiyor": False,
}


def _cfg() -> Dict[str, Any]:
    return runtime.get().get("mentor_tarama", {})


def durum() -> Dict[str, Any]:
    return {**_state, "config": _cfg()}


async def _evren(limit: int, min_hacim_m: float) -> List[str]:
    """Pahali analizin bakacagi sembol listesi — kaynak: piyasa kutuphanesi.

    Onceki hali "hacme gore ilk N" idi. O kuralin sessiz maliyeti sudur:
    liste HER TURDA neredeyse ayni kaliyor, cunku hacim siralamasi gunler
    boyunca degismez. Yani sistem her saat ayni 120 coini yeniden inceleyip
    geri kalan ~380'i hic gormuyordu. Kutuphane butun evreni ucuza izleyip
    (tek ticker_24h cagrisi, agirlik 40) sembolun KENDI tabanina gore
    hareketlenenleri one cikardigi icin bu liste artik her turda degisiyor.
    """
    from . import market_library
    # Acik pozisyonlar her zaman listede: kullanicinin uzerinde parasi olan
    # sembolu taramanin disinda birakmak en kotu kor nokta olurdu.
    zorunlu: List[str] = []
    try:
        rows = db.query("SELECT DISTINCT symbol FROM mentor_trades WHERE status='open'")
        zorunlu = [r["symbol"] for r in rows]
    except Exception:  # noqa: BLE001
        pass
    semboller = market_library.adaylar(n=limit, min_hacim_m=min_hacim_m,
                                       zorunlu=zorunlu)
    if semboller:
        return semboller
    # Kutuphane henuz dolmadi (ilk acilis) — eski yol emniyet supabi.
    from . import binance
    tickers = await binance.ticker_24h()
    perp = {r["symbol"] for r in await binance.perpetual_symbols()}
    uygun = [t for t in tickers
             if t.get("symbol") in perp
             and float(t.get("quoteVolume", 0) or 0) >= min_hacim_m * 1e6]
    uygun.sort(key=lambda t: -float(t.get("quoteVolume", 0) or 0))
    return [t["symbol"] for t in uygun[:limit]]


def _kaydedilmis_mi(symbol: str, pencere_ms: int) -> bool:
    """Ayni sembol icin yakin zamanda otomatik kayit var mi?

    Olmazsa her turda ayni gozlem tekrar yazilir ve karne tek bir olayi
    onlarca kez saymis olur.
    """
    row = db.query_one(
        "SELECT created_at FROM mentor_reviews WHERE symbol=? AND direction=? "
        "ORDER BY created_at DESC LIMIT 1", (symbol, SOURCE))
    return bool(row and db.now_ms() - int(row["created_at"]) < pencere_ms)


async def _degerlendir(symbol: str, interval: str) -> Optional[Dict[str, Any]]:
    """Tek sembol icin ayrintili okuma. Kayit esigini gecerse dict doner."""
    from . import analysis, rsi_context, binance

    snap = await analysis.snapshot(symbol, interval, 500, include_series=False)
    if not snap.get("ok"):
        return None
    try:
        snap["context"] = await analysis.market_context(symbol)
    except Exception:  # noqa: BLE001
        pass
    d = analysis.detailed_analysis(snap)

    # RSI baglami: ayni mumlardan, ek istek yok.
    rsi_ctx = None
    try:
        rows = await binance.klines(symbol, interval, 220)
        fr = None
        try:
            pi = await binance.premium_index(symbol)
            fr = float(pi.get("lastFundingRate", 0) or 0) * 10_000
        except Exception:  # noqa: BLE001
            pass
        if rows and len(rows) >= 60:
            rsi_ctx = rsi_context.classify(symbol, rows, fr)
    except Exception:  # noqa: BLE001
        pass

    price = float(snap.get("price") or 0)
    ind = snap.get("indicators") or {}
    lehte = d.get("evidence_for") or []
    aleyhte = d.get("evidence_against") or []

    # --- KAYIT ESIGI --------------------------------------------------
    # Her sembolu yazmak karneyi gurultuyle doldurur. Yalnizca "bakmaya
    # deger" olanlar giriyor. Esikler ayarlardan degistirilebilir.
    c = _cfg()
    sebepler: List[str] = []
    if rsi_ctx and rsi_ctx.get("zone") in ("overbought", "oversold") \
            and rsi_ctx.get("verdict") in ("devam", "tukenme"):
        sebepler.append(
            f"RSI {rsi_ctx.get('rsi')} {'asiri alim' if rsi_ctx['zone']=='overbought' else 'asiri satim'}"
            f" — {rsi_context.verdict_label(rsi_ctx['zone'], rsi_ctx['verdict'])}")
    if len(lehte) >= int(c.get("min_lehte_kanit", 3)):
        sebepler.append(f"{len(lehte)} bagimsiz lehte kanit hizali")
    if d.get("quality") and "guclu" in str(d.get("quality")).lower():
        sebepler.append(f"veri aileleri hizali ({d.get('quality')})")
    if not sebepler:
        return None

    return {
        "symbol": symbol, "interval": interval, "price": price,
        "yon_okumasi": d.get("bias") or d.get("direction"),
        "kalite": d.get("quality"),
        "kayit_sebebi": sebepler,
        "ozet": d.get("summary"),
        "teyit": d.get("confirmation"),
        "gecersizlik": d.get("invalidation"),
        "lehte": lehte[:6],
        "aleyhte": aleyhte[:6],
        "kor_nokta": (d.get("blind_spots") or [])[:4],
        "boga_senaryo": d.get("bull_scenario"),
        "ayi_senaryo": d.get("bear_scenario"),
        "rsi": (rsi_ctx or {}).get("rsi"),
        "rsi_bolge": (rsi_ctx or {}).get("zone"),
        "rsi_karar": (rsi_ctx or {}).get("verdict"),
        "t_stat": (rsi_ctx or {}).get("t_stat"),
        "funding_bp": round(fr, 2) if fr is not None else None,
        "atr14": ind.get("atr14"),
        "kaynak": SOURCE,
        "uyari": "Otomatik gozlem. Emir degildir, kendi kontrol listeni gec.",
    }


async def tara_bir_kez(admin_id: Optional[int] = None) -> Dict[str, Any]:
    import time
    c = _cfg()
    if _state["calisiyor"]:
        return {"ok": False, "sebep": "zaten calisiyor"}
    _state["calisiyor"] = True
    t0 = time.monotonic()
    taranan = kaydedilen = 0
    try:
        if admin_id is None:
            row = db.query_one("SELECT id FROM users ORDER BY id LIMIT 1")
            admin_id = int(row["id"]) if row else None
        if admin_id is None:
            return {"ok": False, "sebep": "kullanici yok"}

        semboller = await _evren(int(c.get("evren", 40)),
                                 float(c.get("min_hacim_m", 10)))
        pencere = int(c.get("tekrar_pencere_saat", 6)) * 3_600_000
        sem = asyncio.Semaphore(int(c.get("es_zamanli", 3)))

        async def islet(sym: str) -> None:
            nonlocal taranan, kaydedilen
            if _kaydedilmis_mi(sym, pencere):
                return
            async with sem:
                try:
                    sonuc = await _degerlendir(sym, str(c.get("interval", "1h")))
                    taranan += 1
                except Exception as exc:  # noqa: BLE001
                    log.debug("tarama hatasi %s: %s", sym, exc, exc_info=True)
                    return
            if not sonuc:
                return
            db.execute(
                "INSERT INTO mentor_reviews(user_id,symbol,interval,direction,verdict,"
                "price,entry_price,stop_price,target_price,reward_risk,created_at,payload) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (admin_id, sym, sonuc["interval"], SOURCE,
                 "OTOMATIK GOZLEM", sonuc["price"], None, None, None, None,
                 db.now_ms(), json.dumps(sonuc, ensure_ascii=False)))
            kaydedilen += 1

        await asyncio.gather(*(islet(s) for s in semboller))
        _state.update(son_tarama=db.now_ms(), taranan=taranan,
                      kaydedilen=kaydedilen, son_hata=None,
                      son_sure_ms=int((time.monotonic() - t0) * 1000))
        if kaydedilen:
            log.info("Mentor otomatik tarama: %d sembol incelendi, %d gozlem kaydedildi",
                     taranan, kaydedilen)
        return {"ok": True, "taranan": taranan, "kaydedilen": kaydedilen}
    except Exception as exc:  # noqa: BLE001
        _state["son_hata"] = f"{type(exc).__name__}: {exc}"
        log.warning("Mentor tarama basarisiz: %s", exc)
        return {"ok": False, "hata": str(exc)}
    finally:
        _state["calisiyor"] = False


async def _loop() -> None:
    await asyncio.sleep(90)   # acilis yukunu tarama ile carpistirma
    while True:
        try:
            c = _cfg()
            if c.get("enabled", True):
                await tara_bir_kez()
            bekle = max(600, int(c.get("aralik_dakika", 60)) * 60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("mentor tarama dongusu hatasi (dongu devam ediyor): %s", exc)
            bekle = 900
        await asyncio.sleep(bekle)


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="vortex-mentor-scan")
        log.info("Mentor otomatik taramasi baslatildi")


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None


def son_gozlemler(limit: int = 20) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT id,symbol,interval,price,created_at,payload FROM mentor_reviews "
        "WHERE direction=? ORDER BY created_at DESC LIMIT ?", (SOURCE, limit))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except (TypeError, ValueError):
            d["payload"] = {}
        out.append(d)
    return out
