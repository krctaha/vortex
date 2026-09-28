"""BTC REJIMI — altcoin planlarinin eksik ucuncu boyutu.

SORUN
-----
Kullanicinin tespiti: "genel hareketler BTC uzerinden donuyor; su anda acik
short islemlerimiz var, BTC yukari gidiyor ve ona gore zarar ediyoruz."

Tespit dogru ve sistemdeki bosluk gercekti. plan_kur her sembolu TEK
BASINA analiz ediyordu: ARBUSDT'nin kendi yapisi, kendi RSI'i, kendi order
flow'u. Ama ARBUSDT'nin bir saatlik getirisinin buyuk kismi ARBUSDT'yle
ilgili degil — BTC'yle ilgili. BTC %2 yukari giderse yuksek betali bir
altcoin %3-4 yukari gider ve o coinin "asagi baskisi" gosteren kendi
gostergeleri bunu durdurmaz.

Yani sistem, altcoin short'unu BTC'ye karsi acmis oluyordu ve bunu ne
kendisi biliyordu ne de kullaniciya soyluyordu.

BU MODUL NE OLCUYOR
-------------------
1. BTC REJIMI — BTC'nin kendi trendi (EMA dizilimi, 200 EMA'ya gore konum,
   1s/4s/24s ivme, ATR ile olculen oynaklik).
2. PIYASA GENISLIGI (breadth) — evrenin yuzde kaci yesil. Bu, hareketin
   coine mi yoksa PIYASAYA mi ait oldugunu ayirt eden en dogrudan olcu ve
   kutuphane sayesinde bedava: ~500 kontratin 24s degisimi zaten elimizde.
   %85'i yesilse o gun hicbir coin "kendi hikayesini" yasamiyordur.
3. KORELASYON ve BETA — sembol basina, BTC'ye gore. Korelasyon "ne kadar
   birlikte hareket ediyorlar", beta "BTC %1 oynayinca bu coin yuzde kac
   oynuyor". Ikisi ayri sorular: dusuk korelasyonlu yuksek beta gurultudur,
   yuksek korelasyonlu yuksek beta ise kaldiracli BTC demektir.

NE OLCMUYOR — ve neden bunu yazmak onemli
------------------------------------------
Korelasyon nedensellik degildir ve beta SABIT DEGILDIR: sakin donemde 0.9
olan korelasyon, coine ozel bir haberde 0.2'ye duser. Bu yuzden bu modulun
ciktisi bir VETO degil, bir UYARI. "BTC yukari, sen short'sun, bu kontrat
BTC ile %82 korele" cumlesi kullaniciya karar verdirir; onun yerine karar
vermez.

Ayrica hicbir backtest'i yok. Olcum dogru, tahmin degeri iddia edilmiyor.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import db

log = logging.getLogger("vortex.btc")

BTC = "BTCUSDT"

# Korelasyon icin asgari ortak bar sayisi. Bunun altinda korelasyon
# rakami gurultuden ibaret olur; None donuyoruz.
MIN_BAR = 60
KORELASYON_BAR = 120

# BTC bar onbellegi: bir tarama turunda 150 sembol icin 150 kez BTC
# cekmenin anlami yok.
_bar_onbellek: Dict[str, Dict[str, Any]] = {}
BAR_TTL_SN = 45.0

_rejim: Dict[str, Any] = {"ts": 0.0, "veri": None}
REJIM_TTL_SN = 60.0


# ------------------------------------------------------------------ #
# YARDIMCILAR
# ------------------------------------------------------------------ #
def _getiriler(kapanislar: List[float]) -> List[float]:
    """Ardisik log getiriler. Log kullaniyoruz cunku yuzde getiriler
    toplanabilir degil ve buyuk hareketlerde carpik olcum verir."""
    out: List[float] = []
    for i in range(1, len(kapanislar)):
        a, b = kapanislar[i - 1], kapanislar[i]
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def korelasyon_beta(sembol_kapanis: List[float],
                    btc_kapanis: List[float]) -> Optional[Dict[str, float]]:
    """Pearson korelasyonu ve BTC'ye gore beta.

    beta = kov(sembol, btc) / var(btc). "BTC %1 oynadiginda bu kontrat
    tarihsel olarak yuzde kac oynamis" sorusunun cevabi.
    """
    n = min(len(sembol_kapanis), len(btc_kapanis))
    if n < MIN_BAR + 1:
        return None
    a = _getiriler(sembol_kapanis[-n:])
    b = _getiriler(btc_kapanis[-n:])
    m = min(len(a), len(b))
    if m < MIN_BAR:
        return None
    a, b = a[-m:], b[-m:]

    ort_a = sum(a) / m
    ort_b = sum(b) / m
    kov = sum((x - ort_a) * (y - ort_b) for x, y in zip(a, b)) / m
    var_a = sum((x - ort_a) ** 2 for x in a) / m
    var_b = sum((y - ort_b) ** 2 for y in b) / m
    if var_a <= 0 or var_b <= 0:
        return None
    r = kov / math.sqrt(var_a * var_b)
    return {"r": round(max(-1.0, min(r, 1.0)), 3),
            "beta": round(kov / var_b, 2), "ornek": m}


async def btc_barlari(interval: str = "1h", limit: int = 220) -> List[list]:
    """BTC mumlari — kisa omurlu onbellekli.

    Tarama turunda 150 sembol icin ayni veriyi 150 kez cekmek agirlik
    butcesini bosuna yakardi.
    """
    from . import binance
    anahtar = f"{interval}:{limit}"
    kayit = _bar_onbellek.get(anahtar)
    simdi = time.monotonic()
    if kayit and simdi - kayit["ts"] < BAR_TTL_SN:
        return kayit["rows"]
    try:
        rows = await binance.klines(BTC, interval, limit)
    except Exception as exc:  # noqa: BLE001
        log.debug("BTC barlari alinamadi: %s", exc)
        return kayit["rows"] if kayit else []
    _bar_onbellek[anahtar] = {"ts": simdi, "rows": rows}
    return rows


# ------------------------------------------------------------------ #
# BTC REJIMI
# ------------------------------------------------------------------ #
def _ema(degerler: List[float], uzunluk: int) -> Optional[float]:
    if len(degerler) < uzunluk:
        return None
    k = 2.0 / (uzunluk + 1.0)
    e = sum(degerler[:uzunluk]) / uzunluk
    for v in degerler[uzunluk:]:
        e = v * k + e * (1 - k)
    return e


def _atr_yuzde(rows: List[list], uzunluk: int = 14) -> Optional[float]:
    if len(rows) < uzunluk + 1:
        return None
    tr: List[float] = []
    for i in range(1, len(rows)):
        try:
            y, d, onceki_k = float(rows[i][2]), float(rows[i][3]), float(rows[i - 1][4])
        except (TypeError, ValueError):
            continue
        tr.append(max(y - d, abs(y - onceki_k), abs(d - onceki_k)))
    if len(tr) < uzunluk:
        return None
    atr = sum(tr[-uzunluk:]) / uzunluk
    son = float(rows[-1][4])
    return round(atr / son * 100.0, 3) if son > 0 else None


def _breadth() -> Optional[Dict[str, Any]]:
    """Evrenin yuzde kaci yesil — hareket coine mi piyasaya mi ait?

    Kutuphane sayesinde bedava: ~500 kontratin 24s degisimi zaten tabloda.
    """
    r = db.query_one(
        "SELECT COUNT(*) toplam, "
        "SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) yukselen, "
        "AVG(change_pct) ort FROM market_library WHERE change_pct IS NOT NULL")
    if not r or not r.get("toplam"):
        return None
    toplam = int(r["toplam"])
    yukselen = int(r.get("yukselen") or 0)
    return {
        "toplam": toplam,
        "yukselen": yukselen,
        "dusen": toplam - yukselen,
        "yukselen_yuzde": round(yukselen / toplam * 100.0, 1),
        "ortalama_degisim": round(float(r.get("ort") or 0.0), 2),
    }


def _rejim_kodu(kapanislar: List[float], m1: Optional[float],
                m24: Optional[float],
                atr_yuzde: Optional[float] = None) -> Tuple[str, str]:
    """Rejim kodu ve okunabilir etiketi.

    Esikler bilincli olarak GENIS: amac hassas bir tahmin degil, "hangi
    tarafa yaslanmak akintiya karsi yuzmek olur" sorusuna kaba ama
    guvenilir bir cevap. Dar esik her saat rejim degistirir ve uyari
    gurultuye donusur.

    OLU BOLGE — testle yakalanan gercek bir hata:
    ilk surumde EMA20 > EMA50 karsilastirmasinin esigi YOKTU. Dumduz bir
    piyasada bu iki ortalama birbirinden baz puanin kesri kadar ayrilir ve
    isareti tamamen gurultudur; buna ragmen tam bir oy sayiliyordu. Sonuc:
    hicbir yere gitmeyen bir BTC "yukari egilimli" ilan ediliyor, sistem
    her short'a uyari basiyor ve kullanici bir sure sonra uyarilarin
    hepsini gormezden geliyordu. Isaretsiz bir uyari, uyari olmamasindan
    kotudur.

    Olu bolge oynakliga gore olceklendiriliyor: ATR %0.1 olan sakin bir
    gunde %0.1'lik ayrim anlamsiz, ATR %3 olan bir gunde %0.4'luk ayrim
    da anlamsiz.
    """
    fiyat = kapanislar[-1]
    e20, e50, e200 = _ema(kapanislar, 20), _ema(kapanislar, 50), _ema(kapanislar, 200)
    atr = atr_yuzde if (atr_yuzde and atr_yuzde > 0) else 0.4
    olu_ema = max(0.10, 0.25 * atr)     # EMA20/50 ayrimi, fiyatin yuzdesi
    olu_200 = max(0.40, 1.00 * atr)     # fiyatin 200 EMA'ya uzakligi

    puan = 0
    if e20 and e50 and fiyat > 0:
        fark = (e20 - e50) / fiyat * 100.0
        puan += 1 if fark > olu_ema else -1 if fark < -olu_ema else 0
    if e200 and fiyat > 0:
        fark = (fiyat - e200) / e200 * 100.0
        puan += 1 if fark > olu_200 else -1 if fark < -olu_200 else 0
    if m24 is not None:
        puan += 1 if m24 > 1.0 else -1 if m24 < -1.0 else 0
    if m1 is not None:
        puan += 1 if m1 > 0.5 else -1 if m1 < -0.5 else 0

    if puan >= 3:
        return "guclu_yukari", "güçlü yukarı"
    if puan >= 1:
        return "yukari", "yukarı eğilimli"
    if puan <= -3:
        return "guclu_asagi", "güçlü aşağı"
    if puan <= -1:
        return "asagi", "aşağı eğilimli"
    return "yatay", "yönsüz / yatay"


async def rejim(interval: str = "1h") -> Dict[str, Any]:
    """BTC'nin mevcut rejimi + piyasa genisligi. 60 saniye onbellekli."""
    simdi = time.monotonic()
    if _rejim["veri"] and simdi - _rejim["ts"] < REJIM_TTL_SN:
        return _rejim["veri"]

    rows = await btc_barlari(interval, 220)
    if not rows or len(rows) < 60:
        veri = {"ok": False, "sebep": "BTC verisi yetersiz"}
        _rejim.update(ts=simdi, veri=veri)
        return veri

    try:
        kapanislar = [float(k[4]) for k in rows]
    except (TypeError, ValueError):
        return {"ok": False, "sebep": "BTC verisi okunamadi"}

    fiyat = kapanislar[-1]
    # interval bar sayisina cevriliyor: 1h'te 1 bar = 1 saat.
    bar_saat = {"5m": 12, "15m": 4, "30m": 2, "1h": 1, "4h": 0.25}.get(interval, 1)
    n1 = max(1, int(round(bar_saat)))
    n4 = max(1, int(round(bar_saat * 4)))
    n24 = max(1, int(round(bar_saat * 24)))

    def degisim(n: int) -> Optional[float]:
        if len(kapanislar) <= n or kapanislar[-1 - n] <= 0:
            return None
        return round((fiyat / kapanislar[-1 - n] - 1) * 100.0, 2)

    m1, m4, m24 = degisim(n1), degisim(n4), degisim(n24)
    atr = _atr_yuzde(rows)
    kod, etiket = _rejim_kodu(kapanislar, m1, m24, atr)

    veri = {
        "ok": True, "interval": interval, "fiyat": fiyat,
        "kod": kod, "etiket": etiket,
        "degisim_1s": m1, "degisim_4s": m4, "degisim_24s": m24,
        "atr_yuzde": atr,
        "ema20": _ema(kapanislar, 20), "ema50": _ema(kapanislar, 50),
        "ema200": _ema(kapanislar, 200),
        "genislik": _breadth(),
        "not": "BTC rejimi bir tahmin degil, bir AKINTI olcumudur. "
               "Akintiya karsi yuzmek yasak degil; farkinda olmadan "
               "yuzmek pahalidir.",
    }
    _rejim.update(ts=simdi, veri=veri)
    return veri


# ------------------------------------------------------------------ #
# PLAN <-> BTC UYUMU
# ------------------------------------------------------------------ #
# Korelasyonun "yuksek" sayildigi esik. 0.6 uzerinde bir kontrat, kendi
# hikayesinden cok BTC'nin hikayesini yasiyor demektir.
YUKSEK_KORELASYON = 0.6

# Rejimin plan yonuyle carpistigi durumlar.
_YUKARI = ("yukari", "guclu_yukari")
_ASAGI = ("asagi", "guclu_asagi")


def uyum(yon: str, rejim_kodu: str, kor: Optional[Dict[str, float]],
         genislik: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Plan yonu BTC akintisiyla uyumlu mu, catisiyor mu?

    Ciktisi bir VETO DEGIL. Korelasyon nedensellik degildir ve beta
    sabit degildir: sakin donemde 0.9 olan korelasyon, coine ozel bir
    haberde 0.2'ye duser. Bu yuzden en sert cikti "uyari" seviyesinde.
    """
    r = (kor or {}).get("r")
    beta = (kor or {}).get("beta")
    yuksek = r is not None and abs(r) >= YUKSEK_KORELASYON

    carpisma = ((yon == "SHORT" and rejim_kodu in _YUKARI) or
                (yon == "LONG" and rejim_kodu in _ASAGI))
    destek = ((yon == "LONG" and rejim_kodu in _YUKARI) or
              (yon == "SHORT" and rejim_kodu in _ASAGI))

    if r is None:
        return {"durum": "bilinmiyor", "r": None, "beta": None,
                "mesaj": "BTC korelasyonu hesaplanamadı (yeterli ortak bar yok)."}

    if carpisma and yuksek:
        yon_soz = "yukarı" if rejim_kodu in _YUKARI else "aşağı"
        ek = ""
        if genislik and rejim_kodu in _YUKARI and genislik.get("yukselen_yuzde", 0) >= 70:
            ek = (f" Evrenin %{genislik['yukselen_yuzde']}'i yeşil — bu coine özel "
                  f"bir hikâye değil, piyasa geneli.")
        elif genislik and rejim_kodu in _ASAGI and genislik.get("yukselen_yuzde", 100) <= 30:
            ek = (f" Evrenin %{100 - genislik['yukselen_yuzde']:.0f}'i kırmızı — bu coine özel "
                  f"bir hikâye değil, piyasa geneli.")
        return {
            "durum": "carpisiyor", "r": r, "beta": beta,
            "mesaj": (f"BTC {yon_soz} eğilimli ve bu kontrat BTC ile %{abs(r) * 100:.0f} "
                      f"korele (beta {beta}). {yon} akıntıya karşı: BTC %1 oynarsa bu "
                      f"kontrat tarihsel olarak %{abs(beta):.1f} oynamış.{ek}"),
        }
    if carpisma:
        return {"durum": "zayif_carpisma", "r": r, "beta": beta,
                "mesaj": (f"BTC ters yönde ama korelasyon düşük (%{abs(r) * 100:.0f}); "
                          f"bu kontrat şu an kendi hikâyesini yaşıyor olabilir.")}
    if destek and yuksek:
        return {"durum": "destekliyor", "r": r, "beta": beta,
                "mesaj": (f"BTC aynı yönde ve korelasyon yüksek (%{abs(r) * 100:.0f}). "
                          f"Akıntı arkanda — ama bu, kurulumun kendi geçerliliğini "
                          f"kanıtlamaz; BTC dönerse bu kontrat daha sert döner.")}
    return {"durum": "notr", "r": r, "beta": beta,
            "mesaj": f"BTC ile korelasyon %{abs(r) * 100:.0f}; rejim {rejim_kodu}."}
