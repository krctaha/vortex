"""RSI bağlamı — "RSI 80, düşecek mi yoksa daha da gidecek mi?"

SORUN
-----
Çıplak RSI'ın yön içeriği YOKTUR. "Aşırı alım" bir tahmin değil, bir
tanımdır: son 14 barın kazançları kayıplarına baskın demektir. Bu bilgi
tek başına "düşecek" anlamına gelmez — güçlü bir trendde tam tersini
söyler, çünkü trend zaten kazançların kayıplara baskın olması demektir.

Bu, RSI'ı yorumlayan literatürün bilinen bulgusu (Constance Brown /
Andrew Cardwell): YUKARI trendde RSI kabaca 40–80 arasında salınır ve
80 üstü GÜÇ işaretidir; AŞAĞI trendde 20–60 arasında salınır ve 20 altı
ZAYIFLIK işaretidir. Yani aynı RSI değeri, rejime göre zıt iki şey söyler.

O halde doğru soru "RSI kaç?" değil, "bu RSI hangi rejimde oluştu?"

BEŞ AYIRT EDİCİ
---------------
Hepsi elimizdeki veriden hesaplanıyor, hiçbiri tahmin değil:

1. REJİM (|t|) — oynaklığa bölünmüş momentum, ana motorun kullandığı
   sayının aynısı. Aşırı alım, güçlü yukarı trendde DEVAM; trend zayıf
   ya da tersse TÜKENME lehine.

2. UYUMSUZLUK — fiyat daha yüksek tepe yaparken RSI daha düşük tepe
   yapıyorsa, yükselişi süren güç azalıyor demektir. Klasik tükenme
   işareti ve ölçülebilir.

3. KALICILIK — son 10 barın kaçında RSI eşiğin üstündeydi. Günlerdir
   aşırı alımda kalmak bir TREND imzasıdır; sessizlikten sonra tek barda
   80'e fırlamak daha çok tükenme adayıdır.

4. GERİLME — fiyatın EMA20'den ATR cinsinden uzaklığı. Lastik ne kadar
   gerildiyse geri çekilme olasılığı o kadar artar. %-cinsinden değil ATR
   cinsinden ölçülüyor: sakin bir coinde %5 çok, çarpık bir coinde az.

5. FONLAMA — kriptoya özgü ve elimizde. Aşırı pozitif fonlama +
   aşırı alım = long tarafı kalabalık ve bu kalabalık pozisyonu taşımak
   için ÖDÜYOR. Fonlama nötrken aynı RSI çok daha sağlıklıdır.

DÜRÜSTLÜK NOTU
--------------
Bu bir HİPOTEZ, kanıtlanmış bir kenar değil. Bileşenlerin her birinin
gerekçesi var ama bileşimin işe yaradığını kimse ölçmedi. Bu yüzden:
  - her bileşenin katkısı ayrı ayrı gösteriliyor (kara kutu yok)
  - her sınıflandırma KAYDEDİLİYOR ve N bar sonra sonucu yazılıyor
Panelde "tükenme dediklerimizin yüzde kaçı gerçekten döndü" sayısı
birikene kadar bu ekranın söyledikleri fikirdir, bulgu değildir.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from ..indicators.core import atr as _atr, ema as _ema, rsi as _rsi

# Aşırı alım/satım eşikleri. 70/30 gelenek; 80/20 "aşırı"nın aşırısı.
OVERBOUGHT = 70.0
OVERSOLD = 30.0

PERSIST_WINDOW = 10        # kalıcılık kaç barda ölçülsün
DIVERGENCE_LOOKBACK = 40   # tepe/dip aramasının penceresi
SWING_WIDTH = 2            # bir barın tepe sayılması için iki yanındaki bar sayısı


def _t_stat(close: np.ndarray, lookback: int = 30, vol_window: int = 30) -> Optional[float]:
    """Oynaklığa bölünmüş momentum — ana motordaki mantığın aynısı.

    Ham getiri karşılaştırılamaz: %10 yükselen sakin bir coin ile %10
    yükselen çarpık bir coin aynı şey değildir. Payda, o kadar barda
    ŞANSLA birikebilecek hareketin büyüklüğü.
    """
    if len(close) < lookback + vol_window + 2:
        return None
    rets = np.diff(np.log(close))
    sigma = float(np.std(rets[-vol_window:], ddof=1))
    if not np.isfinite(sigma) or sigma < 1e-9:
        return None
    move = float(np.log(close[-1] / close[-1 - lookback]))
    return move / (sigma * math.sqrt(lookback))


def _swing_points(values: np.ndarray, width: int, high: bool) -> List[int]:
    """Yerel tepe (veya dip) indeksleri. İki yanındaki `width` bardan
    daha uçta olan barlar. Basit ama şeffaf; karmaşık bir tepe algoritması
    burada ek bilgi vermiyor."""
    out: List[int] = []
    n = len(values)
    for i in range(width, n - width):
        pencere = values[i - width:i + width + 1]
        if high and values[i] == pencere.max() and values[i] > values[i - 1]:
            out.append(i)
        elif not high and values[i] == pencere.min() and values[i] < values[i - 1]:
            out.append(i)
    return out


def _divergence(close: np.ndarray, rsi_vals: np.ndarray, yon: str) -> Optional[Dict[str, Any]]:
    """Fiyat–RSI uyumsuzluğu.

    Yukarı: fiyat daha yüksek tepe, RSI daha düşük tepe -> ayı uyumsuzluğu.
    Aşağı: fiyat daha düşük dip, RSI daha yüksek dip -> boğa uyumsuzluğu.
    """
    n = min(len(close), len(rsi_vals), DIVERGENCE_LOOKBACK)
    if n < 12:
        return None
    c = close[-n:]
    r = rsi_vals[-n:]
    tepe = yon == "overbought"
    idx = _swing_points(c, SWING_WIDTH, high=tepe)
    if len(idx) < 2:
        return None
    son, onceki = idx[-1], idx[-2]
    # Iki tepe cok yakinsa bu ayni dalganin tirtiklari; uyumsuzluk degil.
    if son - onceki < 5:
        return None
    if not (np.isfinite(r[son]) and np.isfinite(r[onceki])):
        return None
    if tepe:
        var = c[son] > c[onceki] and r[son] < r[onceki]
    else:
        var = c[son] < c[onceki] and r[son] > r[onceki]
    if not var:
        return None
    return {
        "price_prev": round(float(c[onceki]), 8), "price_last": round(float(c[son]), 8),
        "rsi_prev": round(float(r[onceki]), 1), "rsi_last": round(float(r[son]), 1),
        "bars_apart": int(son - onceki),
    }


def classify(symbol: str, rows: Sequence[Sequence],
             funding_bp: Optional[float] = None,
             overbought: float = OVERBOUGHT,
             oversold: float = OVERSOLD) -> Optional[Dict[str, Any]]:
    """Bir sembolün RSI'ını BAĞLAMIYLA sınıflandırır.

    rows: binance kline listesi. funding_bp: anlık fonlama, baz puan.
    Döner: None (yeterli veri yok) ya da karar sözlüğü.

    Skor POZİTİFSE devam, NEGATİFSE tükenme lehine. Her bileşenin
    katkısı ayrı ayrı `evidence` içinde; toplamı tek başına anlamlı
    değil, gerekçesiyle birlikte okunmalı.
    """
    if len(rows) < 60:
        return None
    try:
        close = np.array([float(b[4]) for b in rows], dtype=float)
        high = np.array([float(b[2]) for b in rows], dtype=float)
        low = np.array([float(b[3]) for b in rows], dtype=float)
    except (TypeError, ValueError, IndexError):
        return None

    rsi_vals = _rsi(close, 14)
    rsi_now = float(rsi_vals[-1])
    if not np.isfinite(rsi_now):
        return None

    if rsi_now >= overbought:
        yon = "overbought"
    elif rsi_now <= oversold:
        yon = "oversold"
    else:
        yon = "neutral"

    atr_vals = _atr(high, low, close, 14)
    atr_now = float(atr_vals[-1]) if np.isfinite(atr_vals[-1]) else None
    ema20 = _ema(close, 20)
    price = float(close[-1])

    # --- bileşenler -------------------------------------------------------
    t = _t_stat(close)
    esik = overbought if yon == "overbought" else oversold
    if yon == "overbought":
        kalicilik = int(np.sum(rsi_vals[-PERSIST_WINDOW:] >= esik))
    elif yon == "oversold":
        kalicilik = int(np.sum(rsi_vals[-PERSIST_WINDOW:] <= esik))
    else:
        kalicilik = 0

    gerilme = None
    if atr_now and atr_now > 0 and np.isfinite(ema20[-1]):
        gerilme = round((price - float(ema20[-1])) / atr_now, 2)

    uyumsuzluk = _divergence(close, rsi_vals, yon) if yon != "neutral" else None

    # --- skorlama ---------------------------------------------------------
    # Yön çarpanı: aşırı SATIMDA işaretler ters okunur. dev>0 = devam,
    # yani aşırı alımda "yükselmeye devam", aşırı satımda "düşmeye devam".
    puan = 0.0
    kanit: List[Dict[str, Any]] = []

    def ekle(ad: str, katki: float, aciklama: str) -> None:
        nonlocal puan
        puan += katki
        kanit.append({"ad": ad, "katki": round(katki, 2), "aciklama": aciklama})

    if yon == "neutral":
        # AŞIRI BÖLGE KARARI VERİLEMEZ — ortada tükenecek ya da sürecek bir
        # aşırılık yok. Ama trendin yönü yine de gerçek bir bilgi ve
        # söylenebilir. İkisini AYNI rozet gibi göstermek yanıltıcı olurdu:
        # biri beş bileşenli bir karar, diğeri tek bileşenli bir gözlem.
        # O yüzden ayrı alan (`trend`) ve ayrı etiket.
        if t is None:
            trend, trend_label = None, "—"
        elif t >= 1.5:
            trend, trend_label = "up", "Trend yukarı"
        elif t <= -1.5:
            trend, trend_label = "down", "Trend aşağı"
        elif t >= 0.5:
            trend, trend_label = "up", "Hafif yukarı"
        elif t <= -0.5:
            trend, trend_label = "down", "Hafif aşağı"
        else:
            trend, trend_label = None, "Yönsüz"
        return {"symbol": symbol, "rsi": round(rsi_now, 2), "zone": "neutral",
                "price": price, "verdict": "notr", "score": 0.0, "evidence": [],
                "t_stat": round(t, 2) if t is not None else None,
                "persistence": 0, "persist_window": PERSIST_WINDOW,
                "stretch_atr": gerilme, "divergence": None,
                "expect": None, "label": trend_label,
                "trend": trend, "trend_label": trend_label,
                "funding_bp": round(float(funding_bp), 2) if funding_bp is not None else None}

    isaret = 1.0 if yon == "overbought" else -1.0   # trendin "devam" yönü

    # Hareket parabolik mi? Rejim, kalıcılık ve gerilme AYNI hareketi ölçüyor:
    # dikey bir sıçrama üçünü birden aşırı gösterir. Üçünü bağımsız kanıt
    # saymak aynı bilgiyi üç kez saymak olurdu. Fiyat ortalamadan bu kadar
    # açıldıysa yüksek momentum artık "sağlık" kanıtı değildir.
    parabolik = gerilme is not None and (gerilme * isaret) >= 3.5

    # 1) REJİM — en ağır bileşen. Aşırı alım güçlü yukarı trenddeyse güçtür.
    if t is not None:
        hiza = t * isaret            # pozitif: trend, aşırılığın yönünde
        if parabolik and hiza > 0:
            ekle("rejim", 0.0,
                 f"momentum yüksek (t={t:+.2f}) ama fiyat parabolik — bu güç kanıtı sayılmıyor")
        elif hiza >= 1.5:
            ekle("rejim", 2.5, f"trend aşırılıkla aynı yönde ve güçlü (t={t:+.2f})")
        elif hiza >= 0.5:
            ekle("rejim", 1.0, f"trend aşırılıkla aynı yönde (t={t:+.2f})")
        elif hiza <= -1.0:
            ekle("rejim", -2.5, f"aşırılık trendin TERSİNE (t={t:+.2f}) — karşı yönde sıçrama")
        elif hiza <= -0.3:
            ekle("rejim", -1.0, f"trend zayıf/ters (t={t:+.2f})")
        else:
            ekle("rejim", -0.5, f"trend yönsüz (t={t:+.2f}) — aşırılığı taşıyacak güç yok")

    # 2) UYUMSUZLUK — tükenmenin en doğrudan işareti.
    if uyumsuzluk:
        ekle("uyumsuzluk", -2.0,
             f"fiyat yeni {'tepe' if yon == 'overbought' else 'dip'} yaptı ama RSI yapmadı "
             f"({uyumsuzluk['rsi_prev']} → {uyumsuzluk['rsi_last']})")

    # 3) KALICILIK — trend imzası mı, tek barlık sıçrama mı?
    if kalicilik >= 5 and not parabolik:
        ekle("kalıcılık", 1.5, f"son {PERSIST_WINDOW} barın {kalicilik}'inde aşırı bölgede — trend imzası")
    elif kalicilik >= 5 and parabolik:
        ekle("kalıcılık", 0.0,
             f"son {PERSIST_WINDOW} barın {kalicilik}'inde aşırı bölgede ama hareket parabolik — imza sayılmıyor")
    elif kalicilik <= 1:
        ekle("kalıcılık", -1.0, "aşırı bölgeye ilk kez girdi — tek barlık sıçrama")

    # 4) GERİLME — lastik ne kadar gerildi. ATR cinsinden, yüzde değil.
    if gerilme is not None:
        g = gerilme * isaret
        if g >= 4.0:
            # PARABOLİK. Sağlıklı bir trend ortalamasının 1–2 ATR yakınında
            # seyreder. 4+ ATR açılmış bir fiyat "güçlü trend" değil dikey
            # hareket demektir ve bu ayrımı rejim tek başına yapamıyor:
            # dikey sıçrama da yüksek t üretiyor. O yüzden bu katkı rejimi
            # EZECEK ağırlıkta.
            ekle("gerilme", -3.5,
                 f"fiyat EMA20'den {abs(gerilme):.1f} ATR açılmış — parabolik, sağlıklı trend değil")
        elif g >= 3.0:
            ekle("gerilme", -2.0, f"fiyat EMA20'den {abs(gerilme):.1f} ATR uzakta — çok gergin")
        elif g >= 2.0:
            ekle("gerilme", -1.0, f"fiyat EMA20'den {abs(gerilme):.1f} ATR uzakta")
        elif g <= 1.2:
            ekle("gerilme", 1.0, f"fiyat ortalamaya yakın ({abs(gerilme):.1f} ATR) — düzenli hareket")

    # 5) FONLAMA — kalabalık taraf ölçüsü. Tahmin değil, ödenen para.
    if funding_bp is not None:
        f = float(funding_bp) * isaret
        if f >= 5.0:
            ekle("fonlama", -2.0,
                 f"fonlama {funding_bp:+.1f}bp — aşırılığın yönü kalabalık ve taşımak için ödüyor")
        elif f >= 2.0:
            ekle("fonlama", -0.5, f"fonlama {funding_bp:+.1f}bp — hafif kalabalık")
        elif abs(f) < 1.0:
            ekle("fonlama", 0.5, f"fonlama {funding_bp:+.1f}bp — nötr, kalabalık yok")
        elif f <= -2.0:
            ekle("fonlama", 1.0,
                 f"fonlama {funding_bp:+.1f}bp — karşı taraf kalabalık, sıkışma yakıtı var")

    # --- karar ------------------------------------------------------------
    # Eşikler geniş tutuldu: iki bileşen aynı yönü göstermeden karar
    # verilmiyor. Tek bir bileşenle "tükenme" demek, çıplak RSI ile
    # "düşecek" demekten daha iyi olmazdı.
    if puan >= 2.0:
        karar = "devam"
    elif puan <= -2.0:
        karar = "tukenme"
    else:
        karar = "belirsiz"

    # ETİKET: "devam" tek başına HANGİ yöne devam ettiğini söylemiyor ve
    # aşırı alım ile aşırı satımda ZIT anlama geliyor. Aşırı satımda
    # "devam" düşmeye devam demek — ekranda yeşil bir "Devam edebilir"
    # rozeti bunu iyi haber gibi gösteriyordu. Kararı beklenen FİYAT
    # YÖNÜNE çeviriyoruz; kullanıcının sorduğu soru zaten bu.
    asiri_alim = yon == "overbought"
    devam = karar == "devam"
    beklenen = None if karar == "belirsiz" else ("up" if devam == asiri_alim else "down")
    etiket = {
        ("overbought", "devam"):   "Yükseliş sürebilir",
        ("overbought", "tukenme"): "Düşüş gelebilir",
        ("oversold", "devam"):     "Düşüş sürebilir",
        ("oversold", "tukenme"):   "Tepki gelebilir",
    }.get((yon, karar), "Belirsiz")

    return {
        "symbol": symbol,
        "rsi": round(rsi_now, 2),
        "zone": yon,
        "price": price,
        # beklenen fiyat yönü: "up" / "down" / None(belirsiz)
        "expect": beklenen,
        "label": etiket,
        # Aşırı bölgede trend okuması ayrıca tutulmuyor; karar zaten onu
        # içeriyor. Alanlar UI tek şema okusun diye var.
        "trend": beklenen,
        "trend_label": etiket,
        "t_stat": round(t, 2) if t is not None else None,
        "persistence": kalicilik,
        "persist_window": PERSIST_WINDOW,
        "stretch_atr": gerilme,
        "divergence": uyumsuzluk,
        "funding_bp": round(float(funding_bp), 2) if funding_bp is not None else None,
        "score": round(puan, 2),
        "verdict": karar,
        "evidence": kanit,
    }


def verdict_label(zone: str, verdict: str) -> str:
    """Kararı beklenen FİYAT YÖNÜNE çevirir.

    "devam"/"tükenme" kavramsal olarak doğru ama tek başına yönü
    söylemiyor: aşırı satımda "devam", düşmeye devam demek. Kullanıcı
    ekrana bakınca "ne bekliyoruz" sorusunun cevabını görmeli.
    """
    return {
        ("overbought", "devam"):   "Yükseliş sürebilir",
        ("overbought", "tukenme"): "Düşüş gelebilir",
        ("oversold", "devam"):     "Düşüş sürebilir",
        ("oversold", "tukenme"):   "Tepki gelebilir",
    }.get((zone, verdict), "Belirsiz")


# --------------------------------------------------------------------------- #
# KENDİ KARNESİ
#
# Bir sınıflandırıcı kurup "mantıklı görünüyor" demek kolaydır. Bu bölüm
# onu ölçülebilir kılıyor: her karar kaydediliyor, N bar sonra fiyata
# bakılıp isabet edip etmediği işaretleniyor. Panelde görünen isabet oranı
# bu tablodan SAYILIYOR — benim iddiam değil.
# --------------------------------------------------------------------------- #
from .. import db                                   # noqa: E402
from . import binance                               # noqa: E402

_INTERVAL_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
                "4h": 14_400_000, "1d": 86_400_000}


def record(call: Dict[str, Any], interval: str, horizon_bars: int = 12) -> Optional[int]:
    """Bir sınıflandırmayı ölçülmek üzere kaydeder.

    Yalnızca aşırı bölgedeki ve KARARLI (belirsiz olmayan) çağrılar
    kaydediliyor: "belirsiz" bir tahmin değil, tahminden kaçınmadır —
    isabet oranına katmak sayıyı anlamsız şişirirdi.
    """
    if call.get("zone") not in ("overbought", "oversold"):
        return None
    if call.get("verdict") not in ("devam", "tukenme"):
        return None
    now = db.now_ms()
    adim = _INTERVAL_MS.get(interval, 3_600_000)
    # Ayni sembol icin acik bir cagri varsa tekrar yazma: radar her 3
    # dakikada bir calisiyor, her turda kayit acmak ayni gozlemi onlarca
    # kez saymak olurdu.
    var = db.query_one(
        "SELECT id FROM rsi_calls WHERE symbol=? AND interval=? AND evaluated_at IS NULL",
        (call["symbol"], interval))
    if var:
        return int(var["id"])
    return db.execute(
        "INSERT INTO rsi_calls(symbol,interval,created_at,evaluate_at,rsi,zone,verdict,"
        "score,price,components) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (call["symbol"], interval, now, now + horizon_bars * adim,
         call.get("rsi"), call.get("zone"), call.get("verdict"), call.get("score"),
         call.get("price"), json.dumps({
             "t_stat": call.get("t_stat"), "persistence": call.get("persistence"),
             "stretch_atr": call.get("stretch_atr"),
             "divergence": bool(call.get("divergence")),
             "funding_bp": call.get("funding_bp"),
             "evidence": call.get("evidence", []),
         }, ensure_ascii=False)))


async def evaluate_due() -> Dict[str, int]:
    """Süresi dolan çağrıları fiyata bakıp işaretler.

    İSABET TANIMI — tek cümlede: karar, fiyatın gittiği yönü tuttu mu?
      aşırı alımda  "tükenme" -> fiyat düştüyse doğru
      aşırı alımda  "devam"   -> fiyat yükseldiyse doğru
      aşırı satımda ters okunur.
    Yatay kalırsa (|değişim| < %0.1) kararsız sayılıp isabete katılmıyor;
    "hiçbir şey olmadı" ne doğru ne yanlıştır.
    """
    now = db.now_ms()
    rows = db.query(
        "SELECT * FROM rsi_calls WHERE evaluated_at IS NULL AND evaluate_at<=? LIMIT 200", (now,))
    out = {"evaluated": 0, "correct": 0, "flat": 0, "error": 0}
    if not rows:
        return out
    try:
        tick = {t["symbol"]: float(t.get("lastPrice", 0) or 0)
                for t in await binance.ticker_24h()}
    except Exception:  # noqa: BLE001
        out["error"] = len(rows)
        return out

    for r in rows:
        row = dict(r)
        fiyat = tick.get(row["symbol"])
        if not fiyat or not row.get("price"):
            continue
        degisim = (fiyat - float(row["price"])) / float(row["price"]) * 100
        if abs(degisim) < 0.1:
            dogru = None
            out["flat"] += 1
        else:
            yukari = degisim > 0
            asiri_alim = row["zone"] == "overbought"
            devam = row["verdict"] == "devam"
            # asiri alimda devam = yukari, tukenme = asagi; asiri satimda ters
            beklenen_yukari = (devam == asiri_alim)
            dogru = (yukari == beklenen_yukari)
            if dogru:
                out["correct"] += 1
        db.execute(
            "UPDATE rsi_calls SET evaluated_at=?,price_after=?,forward_pct=?,correct=? WHERE id=?",
            (now, fiyat, round(degisim, 3),
             None if dogru is None else (1 if dogru else 0), row["id"]))
        out["evaluated"] += 1
    return out


def scorecard(days: int = 30) -> Dict[str, Any]:
    """RSI radarının kendi isabet karnesi. Tahmin yok, sayım var."""
    since = db.now_ms() - days * 86_400_000
    rows = db.query(
        "SELECT verdict, zone, correct, forward_pct FROM rsi_calls "
        "WHERE evaluated_at IS NOT NULL AND correct IS NOT NULL AND created_at>=?", (since,))
    acik = db.query_one(
        "SELECT COUNT(*) c FROM rsi_calls WHERE evaluated_at IS NULL")
    out: Dict[str, Any] = {"days": days, "n": len(rows),
                           "pending": int(acik["c"]) if acik else 0, "by_verdict": {}}
    if not rows:
        out["note"] = ("Henüz sonuçlanmış çağrı yok. Bu ekranın söyledikleri "
                       "şu an bir fikir; isabet sayısı birikene kadar bulgu değil.")
        return out
    dogru = sum(1 for r in rows if r["correct"])
    out["accuracy"] = round(dogru / len(rows) * 100, 1)
    for v in ("devam", "tukenme"):
        alt = [r for r in rows if r["verdict"] == v]
        if alt:
            d = sum(1 for r in alt if r["correct"])
            out["by_verdict"][v] = {
                "n": len(alt), "accuracy": round(d / len(alt) * 100, 1),
                "avg_move_pct": round(sum(float(r["forward_pct"] or 0) for r in alt) / len(alt), 2),
            }
    # %50 bir yazi-tura ile ayni; bunun altinda kalan bir siniflandirici
    # bilgi uretmiyor demektir. Kullaniciya bunu acikca soylemek gerekiyor.
    out["better_than_coinflip"] = out["accuracy"] > 50
    return out
