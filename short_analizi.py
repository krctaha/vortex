#!/usr/bin/env python3
"""SHORT ANALIZI — "short kaybettiriyor" cumlesini parcalarina ayirir.

NEDEN AYRI BIR MODUL
====================
27.08 backtesti SHORT icin ortalama -0,1008R olctu, %95 GA [-0,185, -0,016].
Sifiri icermeyen tek bulguydu. Ama bu cumle UC farkli seyi ayni anda
kastedebilir ve uc farkli sey gerektirir:

  1. "Short stratejisi bozuk."              -> stratejiyi degistir
  2. "Test doneminde piyasa yukseldi."      -> hicbir sey degistirme, bekle
  3. "Short'lar YANLIS ZAMANDA aciliyor."   -> bir SUZGEC ekle

Ucu ayirmadan verilen her karar korlemesine. Bu modul ayrimi yapiyor.

OLCULEN SEY NE
==============
Her short islemin ACILIS ANINDAKI baglami hesaplayip islemleri kovalara
ayiriyor:

  BTC REJIMI   — canli sistemdeki `btc_rejim._rejim_kodu` ile AYNI kod.
                 "BTC yukselirken altcoin short'u" hipotezini test eder.
  MTF HIZASI   — canli sistemdeki `mtf.yon_teyidi` ile AYNI agirlikli oy
                 (4h=3, 1h=2, 15m=1). "Ust zaman dilimi tersini soyluyordu"
                 hipotezini test eder.

Ikisi de 27.08 backtestinden SONRA sisteme girdi. Yani o rapor,
BUGUNKU sistemin short'larini degil, SUZGECSIZ short'lari olcmustu.
Bu modulun cevapladigi soru tam olarak su:

    "Sistemin BUGUN gosterdigi short'lar (BTC rejimi + MTF suzgecinden
     gecmis olanlar) gecmis veride ne yapardi?"

EK API MALIYETI YOK
===================
4h barlar 1h barlardan, 15m barlar 5m barlardan TURETILIYOR. Ikisi de
zaten indirilmis durumda. Tek ek maliyet CPU.

NE VAAT ETMIYOR
===============
Kovalara ayirmak coklu karsilastirma yaratir: yeterince kova acarsan
biri tesadufen iyi gorunur. Bu yuzden her kovanin n'i ve guven araligi
basiliyor ve rapor "hangisi en iyi" demiyor. Bir kovayi ciddiye almak
icin GA sifirin ustunde olmali VE onceden yazilmis bir gerekcesi
bulunmali. Buradaki iki gerekce onceden yazilmisti: BTC akintisi ve
zaman dilimi catismasi.
"""
from __future__ import annotations

import bisect
import statistics
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------- #
# Bar turetme
# --------------------------------------------------------------------- #
MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def topla(bars: List[list], kaynak: str, hedef: str) -> List[list]:
    """Kucuk mumlari buyuk mumlara toplar (5m->15m, 1h->4h).

    Kova siniri UTC epoch'a gore: Binance de boyle boler, aksi halde
    turetilen mum borsanin mumundan kayar ve olcum sessizce yanlis olur.
    Yalnizca TAM kovalar donuyor — eksik bir 4h mumu, kapanmamis bir mumla
    karar vermek demektir ki bu look-ahead'in tersi bir hata olur.
    """
    adim_k, adim_h = MS[kaynak], MS[hedef]
    if adim_h % adim_k:
        raise ValueError(f"{hedef} {kaynak}'nin tam kati degil")
    gereken = adim_h // adim_k

    kovalar: Dict[int, List[list]] = {}
    for b in bars:
        try:
            acilis = int(b[0])
        except (TypeError, ValueError, IndexError):
            continue
        kovalar.setdefault(acilis - (acilis % adim_h), []).append(b)

    out: List[list] = []
    for acilis in sorted(kovalar):
        grup = sorted(kovalar[acilis], key=lambda x: int(x[0]))
        if len(grup) < gereken:
            continue                      # eksik kova atlanir
        try:
            out.append([
                acilis,
                grup[0][1],                                        # open
                max(float(x[2]) for x in grup),                    # high
                min(float(x[3]) for x in grup),                    # low
                grup[-1][4],                                       # close
                sum(float(x[5]) for x in grup),                    # volume
                acilis + adim_h - 1,                               # close time
                sum(float(x[7]) for x in grup),                    # quote volume
                sum(int(x[8]) for x in grup),                      # trades
                sum(float(x[9]) for x in grup),
                sum(float(x[10]) for x in grup),
                "0",
            ])
        except (TypeError, ValueError, IndexError):
            continue
    return out


# --------------------------------------------------------------------- #
# BTC rejimi — canli sistemin AYNI kodu
# --------------------------------------------------------------------- #
def _atr_yuzde(rows: List[list], uzunluk: int = 14) -> Optional[float]:
    if len(rows) < uzunluk + 1:
        return None
    tr: List[float] = []
    for i in range(len(rows) - uzunluk, len(rows)):
        try:
            y, d, ok = float(rows[i][2]), float(rows[i][3]), float(rows[i - 1][4])
        except (TypeError, ValueError, IndexError):
            continue
        tr.append(max(y - d, abs(y - ok), abs(d - ok)))
    if not tr:
        return None
    son = float(rows[-1][4])
    return (statistics.fmean(tr) / son * 100.0) if son > 0 else None


def btc_rejim_serisi(btc_1h: List[list]) -> List[Tuple[int, str]]:
    """Her BTC barinin KAPANIS anindaki rejim kodu.

    Canli `btc_rejim._rejim_kodu` cagriliyor — kopyalanmiyor. Kopyalansaydi
    iki uygulama zamanla ayrisir ve backtest canli sistemi olcmemis olurdu.
    """
    from app.services.btc_rejim import _rejim_kodu

    seri: List[Tuple[int, str]] = []
    kapanislar = [float(b[4]) for b in btc_1h]
    for i in range(220, len(btc_1h)):
        pencere = kapanislar[:i + 1]
        m1 = ((pencere[-1] / pencere[-2] - 1) * 100.0) if pencere[-2] > 0 else None
        m24 = ((pencere[-1] / pencere[-25] - 1) * 100.0) if len(pencere) > 25 and pencere[-25] > 0 else None
        atr = _atr_yuzde(btc_1h[max(0, i - 20):i + 1])
        kod, _ = _rejim_kodu(pencere[-220:], m1, m24, atr)
        seri.append((int(btc_1h[i][6]), kod))
    return seri


def rejim_at(seri: List[Tuple[int, str]], ms: int) -> Optional[str]:
    if not seri:
        return None
    times = [t for t, _ in seri]
    i = bisect.bisect_right(times, ms) - 1
    return seri[i][1] if 0 <= i < len(seri) else None


# BTC rejimi SHORT icin lehte mi aleyhte mi.
# "guclu_yukari"da altcoin short'u akintiya karsi yuzmektir.
SHORT_ALEYHTE = {"guclu_yukari", "yukari"}
SHORT_LEHTE = {"guclu_asagi", "asagi"}


def btc_kova(kod: Optional[str]) -> str:
    if kod is None:
        return "bilinmiyor"
    if kod in SHORT_ALEYHTE:
        return "BTC yukari (aleyhte)"
    if kod in SHORT_LEHTE:
        return "BTC asagi (lehte)"
    return "BTC yatay"


# --------------------------------------------------------------------- #
# MTF hizasi — canli sistemin AYNI agirlikli oyu
# --------------------------------------------------------------------- #
async def _yon_kodu(symbol: str, interval: str, rows: List[list]) -> Optional[str]:
    """Verilen mumlarla yon kodu. Ag YOK — rows disaridan geliyor."""
    from app.services import analysis
    try:
        snap = await analysis.snapshot(symbol, interval, 0, include_series=False, rows=rows)
        if not snap.get("ok"):
            return None
        return str(analysis.detailed_analysis(snap).get("direction_code") or "neutral")
    except Exception:  # noqa: BLE001
        return None


def _kod_puan(kod: Optional[str]) -> int:
    return 1 if kod == "bull" else -1 if kod == "bear" else 0


async def mtf_net(symbol: str, ana_yon: str, ana_kod: str,
                  bars4h: List[list], bars15m: List[list],
                  ms: int, pencere: int = 300) -> Optional[int]:
    """Canli `mtf.yon_teyidi` ile ayni net puan (-6..+6), plan yonunden.

    KAPANMIS mum sarti: `ms` anindan SONRA kapanan hicbir mum kullanilmaz.
    Bu look-ahead'in en sinsi bicimi — 4h mumun ortasindayken o mumun
    kapanisini gormek, backtest'i sistematik olarak iyimser yapar.
    """
    from app.services.mtf import YON_AGIRLIK

    def kesit(bars: List[list]) -> List[list]:
        i = bisect.bisect_right([int(b[6]) for b in bars], ms)
        return bars[max(0, i - pencere):i]

    r4, r15 = kesit(bars4h), kesit(bars15m)
    # analysis.snapshot 60 barin altinda calismiyor; canli sistem 300
    # veriyor. Az barla hesaplanan yon kodu canlidakiyle AYNI SEY DEGIL,
    # o yuzden esik burada da 60 ve rapor kac islemde okunamadigini
    # sayiyor.
    kod4 = await _yon_kodu(symbol, "4h", r4) if len(r4) >= 60 else None
    kod15 = await _yon_kodu(symbol, "15m", r15) if len(r15) >= 60 else None

    # HICBIR TEYIT DILIMI OKUNAMADIYSA SONUC YOK.
    # Canli kod bu durumda 1h'i yine sayar ama biz sayamayiz: 1h zaten
    # planin KENDI yonudur. Yalnizca ondan olusan bir "hiza" puani,
    # planin kendi kendini onaylamasidir — olcum degil. Boyle bir sayiyi
    # "MTF zayif" kovasina koymak, olcum gibi gorunen bir tahmin uretirdi.
    if kod4 is None and kod15 is None:
        return None

    kodlar: Dict[str, Optional[str]] = {
        "1h": ana_kod or ("bull" if ana_yon == "LONG" else "bear"),
        "4h": kod4, "15m": kod15,
    }
    puan = 0
    for iv, agirlik in YON_AGIRLIK.items():
        kod = kodlar.get(iv)
        if kod is None:
            continue
        puan += agirlik * _kod_puan(kod)
    return puan * (1 if ana_yon == "LONG" else -1)


def mtf_kova(net: Optional[int], min_hiza: int = 3) -> str:
    if net is None:
        return "bilinmiyor"
    if net >= min_hiza:
        return "MTF hizali"
    if net > 0:
        return "MTF zayif"
    if net == 0:
        return "MTF yonsuz"
    return "MTF catisiyor"


# --------------------------------------------------------------------- #
# Rapor
# --------------------------------------------------------------------- #
def _hdr(baslik: str) -> None:
    print("\n" + "=" * 104)
    print(baslik)
    print("=" * 104)


def _kova_raporu(ozetle, line, trades: List[Any], anahtar, baslik: str,
                 sira: Optional[List[str]] = None) -> Dict[str, Any]:
    gruplar: Dict[str, List[Any]] = {}
    for t in trades:
        gruplar.setdefault(anahtar(t), []).append(t)
    adlar = [a for a in (sira or []) if a in gruplar] + \
            sorted(a for a in gruplar if a not in (sira or []))
    print(f"\n{baslik}")
    out: Dict[str, Any] = {}
    for ad in adlar:
        s = ozetle(gruplar[ad], ad)
        out[ad] = s
        print(line(s))
    return out


def rapor(trades: List[Any], ozetle, line, meta: Dict[str, Any]) -> Dict[str, Any]:
    """SHORT islemlerini baglam kovalarina ayirip basar.

    Yildiz (*) = %95 guven araligi sifiri icermiyor. Yildizsiz bir kova
    "iyi" ya da "kotu" DEGIL, "olculemedi" demektir; n kucukse aralik
    zaten her seyi kapsar.
    """
    shortlar = [t for t in trades if t.side == "SHORT"]
    _hdr("SHORT ANALIZI — 'short kaybettiriyor' cumlesi neyin sonucu?")
    for k, v in meta.items():
        print(f"  {k:<26} {v}")
    print(f"\n  toplam SHORT islem        {len(shortlar)}")
    if not shortlar:
        print("\n  SHORT islem yok — kova analizi yapilamaz.\n")
        return {}

    print(line(ozetle(shortlar, "TUM SHORT'LAR")))

    sonuc: Dict[str, Any] = {"hepsi": ozetle(shortlar, "hepsi")}

    # --- 1) BTC rejimine gore ---------------------------------------
    sonuc["btc"] = _kova_raporu(
        ozetle, line, shortlar, lambda t: btc_kova(getattr(t, "btc_rejim", None)),
        "1) ACILIS ANINDAKI BTC REJIMINE GORE\n"
        "   Hipotez: short'lar strateji bozuk oldugu icin degil, BTC\n"
        "   yukselirken acildiklari icin kaybediyor.",
        ["BTC asagi (lehte)", "BTC yatay", "BTC yukari (aleyhte)", "bilinmiyor"])

    # --- 2) MTF hizasina gore ---------------------------------------
    sonuc["mtf"] = _kova_raporu(
        ozetle, line, shortlar, lambda t: mtf_kova(getattr(t, "mtf_net", None)),
        "2) COK ZAMAN DILIMI HIZASINA GORE (4h=3, 1h=2, 15m=1)\n"
        "   Hipotez: kaybeden short'lar ust zaman dilimi tersini soylerken\n"
        "   acilanlar.",
        ["MTF hizali", "MTF zayif", "MTF yonsuz", "MTF catisiyor", "bilinmiyor"])

    # --- 3) BUGUNKU SISTEMIN GORDUGU SHORT'LAR ----------------------
    # Canli sistem su anda ikisini de uyguluyor: MTF catisan kurulum
    # ELENIYOR, BTC catismasi siralamada cezalandiriliyor. Bu kova, 27.08
    # raporunun OLCMEDIGI sey: suzgecten gecmis short'lar.
    def _gecti(t) -> bool:
        net = getattr(t, "mtf_net", None)
        kod = getattr(t, "btc_rejim", None)
        return net is not None and net > 0 and kod not in SHORT_ALEYHTE

    gecen = [t for t in shortlar if _gecti(t)]
    elenen = [t for t in shortlar if not _gecti(t)]
    print("\n3) BUGUNKU SUZGECLER UYGULANSAYDI\n"
          "   Kural: MTF net > 0 VE BTC rejimi short'un aleyhinde degil.\n"
          "   27.08 raporu bu suzgecler EKLENMEDEN once olculmustu.")
    print(line(ozetle(gecen, "suzgecten gecen")))
    print(line(ozetle(elenen, "suzgecin eledigi")))
    sonuc["suzgecli"] = {"gecen": ozetle(gecen, "gecen"),
                         "elenen": ozetle(elenen, "elenen")}

    # --- 4) Aya gore ------------------------------------------------
    from datetime import datetime, timezone

    def ay(t) -> str:
        return f"{datetime.fromtimestamp(t.opened_at / 1000, timezone.utc):%Y-%m}"

    sonuc["ay"] = _kova_raporu(
        ozetle, line, shortlar, ay,
        "4) AYA GORE\n"
        "   Tek bir donemin sonucu mu, yoksa surekli mi? Bir ay digerlerini\n"
        "   tasiyorsa bulgu o aya ait demektir, stratejiye degil.")

    # --- 5) Karar notu ----------------------------------------------
    print("\n" + "-" * 104)
    h, g = sonuc["hepsi"], sonuc["suzgecli"]["gecen"]
    print("OKUMA NOTU")
    print(f"  Tum short'lar        ort {h['avg_net_r']:+.4f}R  n={h['n']}  "
          f"GA[{h['ci95'][0]:+.3f},{h['ci95'][1]:+.3f}]"
          f"{'  *anlamli' if h['significant'] else '  (anlamsiz)'}")
    if g.get("n"):
        print(f"  Suzgecten gecenler   ort {g['avg_net_r']:+.4f}R  n={g['n']}  "
              f"GA[{g['ci95'][0]:+.3f},{g['ci95'][1]:+.3f}]"
              f"{'  *anlamli' if g['significant'] else '  (anlamsiz)'}")
        fark = g["avg_net_r"] - h["avg_net_r"]
        print(f"  Suzgeclerin katkisi  {fark:+.4f}R/islem")
        print("\n  Bir kovayi ciddiye almak icin GA sifirin ustunde olmali VE")
        print("  onceden yazilmis bir gerekcesi bulunmali. En yuksek ortalamayi")
        print("  secmek coklu karsilastirma tuzagidir.")
    else:
        print("  Suzgecten gecen short yok — bu kova yorumlanamaz.")
    print("-" * 104 + "\n")
    return sonuc
