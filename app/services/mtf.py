"""COK ZAMAN DILIMI — yonu buyuk resim verir, zamanlamayi kucuk resim.

NEDEN AYRI IKI SORU
-------------------
Tek zaman diliminde calisan bir sistem iki farkli soruyu ayni veriyle
cevaplamaya calisir ve ikisinde de ortalama bir is cikarir:

  "Hangi yone?"  -> yavas soru. 15 dakikalik gurultu bu sorunun cevabini
                    saatte bes kez degistirir; 4 saatlik yapi ise gunlerce
                    ayni seyi soyler. Yon buyuk resimden gelmeli.
  "Ne zaman?"    -> hizli soru. 4 saatlik mum sana "su anda mi girmeliyim"
                    diye soramaz; o mum kapandiginda hareket bitmis olur.
                    Zamanlama kucuk resimden gelmeli.

Kullanicinin istegi tam olarak bu ayrimdi: yon 1h/4h/15m uzerinden,
giris 5m/1m uzerinden.

AGIRLIK BUTCESI — bu modul NEDEN sadece adaylara calisiyor
-----------------------------------------------------------
Ek zaman dilimi basina bir snapshot (~2-3 agirlik) ve zamanlama icin iki
ucuz kline cagrisi gerekiyor. 150 sembolun HEPSINE uygulamak tarama
basina ~4500 agirlik ederdi; Binance'in dakikalik butcesi 2400.

Bu yuzden huni uc kademeli:
  1. Kutuphane   -> ~500 sembolden 150 aday (bedava, tek cagri)
  2. 1h plan     -> 150 adaydan kurulumu olan ~10-15 sembol
  3. BU MODUL    -> yalnizca o 10-15 sembol icin cok zaman dilimi
Kademe 3 boylece ~300 agirlige iniyor. Ayni huni mantigi, bir kat daha.

NE VAAT ETMIYOR
---------------
Zaman dilimi hizalanmasi bir avantaj GARANTISI degil; olculmus bir
backtest'i de yok. Yaptigi sey daha mutevazi ve dogrulanabilir: bir
kurulumun yalnizca tek bir zaman diliminde var olup olmadigini soylemek.
"1h short diyor ama 4h guclu yukari" cumlesi tahmin degil, OLCUMDUR.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

log = logging.getLogger("vortex.mtf")

# YON KOMITESI ve agirliklari.
#
# 4h en agir cunku en yavas ve en kararli; 15m en hafif cunku en gurultulu.
# Ana zaman dilimi (1h) ortada: kurulumun kuruldugu yer orasi.
# Toplam agirlik 6.
YON_AGIRLIK = {"4h": 3, "1h": 2, "15m": 1}
TEYIT_INTERVAL = ("4h", "15m")     # 1h zaten kademe 2'de hesaplandi

# Hizali sayilmak icin gereken net puan. 3, "4h tek basina yeter ya da
# 1h + 15m birlikte yeter" demek. Daha dusuk bir esik 15m gurultusunun
# tek basina yon belirlemesine izin verirdi.
MIN_HIZA = 3

# --- ZAMANLAMA ---
# 5m ana zamanlama dilimi, 1m yalnizca son teyit.
ZAMAN_INTERVAL = "5m"
TETIK_INTERVAL = "1m"
# UZAMA OLCUSU — testle duzeltilen bir tasarim hatasi.
#
# Ilk surumde tek bir mutlak esik vardi: fiyat 5m EMA21'den 1.2 ATR
# uzaksa "gec kalindi". Bu YANLIS, cunku duzgun bir trendde fiyat
# ortalamanin USTUNDE KALIR — surekli. Sakin, istikrarli bir yukselisin
# her bari o esigi asar ve sistem kullaniciya "trende hicbir zaman girme"
# demis olur.
#
# Dogru soru "ortalamadan ne kadar uzak" degil, "KENDI NORMALINDEN ne
# kadar uzak". Ayni ilke kutuphanedeki hacim oraninda da kullanildi:
# mutlak esik yerine sembolun kendi tabani. Burada da mesafe, son 100
# barda ayni mesafenin dagilimiyla kiyaslaniyor.
#
# OLCU: mevcut mesafenin, son 120 bardaki mesafelerin MEDYANINA orani.
#
# Yuzdelik siralamasi denendi ve calismadi: duzgun bir trendde mesafe
# tekduze artar, dolayisiyla son bar HER ZAMAN en yuksek yuzdelikte
# cikar ve istikrarli trend "gec kalindi" sayilirdi. Medyana oran bu
# yapaylıktan etkilenmiyor — istikrarli trendde mevcut mesafe medyana
# yakindir (oran ~1), sicramada medyanin kat kat ustundedir.
#
# Iki kosul BIRLIKTE araniyor:
#   - oran : mesafe kendi medyaninin en az 2 katı mı (sicrama mi)
#   - taban: mutlak olarak en az 1 ATR mi (duz piyasada oran tek basina
#            anlamsiz olurdu; sifira yakin mesafeler arasinda da bir
#            "iki kat" vardir)
UZAMA_KAT = 2.0
UZAMA_TABAN_ATR = 1.0
# Medyan sifira cok yakinsa oran patlar; taban bir medyan varsayiyoruz.
UZAMA_MIN_MEDYAN = 0.25


def _kod_puan(kod: str) -> int:
    return 1 if kod == "bull" else -1 if kod == "bear" else 0


async def _tf_yonu(symbol: str, interval: str) -> Optional[str]:
    """Tek zaman diliminin yon kodu: bull / bear / neutral.

    market_context BILINCLI OLARAK cekilmiyor: fonlama ve acik pozisyon
    verisi zaman dilimine gore degismez, kademe 2'de zaten okundu ve her
    zaman dilimi icin tekrar cekmek ayni bilgiyi ikinci kez odemek olurdu.
    Bu, teyit dilimlerinin yalnizca YAPI/TREND/MOMENTUM/AKIS uzerinden oy
    kullanmasi demek — bir zaman dilimi teyidinden istenen de tam budur.
    """
    from . import analysis
    try:
        snap = await analysis.snapshot(symbol, interval, 300, include_series=False)
        if not snap.get("ok"):
            return None
        return str(analysis.detailed_analysis(snap).get("direction_code") or "neutral")
    except Exception as exc:  # noqa: BLE001
        log.debug("mtf %s %s: %s", symbol, interval, exc)
        return None


async def yon_teyidi(symbol: str, ana_yon: str,
                     ana_kod: str = "") -> Dict[str, Any]:
    """1h + 4h + 15m komitesi. Plan yonu bu komiteyle uyumlu mu?"""
    kodlar: Dict[str, Optional[str]] = {"1h": ana_kod or ("bull" if ana_yon == "LONG" else "bear")}
    sonuc = await asyncio.gather(*(_tf_yonu(symbol, iv) for iv in TEYIT_INTERVAL))
    for iv, kod in zip(TEYIT_INTERVAL, sonuc):
        kodlar[iv] = kod

    puan = 0
    okunan = 0
    for iv, agirlik in YON_AGIRLIK.items():
        kod = kodlar.get(iv)
        if kod is None:
            continue
        okunan += agirlik
        puan += agirlik * _kod_puan(kod)

    plan_isaret = 1 if ana_yon == "LONG" else -1
    net = puan * plan_isaret          # plan yonunden bakinca pozitif = lehte

    if okunan == 0:
        durum, mesaj = "bilinmiyor", "Diğer zaman dilimleri okunamadı."
    elif net >= MIN_HIZA:
        durum = "hizali"
        mesaj = f"4h/1h/15m {ana_yon} yönünde hizalı (net {net}/6)."
    elif net > 0:
        durum = "zayif"
        mesaj = (f"Zaman dilimleri {ana_yon} yönünde ama zayıf hizalı "
                 f"(net {net}/6) — üst zaman dilimi tam desteklemiyor.")
    elif net == 0:
        durum = "yonsuz"
        mesaj = "Zaman dilimleri birbirini nötrlüyor; net bir üst yön yok."
    else:
        durum = "catisiyor"
        ters = [iv for iv in ("4h", "15m")
                if kodlar.get(iv) and _kod_puan(kodlar[iv]) == -plan_isaret]
        mesaj = (f"{' ve '.join(ters) or 'Üst zaman dilimi'} {ana_yon} yönüne "
                 f"TERS (net {net}/6). Kurulum tek zaman diliminde yaşıyor.")

    return {"durum": durum, "net": net, "azami": okunan,
            "dilimler": {iv: kodlar.get(iv) for iv in YON_AGIRLIK},
            "mesaj": mesaj}


# ------------------------------------------------------------------ #
# ZAMANLAMA — 5m + 1m
# ------------------------------------------------------------------ #
def _ema(degerler: List[float], uzunluk: int) -> Optional[float]:
    if len(degerler) < uzunluk:
        return None
    k = 2.0 / (uzunluk + 1.0)
    e = sum(degerler[:uzunluk]) / uzunluk
    for v in degerler[uzunluk:]:
        e = v * k + e * (1 - k)
    return e


def _ema_serisi(degerler: List[float], uzunluk: int) -> List[Optional[float]]:
    """Her bar icin EMA — uzama dagilimini olcmek icin gerekli."""
    out: List[Optional[float]] = [None] * len(degerler)
    if len(degerler) < uzunluk:
        return out
    k = 2.0 / (uzunluk + 1.0)
    e = sum(degerler[:uzunluk]) / uzunluk
    out[uzunluk - 1] = e
    for i in range(uzunluk, len(degerler)):
        e = degerler[i] * k + e * (1 - k)
        out[i] = e
    return out


def _atr(rows: List[list], uzunluk: int = 14) -> Optional[float]:
    if len(rows) < uzunluk + 1:
        return None
    tr: List[float] = []
    for i in range(1, len(rows)):
        try:
            y, d, ok = float(rows[i][2]), float(rows[i][3]), float(rows[i - 1][4])
        except (TypeError, ValueError):
            continue
        tr.append(max(y - d, abs(y - ok), abs(d - ok)))
    return sum(tr[-uzunluk:]) / uzunluk if len(tr) >= uzunluk else None


def _net_yon(kapanislar: List[float], n: int) -> int:
    """Son n barin net yonu: +1 yukari, -1 asagi, 0 yatay."""
    if len(kapanislar) < n + 1:
        return 0
    a, b = kapanislar[-1 - n], kapanislar[-1]
    if a <= 0:
        return 0
    fark = (b / a - 1) * 100.0
    return 1 if fark > 0.03 else -1 if fark < -0.03 else 0


async def giris_zamani(symbol: str, yon: str, giris: float) -> Dict[str, Any]:
    """5m ve 1m uzerinden: SIMDI mi, BEKLE mi, GEC mi?

    Uc soru soruluyor ve ucu de mekanik:
      1. Kucuk zaman diliminin momentumu plan yonunde mi? (5m EMA9/EMA21)
      2. Fiyat 5m ortalamasindan asiri uzaklasmis mi? (ATR cinsinden)
      3. Son dakikalar plan yonunu teyit ediyor mu? (1m net yon)

    Bu bir GIRIS EMRI degil. "Su an fiyat nerede duruyor" sorusunun
    olculmus cevabi; kovalamayi engellemek icin var.
    """
    from . import binance
    try:
        b5, b1 = await asyncio.gather(
            binance.klines(symbol, ZAMAN_INTERVAL, 120),
            binance.klines(symbol, TETIK_INTERVAL, 60),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("mtf zamanlama %s: %s", symbol, exc)
        return {"durum": "bilinmiyor", "mesaj": "Kısa vadeli veri okunamadı."}

    try:
        k5 = [float(k[4]) for k in b5]
        k1 = [float(k[4]) for k in b1]
    except (TypeError, ValueError):
        return {"durum": "bilinmiyor", "mesaj": "Kısa vadeli veri okunamadı."}
    if len(k5) < 30:
        return {"durum": "bilinmiyor", "mesaj": "Kısa vadeli veri yetersiz."}

    fiyat = k5[-1]
    e9, e21 = _ema(k5, 9), _ema(k5, 21)
    atr5 = _atr(b5)
    isaret = 1 if yon == "LONG" else -1

    momentum = 0
    if e9 and e21:
        momentum = 1 if e9 > e21 else -1
    dakika = _net_yon(k1, 5)

    # Uzama, KENDI gecmisiyle kiyaslaniyor (bkz. UZAMA_YUZDELIK notu).
    uzama = None
    uzama_kat = None
    if e21 and atr5 and atr5 > 0:
        uzama = round((fiyat - e21) * isaret / atr5, 2)
        seri = _ema_serisi(k5, 21)
        gecmis = sorted(abs(k5[i] - seri[i]) / atr5
                        for i in range(len(k5)) if seri[i] is not None)
        if len(gecmis) >= 30:
            orta = len(gecmis) // 2
            medyan = (gecmis[orta] if len(gecmis) % 2
                      else (gecmis[orta - 1] + gecmis[orta]) / 2)
            medyan = max(medyan, UZAMA_MIN_MEDYAN)
            uzama_kat = round(abs(uzama) / medyan, 2)

    lehte_momentum = momentum == isaret
    lehte_dakika = dakika == isaret
    asiri = (uzama is not None and uzama > 0
             and uzama >= UZAMA_TABAN_ATR
             and uzama_kat is not None and uzama_kat >= UZAMA_KAT)

    # Geri cekilme tetigi: 5m EMA21. Uzamis fiyatta bu seviye, stopu
    # genisletmeden girilebilecek en yakin makul yer.
    tetik = round(e21, 10) if e21 else None

    if asiri:
        durum = "gec"
        mesaj = (f"Fiyat 5m ortalamasından {uzama} ATR uzakta — normalinin "
                 f"{uzama_kat} katı. Hareket zaten yapılmış; şimdi girmek stopu "
                 f"gereksiz genişletir. "
                 f"{('Geri çekilme seviyesi ' + str(tetik) + '.') if tetik else ''}")
    elif lehte_momentum and lehte_dakika:
        durum = "simdi"
        mesaj = ("5m momentum ve son 5 dakika plan yönünde. Kurulum "
                 "zamanlama açısından şu an geçerli.")
    elif lehte_momentum:
        durum = "bekle"
        mesaj = ("5m momentum plan yönünde ama son dakikalar teyit etmiyor. "
                 "1m kapanış teyidini bekle.")
    else:
        durum = "bekle"
        mesaj = (f"5m momentum plan yönünün TERSİNE. Kısa vade henüz dönmemiş; "
                 f"{('tetik ' + str(tetik)) if tetik else 'teyit'} beklemek daha ucuz.")

    return {
        "durum": durum, "mesaj": mesaj,
        "uzama_atr": uzama, "uzama_kat": uzama_kat, "tetik": tetik,
        "momentum_5m": "lehte" if lehte_momentum else "aleyhte",
        "son_dakikalar": "lehte" if lehte_dakika else ("aleyhte" if dakika else "yatay"),
        "not": "Zamanlama bir giriş emri değil; kovalamayı engellemek için "
               "ölçülmüş bir bağlamdır.",
    }
