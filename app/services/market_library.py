"""PIYASA KUTUPHANESI — butun evreni ucuza gorme katmani.

SORUN
-----
"Biz neden bu hareket baslamadan once bunu yakalayamiyoruz radarimizdan"
sorusunun cevabi mimariydi: radar KUCUKTU. Tarama su sekilde calisiyordu:

    hacme gore ilk N sembolu al -> her birine PAHALI derin analiz uygula

N kucuk olmak zorundaydi, cunku derin analiz (500 bar kline + 220 bar RSI +
premiumIndex) sembol basina ~10 agirlik harciyor ve Binance'in dakikalik
butcesi 2400. 500 kontratin hepsine derin analiz = ~5000 agirlik; tek bir
taramada butceyi iki kat asar. Yani "hepsini tara" secenegi yoktu.

Sonuc iki katli bir korluktu:
  1. ~500 surekli kontratin yalnizca 40-120'si goruluyordu.
  2. Gorulenler HEP AYNI en likit coinlerdi. Hareket kucuk bir kontratta
     basladiginda radara hic girmiyordu — RAYSOL %58 yaparken sistemin
     onu gormemesinin sebebi buydu, kotu analiz degil, YOKLUK.

COZUM: IKI KADEMELI HUNI
------------------------
Kademe 1 (ucuz, EVRENSEL). /fapi/v1/ticker/24hr sembolsuz cagrildiginda
TEK istekte butun sembolleri getiriyor ve agirligi 40. Yani tam kapsama
zaten neredeyse bedavaydi; eksik olan onu SAKLAMAK ve zaman icinde
karsilastirabilmekti. Bu modul o katmani tutuyor: her tazelemede ~500
sembolun tamami kaydediliyor.

Kademe 2 (pahali, SECICI). Derin analiz yalnizca kademe 1'in one cikardigi
adaylara uygulaniyor. Boylece butce ayni kalirken kapsama 120'den ~500'e
cikiyor.

TARIH NEDEN GEREKLI
-------------------
Binance'in verdigi 24s degisim ve hacim MUTLAK sayilar. "Hacim normalin 4
katina cikti" demek icin sembolun KENDI normaline ihtiyac var. Sabit esik
bunu yapamaz: 5 milyon dolarlik hacim BTC icin olu, yeni bir kontrat icin
patlama. Kullanicinin "3M hacim her coinde olmaz, yoksa hep yuksek fiyatli
coinleri kovalar" tespiti tam olarak buydu. market_history her sembolun
kendi taban cizgisini biriktiriyor.

ILGI PUANI NE DEGILDIR
----------------------
ilgi bir TAHMIN degil, bir DIKKAT DAGITIM kurali. "Bu coin yukselecek"
demiyor; "pahali analizi harcayacak yer burasi" diyor. Hicbir backtest'i
yok ve olmasi da gerekmiyor: yanlis siralarsa maliyeti bir sonraki tarama
turunda duzelir, para kaybettirmez. Karar hala kademe 2'nin ve kullanicinin.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from statistics import median
from typing import Any, Dict, List, Optional

from .. import db

log = logging.getLogger("vortex.market.library")

# Kutuphaneyi ne siklikta tazeleyecegiz. Tek cagrinin agirligi 40; dakikada
# bir bile calissa 2400'luk butcenin %1.7'si. Bilincli olarak sik.
TAZELE_SANIYE = 60

# Tarihe ne siklikta satir yaziyoruz. Tazeleme dakikalik ama her dakika
# 500 satir yazmak 14 gunde 10 milyon satir eder. Saatlik: ~168 bin. Bir
# sembolun taban cizgisi icin saatlik cozunurluk fazlasiyla yeterli.
TARIH_ARALIK_MS = 3_600_000
TARIH_GUN = 14

# Hacim orani ve sikisma icin gereken asgari gecmis. Bunun altinda o alanlar
# None doner — uydurulmus bir taban cizgisi, taban cizgisi olmamasindan kotu.
MIN_TARIH_ORNEK = 12

# Islem gorulemez kontratlari elemek icin mutlak zemin. Yuksek bir filtre
# DEGIL: amaci hacim kovalamak degil, defteri olmayan sembolu atmak.
# 06.09 oncesi bu esik 10M idi ve evrenin %80'ini kesiyordu.
TABAN_HACIM_USD = 300_000.0

_state: Dict[str, Any] = {
    "son_tazeleme": None, "sembol_sayisi": 0, "tarih_satiri": 0,
    "son_hata": None, "son_sure_ms": None, "son_tarih_yazimi": None,
}
_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()


def durum() -> Dict[str, Any]:
    return dict(_state)


# ------------------------------------------------------------------ #
# TAZELEME
# ------------------------------------------------------------------ #
def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _taban_cizgileri(now_ms: int) -> Dict[str, Dict[str, float]]:
    """Her sembol icin kendi gecmisinden medyan hacim ve medyan bant genisligi.

    Tek sorguda cekiliyor: sembol basina sorgu atmak 500 sorgu demekti.
    """
    esik = now_ms - TARIH_GUN * 86_400_000
    rows = db.query(
        "SELECT symbol, quote_volume FROM market_history "
        "WHERE ts >= ? AND quote_volume IS NOT NULL", (esik,))
    gruplar: Dict[str, List[float]] = {}
    for r in rows:
        gruplar.setdefault(r["symbol"], []).append(float(r["quote_volume"] or 0))
    out: Dict[str, Dict[str, float]] = {}
    for sym, hacimler in gruplar.items():
        if len(hacimler) >= MIN_TARIH_ORNEK:
            m = median(hacimler)
            if m > 0:
                out[sym] = {"hacim_medyan": m, "ornek": len(hacimler)}
    return out


def _momentum(now_ms: int) -> Dict[str, Dict[str, Optional[float]]]:
    """1 ve 4 saat onceki fiyata gore degisim.

    Binance'in 24s degisimi cok gec bir sinyal: %58'lik hareket zaten
    bittiginde bile 24s degisimi hala yuksek gorunur. 1 saatlik degisim
    hareketin BASLADIGI ani gosterir.
    """
    esik = now_ms - 5 * 3_600_000
    rows = db.query(
        "SELECT symbol, ts, price FROM market_history "
        "WHERE ts >= ? AND price > 0 ORDER BY ts ASC", (esik,))
    seri: Dict[str, List[tuple]] = {}
    for r in rows:
        seri.setdefault(r["symbol"], []).append((int(r["ts"]), float(r["price"])))

    def en_yakin(pts: List[tuple], hedef: int) -> Optional[float]:
        if not pts:
            return None
        best = min(pts, key=lambda p: abs(p[0] - hedef))
        # 40 dakikadan uzak bir ornek "1 saat once" sayilmaz.
        return best[1] if abs(best[0] - hedef) <= 40 * 60_000 else None

    out: Dict[str, Dict[str, Optional[float]]] = {}
    for sym, pts in seri.items():
        out[sym] = {
            "p1": en_yakin(pts, now_ms - 3_600_000),
            "p4": en_yakin(pts, now_ms - 4 * 3_600_000),
        }
    return out


def _ilgi_puani(satir: Dict[str, Any]) -> float:
    """Pahali analizi nereye harcayacagimizi belirleyen siralama puani.

    Bilesenler ve NEDEN oyle isaretli:

    hacim  — sembolun KENDI medyanina gore hacim genislemesi. Hareketin en
             erken izi budur; fiyat kimildamadan once defter kalinlasir.
             log tabani kullaniliyor cunku 40 kat ile 4 kat arasindaki fark
             10 kat degil, bir kademe daha ilgi cekicidir.
    ivme   — 1 saatlik degisim. 24s degisim gec kalmis bir olcu.
    bant   — 24s aralikta fiyatin nerede durdugu. Uclar (0 ya da 1) hem
             kirilim hem tukenme adayi; ortasi yapisal olarak sessiz.
    gec    — CEZA. 24 saatte %35'ten fazla gitmis bir sembolde "hareketi
             erken yakalama" iddiasi artik dogru degil; onu one cikarmak
             kovalamak olur. Kullanicinin sikayeti tam buydu.
    """
    puan = 0.0

    vr = satir.get("vol_ratio")
    if vr and vr > 0:
        # 1x -> 0, 2x -> ~1, 8x -> ~3, ust sinir 3.5
        puan += 2.0 * max(-1.0, min(math.log2(vr), 3.5))

    m1 = satir.get("mom_1h")
    if m1 is not None:
        puan += 1.6 * min(abs(m1) / 2.0, 3.0)

    bp = satir.get("band_pos")
    if bp is not None:
        puan += 1.2 * min(abs(bp - 0.5) * 2.0, 1.0)

    ch = satir.get("change_pct")
    if ch is not None and abs(ch) > 35.0:
        puan -= min((abs(ch) - 35.0) / 15.0, 3.0)

    return round(puan, 3)


async def tazele() -> Dict[str, Any]:
    """Tek ticker_24h cagrisiyla BUTUN evreni tazele (agirlik 40)."""
    from . import binance
    if _lock.locked():
        return {"ok": False, "sebep": "zaten calisiyor"}
    async with _lock:
        t0 = time.monotonic()
        try:
            tickers = await binance.ticker_24h()
            perp = {r["symbol"] for r in await binance.perpetual_symbols()}
        except Exception as exc:  # noqa: BLE001
            _state["son_hata"] = f"{type(exc).__name__}: {exc}"
            log.warning("Kutuphane tazelenemedi: %s", exc)
            return {"ok": False, "hata": str(exc)}

        now = db.now_ms()
        tabanlar = _taban_cizgileri(now)
        momentum = _momentum(now)

        satirlar: List[tuple] = []
        for t in (tickers or []):
            sym = str(t.get("symbol") or "")
            if sym not in perp:
                continue
            hacim = _f(t.get("quoteVolume"))
            if hacim < TABAN_HACIM_USD:
                continue
            price = _f(t.get("lastPrice"))
            high, low = _f(t.get("highPrice")), _f(t.get("lowPrice"))
            band = ((price - low) / (high - low)) if high > low else None

            taban = tabanlar.get(sym)
            vol_ratio = round(hacim / taban["hacim_medyan"], 3) if taban else None

            mom = momentum.get(sym) or {}
            m1 = (round((price / mom["p1"] - 1) * 100, 3)
                  if mom.get("p1") and price > 0 else None)
            m4 = (round((price / mom["p4"] - 1) * 100, 3)
                  if mom.get("p4") and price > 0 else None)

            sikisma = round((high - low) / price * 100, 3) if price > 0 and high > low else None

            row = {
                "symbol": sym, "price": price,
                "change_pct": _f(t.get("priceChangePercent")),
                "quote_volume": hacim, "high": high, "low": low,
                "open_price": _f(t.get("openPrice")),
                "weighted_avg": _f(t.get("weightedAvgPrice")),
                "trade_count": int(_f(t.get("count"))),
                "band_pos": round(band, 4) if band is not None else None,
                "vol_ratio": vol_ratio, "mom_1h": m1, "mom_4h": m4,
                "sikisma": sikisma,
            }
            row["ilgi"] = _ilgi_puani(row)
            satirlar.append((
                sym, now, row["price"], row["change_pct"], row["quote_volume"],
                row["high"], row["low"], row["open_price"], row["weighted_avg"],
                row["trade_count"], row["band_pos"], row["vol_ratio"],
                row["mom_1h"], row["mom_4h"], row["sikisma"], row["ilgi"]))

        if satirlar:
            db.executemany(
                "INSERT INTO market_library(symbol,updated_at,price,change_pct,"
                "quote_volume,high,low,open_price,weighted_avg,trade_count,"
                "band_pos,vol_ratio,mom_1h,mom_4h,sikisma,ilgi) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "updated_at=excluded.updated_at, price=excluded.price, "
                "change_pct=excluded.change_pct, quote_volume=excluded.quote_volume, "
                "high=excluded.high, low=excluded.low, open_price=excluded.open_price, "
                "weighted_avg=excluded.weighted_avg, trade_count=excluded.trade_count, "
                "band_pos=excluded.band_pos, vol_ratio=excluded.vol_ratio, "
                "mom_1h=excluded.mom_1h, mom_4h=excluded.mom_4h, "
                "sikisma=excluded.sikisma, ilgi=excluded.ilgi", satirlar)

        # Tarihe yalnizca saatte bir yaz.
        son_yazim = _state.get("son_tarih_yazimi") or 0
        if now - son_yazim >= TARIH_ARALIK_MS:
            db.executemany(
                "INSERT INTO market_history(symbol,ts,price,change_pct,quote_volume) "
                "VALUES(?,?,?,?,?)",
                [(s[0], now, s[2], s[3], s[4]) for s in satirlar])
            db.execute("DELETE FROM market_history WHERE ts < ?",
                       (now - TARIH_GUN * 86_400_000,))
            _state["son_tarih_yazimi"] = now

        _state.update(son_tazeleme=now, sembol_sayisi=len(satirlar),
                      son_hata=None,
                      son_sure_ms=int((time.monotonic() - t0) * 1000))
        try:
            _state["tarih_satiri"] = int(
                (db.query_one("SELECT COUNT(*) c FROM market_history") or {}).get("c", 0))
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "sembol": len(satirlar)}


# ------------------------------------------------------------------ #
# OKUMA
# ------------------------------------------------------------------ #
def kutuphane(limit: int = 500, sirala: str = "ilgi",
              min_hacim_m: float = 0.0) -> List[Dict[str, Any]]:
    kolon = {"ilgi": "ilgi", "hacim": "quote_volume", "degisim": "change_pct",
             "ivme": "mom_1h", "hacim_orani": "vol_ratio"}.get(sirala, "ilgi")
    rows = db.query(
        f"SELECT * FROM market_library WHERE quote_volume >= ? "
        f"ORDER BY {kolon} DESC NULLS LAST LIMIT ?",
        (min_hacim_m * 1e6, max(1, min(limit, 1000))))
    return [dict(r) for r in rows]


def adaylar(n: int = 60, min_hacim_m: float = 0.0,
            zorunlu: Optional[List[str]] = None) -> List[str]:
    """Kademe 2'nin (pahali derin analiz) bakacagi sembol listesi.

    Iki kaynaktan besleniyor:
      - ilgi puanina gore ilk n-k tanesi (nerede hareket var)
      - hacme gore ilk k tanesi (BTC/ETH gibi cekirdek her zaman gorulsun;
        ilgi puani sakin bir BTC'yi listeden atardi ve kullanici en cok
        onlara bakiyor)
    Ayrica `zorunlu` (ornegin acik pozisyonlar) her zaman iceride.
    """
    n = max(5, min(int(n), 300))
    cekirdek_pay = max(5, n // 5)
    secili: List[str] = []
    gorulen = set()

    for sym in (zorunlu or []):
        if sym and sym not in gorulen:
            gorulen.add(sym)
            secili.append(sym)

    for r in db.query(
            "SELECT symbol FROM market_library WHERE quote_volume >= ? "
            "ORDER BY quote_volume DESC LIMIT ?",
            (min_hacim_m * 1e6, cekirdek_pay)):
        if r["symbol"] not in gorulen:
            gorulen.add(r["symbol"])
            secili.append(r["symbol"])

    for r in db.query(
            "SELECT symbol FROM market_library WHERE quote_volume >= ? "
            "ORDER BY ilgi DESC NULLS LAST LIMIT ?",
            (min_hacim_m * 1e6, n)):
        if len(secili) >= n:
            break
        if r["symbol"] not in gorulen:
            gorulen.add(r["symbol"])
            secili.append(r["symbol"])

    return secili[:n]


def ozet() -> Dict[str, Any]:
    r = db.query_one(
        "SELECT COUNT(*) toplam, MAX(updated_at) guncel, "
        "SUM(CASE WHEN vol_ratio IS NOT NULL THEN 1 ELSE 0 END) tabanli, "
        "SUM(quote_volume) hacim FROM market_library") or {}
    h = db.query_one("SELECT COUNT(*) c, MIN(ts) ilk FROM market_history") or {}
    return {
        "izlenen_sembol": int(r.get("toplam") or 0),
        "taban_cizgisi_olan": int(r.get("tabanli") or 0),
        "toplam_hacim_usd": float(r.get("hacim") or 0),
        "guncelleme": r.get("guncel"),
        "tarih_satiri": int(h.get("c") or 0),
        "tarih_baslangic": h.get("ilk"),
        "durum": durum(),
    }


# ------------------------------------------------------------------ #
# PIYASA NABZI — BTC, ETH ve konumlanma orani
# ------------------------------------------------------------------ #
# Ekranin en ustundeki uc kutu. Fiyatlar KUTUPHANEDEN geliyor, yani ek
# maliyeti yok: ticker_24h zaten butun evreni tek cagrida getiriyor ve
# BTC ile ETH o tablonun icinde duruyor.
#
# LONG/SHORT ORANI ne olcuyor, ne OLCMUYOR:
# Binance'in /futures/data/globalLongShortAccountRatio ucu, bir sembolde
# net long tasiyan HESAP sayisinin net short tasiyanlara oranini veriyor.
# Dikkat: HESAP sayisi, POZISYON BUYUKLUGU degil. Bin kucuk hesap long,
# on dev hesap short olabilir; oran yine "long agirlikli" der. Bu yuzden
# arayuzde "hesaplarin %X'i long" yaziyor — "piyasa long" demek yanlis
# olurdu ve tam da kalabaligi takip etmeye yol acan cumle odur.
#
# Tek sembol yerine hacme gore ilk birkac sembolun hacim agirlikli
# ortalamasi aliniyor: yalnizca BTC'ye bakip "futures piyasasi" demek
# fazla dar bir iddia olurdu. Yine de bu bir BINANCE orneklemi, butun
# piyasa degil.
NABIZ_SEMBOL = 6
NABIZ_TTL_MS = 300_000          # 5 dakika; bu oran o hizda degismiyor
_nabiz: Dict[str, Any] = {"ts": 0, "veri": None}


def _lib_satir(symbol: str) -> Optional[Dict[str, Any]]:
    r = db.query_one("SELECT * FROM market_library WHERE symbol=?", (symbol,))
    return dict(r) if r else None


async def _long_short_orani() -> Optional[Dict[str, Any]]:
    from . import binance
    semboller = [r["symbol"] for r in db.query(
        "SELECT symbol FROM market_library ORDER BY quote_volume DESC LIMIT ?",
        (NABIZ_SEMBOL,))]
    if not semboller:
        return None

    async def tek(sym: str) -> Optional[Dict[str, Any]]:
        try:
            rows = await binance.global_long_short(sym, "1h", 1)
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        try:
            oran = float(rows[-1].get("longShortRatio") or 0)
            uzun = float(rows[-1].get("longAccount") or 0)
        except (TypeError, ValueError):
            return None
        if oran <= 0:
            return None
        # longAccount 0-1 arasi bir oran olarak geliyor; gelmezse
        # longShortRatio'dan turetiyoruz. Ikisi de yoksa satir atlanir.
        yuzde = uzun if 0 < uzun <= 1 else oran / (1.0 + oran)
        return {"symbol": sym, "oran": oran, "long": yuzde}

    sonuc = [x for x in await asyncio.gather(*(tek(s) for s in semboller)) if x]
    if not sonuc:
        return None

    agirlik: Dict[str, float] = {}
    isaretler = ",".join("?" * len(sonuc))
    for r in db.query(
            f"SELECT symbol, quote_volume FROM market_library "
            f"WHERE symbol IN ({isaretler})", [x["symbol"] for x in sonuc]):
        agirlik[r["symbol"]] = float(r["quote_volume"] or 0)

    top_w = sum(agirlik.get(x["symbol"], 0.0) for x in sonuc)
    if top_w <= 0:
        agirlik = {x["symbol"]: 1.0 for x in sonuc}
        top_w = float(len(sonuc))

    long_yuzde = sum(x["long"] * agirlik.get(x["symbol"], 0.0)
                     for x in sonuc) / top_w * 100.0
    long_yuzde = max(0.0, min(long_yuzde, 100.0))
    short_yuzde = 100.0 - long_yuzde
    return {
        "long_yuzde": round(long_yuzde, 1),
        "short_yuzde": round(short_yuzde, 1),
        "oran": round(long_yuzde / max(short_yuzde, 0.001), 2),
        "ornek": len(sonuc),
        "semboller": [x["symbol"] for x in sonuc],
    }


async def nabiz() -> Dict[str, Any]:
    """BTC, ETH ve konumlanma orani — sayfanin en ustundeki uc kutu.

    Fiyatlar burada kutuphaneden (60 saniyelik REST anlik goruntusu)
    donuyor; arayuz uzerine WEBSOCKET tick'lerini yaziyor. Ikisi de
    gerekli: WS baglanmadan once ya da kopunca kutuphane degeri kaliyor,
    bagliyken WS kazaniyor. 24 saatlik degisim yuzdesi WS'te gelmedigi
    icin her halukarda buradan okunuyor.
    """
    from . import binance, binance_ws
    # Akisa abone ol: istemci de abone oluyor ama sunucu tarafinda garanti
    # altina almak, kullanici baska sayfadayken bile tick'lerin akmasini
    # ve tekrar donunce fiyatin HAZIR olmasini sagliyor.
    try:
        binance_ws.watch(["BTCUSDT", "ETHUSDT"])
    except Exception:  # noqa: BLE001
        pass
    now = db.now_ms()
    if _nabiz["veri"] is not None and now - int(_nabiz["ts"] or 0) < NABIZ_TTL_MS:
        ls = _nabiz["veri"]
    else:
        ls = await _long_short_orani()
        if ls is None and binance.is_demo():
            # DEMO: kutu bos kalmasin diye sentetik ama ACIKCA isaretli
            # deger. Arayuz "demo" bayragini gorunce oyle yaziyor.
            ls = {"long_yuzde": 58.4, "short_yuzde": 41.6, "oran": 1.40,
                  "ornek": 0, "semboller": [], "demo": True}
        _nabiz.update(ts=now, veri=ls)

    def kutu(sym: str) -> Optional[Dict[str, Any]]:
        r = _lib_satir(sym)
        if not r:
            return None
        canli = None
        try:
            canli = binance_ws.get_price(sym)
        except Exception:  # noqa: BLE001
            pass
        return {"symbol": sym, "price": canli or r.get("price"),
                "kaynak": "ws" if canli else "kutuphane",
                "change_pct": r.get("change_pct"), "mom_1h": r.get("mom_1h")}

    return {"ok": True, "btc": kutu("BTCUSDT"), "eth": kutu("ETHUSDT"),
            "long_short": ls, "guncelleme": _state.get("son_tazeleme")}


# ------------------------------------------------------------------ #
# ARKA PLAN
# ------------------------------------------------------------------ #
async def _loop() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await tazele()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("kutuphane dongusu hatasi (dongu devam ediyor): %s", exc)
        await asyncio.sleep(TAZELE_SANIYE)


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="vortex-market-library")
        log.info("Piyasa kutuphanesi baslatildi (her %ds tam evren)", TAZELE_SANIYE)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None
