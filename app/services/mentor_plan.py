"""MENTOR PLAN — sistem plani KURAR, kullanici onaylar.

AKISIN YONU NEDEN DEGISTI
-------------------------
Onceki /review akisi sunu yapiyordu: kullanici sembol + yon + giris + stop
yazar, sistem not verir. Yani kullanicidan CEVABI isteyip sonra o cevabi
puanliyorduk. Oysa kullanici zaten "ne yapayim" diye soruyor. Stop sormak
en acik ornegiydi: adam stopun ne olmasi gerektigini bilse zaten sormazdi.

Bu modul akisi ceviriyor:
    eski:  kullanici plan yazar  -> sistem yargilar
    yeni:  sistem plan kurar     -> kullanici karar verir

Cikti tam ve uygulanabilir: yon, giris, stop, hedefler, MIKTAR, kaldirac,
gecersizlik noktasi, teyit sarti ve gerekce. Kullanicinin kafasinda hesap
yapmasi gereken hicbir sey kalmiyor.

NE VAAT ETMIYOR
---------------
Bu bir kazanc vaadi DEGILDIR. Alti backtest'te bu sinyal ailesinin maliyet
sonrasi olculmus bir avantaji cikmadi. Buradaki deger tahminde degil,
MEKANIK ISI USTLENMEKTE: seviyeleri bulmak, riski bolmek, boyutu hesaplamak,
gecersizligi yazmak. Kullanici bunlari isteyken telefonda kafadan yapamaz.
Plan "bunu al" demez; "bir kurulum varsa sekli budur, yoksa yok" der.

KURULUM YOKSA "YOK" DER
-----------------------
Her sorguya bir plan uydurmak en zararli davranis olurdu. Yapi yonsuzse ya
da seviyeler anlamli bir risk/getiri vermiyorsa modul acikca "kurulum yok"
doner. Bos gun de bir cevaptir.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .. import runtime

log = logging.getLogger("vortex.mentor.plan")

# Yapisal stop icin ATR tamponu: seviyeye tam degil, biraz otesine.
# Tam seviyeye koymak, seviyeyi fitilleyip donen fiyatta gereksiz stop yer.
STOP_ATR_TAMPON = 0.25

# Bir kurulumun "var" sayilmasi icin gereken asgari risk/getiri.
# Ikinci hedefe gore olculur, cunku ilk yapisal seviye tanimi geregi yakindir.
MIN_RR = 1.2

# Stop, GURULTU BANDININ disinda olmali.
# Yapisal seviye bazen fiyata cok yakin dusuyor; o zaman risk/getiri
# kagit uzerinde muhtesem gorunuyor (stop %0.6 -> rr 12) ama o stop
# siradan bir dalgalanmada yenir. Daha kotusu: planlari rr'ye gore
# siralayinca EN DAR STOPLULAR basa geliyor, yani en kirilgan kurulumlar
# en iyi gibi sunuluyor. Bu esik onu engelliyor.
# Kullanicinin kendi backtest'i de ayni yone isaret ediyordu:
# ortalama MAE -0.835R, yani stoplar zaten kil payi yetiyordu.
# 06.09: 0.8 -> 1.0. Canlida BTC icin stop %0.41 (~0.9xATR) gecti ve
# rr 9.55 uretti; hedef %4 uzaktayken 1 ATR'den dar stop o hedefe
# varmadan gurultuye yenir. Tam bir ATR makul ve savunulabilir taban.
MIN_STOP_ATR = 1.0      # BIRINCIL kapi: stop en az 1 x ATR uzakta
# Yuzde tabani BILINCLI OLARAK cok dusuk. Sabit bir yuzde esigi yanlis
# araci: BTC'nin 1h ATR'si ~%0.3, SOL'unki ~%1.5. %1 gibi bir taban
# BTC'de gecerli kurulumlari elerdi. Yuzde yalnizca ATR okunamadigi ya
# da sacma cikitigi durumlar icin emniyet supabi.
MIN_STOP_PCT = 0.15


# --------------------------------------------------------------------- #
# MALIYET KAPISI — "maliyet edge'i geciyor" sorununun dogrudan olcusu
#
# ARITMETIK:
#     maliyet_R = (2*komisyon + 2*slipaj) x (giris / stop_mesafesi)
#
# Yani maliyetin R cinsinden agirligi STOP MESAFESINE TERS ORANTILI.
# Stop yarilanirsa maliyet R olarak IKIYE KATLANIR. Bu bir tahmin degil,
# tanim.
#
# NEDEN SHORT TARAFI DAHA COK YANIYOR — olculdu:
# 26.08 kayitlarinda LONG'larin stop mesafesi medyani %2,25, SHORT'larin
# %0,83. Yani short'lar ~2,7 kat dar stopla aciliyor ve bu yuzden R
# cinsinden ~1,9 kat maliyet oduyor (0,147R'ye karsi 0,079R).
#
# Bu sansizlik degil, GEOMETRI: SHORT'un stopu en yakin DIRENCIN ustune
# konuyor. Fiyat aralik tepesindeyken o direnc yakindir. LONG'un stopu
# en yakin DESTEGIN altina konuyor ve o daha uzaktadir. Ayni asimetri
# siralamada da cikmisti (short'un rr'si yapay olarak buyuk gorunuyordu);
# orada rr'yi 4R'de kirparak cozulmustu, kok sebep buydu.
#
# 27.08 backtestinin ozeti: brut +0,0576R, maliyet -0,0858R, net -0,0282R.
# Maliyet brut edge'den BUYUK. Bu kapi tam olarak bunu engelliyor:
# maliyeti olculmus brut edge'in yaninda buyuk kalan kurulum PLAN DEGILDIR.
#
# NE VAAT ETMIYOR: bu kapi kazandirmayi vaat etmiyor. Yalnizca aritmetik
# olarak kaybettirmesi garanti olan kurulumlari eliyor. Kalanlarin
# pozitif oldugu HALA olculmedi.
TAKER_KOMISYON = 0.0005          # Binance USDS-M VIP 0 taker, %0,05
SLIPAJ = 0.0002                  # backtest ile ayni varsayim, %0,02
GIDIS_DONUS = 2 * TAKER_KOMISYON + 2 * SLIPAJ     # %0,14


def maliyet_r(price: float, risk_mesafe: float) -> Optional[float]:
    """Bir islemin gidis-donus maliyeti, R cinsinden."""
    if not price or risk_mesafe <= 0:
        return None
    return GIDIS_DONUS * (price / risk_mesafe)


def maliyet_kapisi(price: float, risk_mesafe: float,
                   tavan: float) -> Optional[str]:
    """Maliyet tavani asiliyorsa SEBEP metni, degilse None.

    tavan <= 0 KAPALI demek. Fonksiyonun kendisi bunu bilmeliydi:
    cagri yerindeki `if mal_tavan > 0` korumasina guvenmek, kapiyi
    baska bir yerden cagiran ikinci bir kod yazildigi gun sifira
    bolme hatasi verirdi (testte oyle oldu).
    """
    if tavan is None or tavan <= 0:
        return None
    m = maliyet_r(price, risk_mesafe)
    if m is None or m <= tavan:
        return None
    yuzde = risk_mesafe / price * 100.0
    gerekli = GIDIS_DONUS / tavan * 100.0
    return (f"Stop yalnız %{yuzde:.2f} uzakta; gidiş-dönüş maliyet "
            f"{m:.3f}R ediyor (tavan {tavan:.3f}R). Ölçülen brüt "
            f"beklenti +0,058R — bu kurulumda maliyet beklentiden büyük, "
            f"yani kâğıt üzerinde bile negatif. Bu stopun en az "
            f"%{gerekli:.2f} uzakta olması gerekirdi.")


def stop_gurultude_mi(risk_mesafe: float, price: float,
                      atr: float) -> Optional[str]:
    """Stop gurultu bandinin icinde mi? Iceriyse SEBEP metni, degilse None.

    NEDEN VAR — olculmus bir sorun:
    Yapisal seviye bazen fiyata cok yakin dusuyor. O zaman risk/getiri
    kagit uzerinde muhtesem gorunuyor (stop %0.63 -> rr 11.5) ama o stop
    siradan bir dalgalanmada yenir. Daha kotusu: planlar rr'ye gore
    siralandigi icin EN DAR STOPLULAR basa geliyor — yani en kirilgan
    kurulumlar en iyi diye sunuluyor. Tam ters tesvik.

    Kullanicinin kendi backtest'i de ayni yone isaret ediyordu:
    ortalama MAE -0.835R, stoplar zaten kil payi yetiyordu.

    ATR birincil olcu cunku her kontratin oynakligi farkli: BTC'nin 1h
    ATR'si ~%0.3, SOL'unki ~%1.5. Sabit yuzde esigi BTC'de gecerli
    kurulumlari elerdi.
    """
    if risk_mesafe <= 0 or price <= 0:
        return None
    yuzde = risk_mesafe / price * 100.0
    if atr and atr > 0:
        kat = risk_mesafe / atr
        if kat < MIN_STOP_ATR:
            return (f"Yapısal stop yalnız {yuzde:.2f}% ({kat:.2f}×ATR) uzakta "
                    f"— gürültü bandının içinde. Bu stop sıradan bir "
                    f"dalgalanmada yenir; kâğıt üzerindeki yüksek "
                    f"risk/getiri aldatıcı olur.")
    elif yuzde < MIN_STOP_PCT:
        return (f"Yapısal stop yalnız {yuzde:.2f}% uzakta ve ATR okunamadı "
                f"— ölçemediğimiz bir riski plan gibi sunmuyoruz.")
    return None


def _boyut(giris: float, stop: float, rcfg: Dict[str, Any]) -> Dict[str, Any]:
    """Pozisyon boyutu — kullanicinin KENDI risk ayarindan.

    qty = kabul edilen risk / birim basina risk
    Ayrica marj tavani uygulanir; ikisinden KUCUK olan secilir.
    Kullaniciya "ne kadar alayim" diye sormanin bir anlami yok, cevap
    ayarlarinda zaten yaziyor.
    """
    risk_usdt = float(rcfg.get("max_risk_per_trade", 6.0))
    marj_tavan = float(rcfg.get("max_position_margin", 60.0))
    kaldirac = int(rcfg.get("default_leverage", 4) or 4)

    birim_risk = abs(giris - stop)
    if birim_risk <= 0 or giris <= 0:
        return {"olculemedi": "stop girise esit"}

    qty_riskten = risk_usdt / birim_risk
    qty_marjdan = marj_tavan * kaldirac / giris
    qty = min(qty_riskten, qty_marjdan)
    notional = qty * giris
    marj = notional / kaldirac
    # Hangi sinir bagladi? Kullanici bunu bilmezse boyutu neden kucuk
    # oldugunu anlamaz.
    baglayan = "risk" if qty_riskten <= qty_marjdan else "marj tavani"

    # LIKIDASYON MESAFESI.
    # Izole modda likidasyon kabaca (100 / kaldirac)% ters harekette gelir:
    # 4x'te ~%25. Stop bundan UZAKTAYSA stop hic calismaz, once likide
    # olursun. Bu yuzden guvenli kaldirac stop yuzdesinden turetiliyor.
    #   guvenli_kaldirac = guvenlik_payi% / stop%
    # ornek: stop %30 ve pay %65 -> 65/30 = 2.16 -> 2x
    # (Ilk yazimda burada fazladan bir x100 vardi; 4x'te %30 stop icin
    #  "216x guvenli" diyordu ve kaldirac hic dusurulmuyordu. Test yakaladi.)
    stop_pct = birim_risk / giris * 100.0
    LIKIDASYON_GUVENLIK_PAYI = 65.0      # likidasyonun %65'inde stop yesin
    guvenli_kaldirac = int(LIKIDASYON_GUVENLIK_PAYI / stop_pct) if stop_pct > 0 else kaldirac
    onerilen_kaldirac = max(1, min(kaldirac, guvenli_kaldirac))

    return {
        "qty": round(qty, 8),
        "notional": round(notional, 2),
        "marj": round(marj, 2),
        "kaldirac": onerilen_kaldirac,
        "istenen_kaldirac": kaldirac,
        "kaldirac_dusuruldu": onerilen_kaldirac < kaldirac,
        "riske_edilen": round(min(risk_usdt, qty * birim_risk), 2),
        "stop_yuzde": round(stop_pct, 2),
        "baglayan_sinir": baglayan,
    }


# --------------------------------------------------------------------- #
# TP MERDIVENI — BES KADEME
#
# NEDEN BES: kullanici kismi cikis kademelerini bildirimde gormek istedi.
# Tek hedef "ya hep ya hic" demek; kademe pozisyonun bir kismini erken
# realize edip kalanini birakmayi mumkun kiliyor.
#
# NEDEN HEPSI YAPISAL DEGIL: fiyatin ustunde her zaman bes tane anlamli
# destek/direnc bulunmuyor. Olmayan seviyeyi UYDURMAK, olcum gibi gorunen
# bir tahmin uretmek olurdu. Bu yuzden iki kaynak var ve her kademe hangi
# kaynaktan geldigini SOYLUYOR:
#   kaynak="yapi" -> gercek bir destek/direnc seviyesi
#   kaynak="R"    -> saf risk katı, yapisal dayanagi yok
#
# DURUSTLUK NOTU: 27.08 backtestinde ortalama MFE +1,02R cikti — yani
# ortalama islem lehine ancak 1R gidiyor. Ust kademelerin (3R, 4R) pratikte
# nadiren dolduruldugu anlamina gelir. Merdiven bunu degistirmiyor;
# yalnizca dolarsa ne olacagini onceden yaziyor. Paylar da buna gore
# on agirlikli: agirlik erken kademelerde.
TP_R_MERDIVEN = (1.0, 1.5, 2.0, 3.0, 4.0)
TP_PAY = (30, 25, 20, 15, 10)
TP_ADET = 5
# Iki kademe birbirine bu kadar yakinsa ayri kademe saymaya degmez.
TP_MIN_ARALIK_R = 0.15


def _tp_merdiveni(yon: str, price: float, risk_mesafe: float,
                  hedefler: List[float]) -> List[Dict[str, Any]]:
    """Yapisal hedefleri R merdiveniyle bes kademeye tamamlar.

    Sira onemli: ONCE yapisal seviyeler yerini aliyor (rr kapisi zaten
    onlara gore hesaplandi, listeden dusmeleri tutarsizlik olurdu), sonra
    aralardaki BOSLUKLAR R katlariyla dolduruluyor. Tersi yapilsaydi —
    once merdiven, sonra yapi — uzaktaki gercek direnc listeden duserdi.
    """
    if risk_mesafe <= 0:
        return []
    ileri = 1.0 if yon == "LONG" else -1.0

    def r_of(fiyat: float) -> float:
        return ((fiyat - price) * ileri) / risk_mesafe

    secilen: List[Dict[str, Any]] = []

    def uzak_mi(r: float) -> bool:
        return all(abs(r - x["r_ham"]) >= TP_MIN_ARALIK_R for x in secilen)

    # 1) Yapisal seviyeler — onceligi bunlarin.
    for h in hedefler:
        if len(secilen) >= TP_ADET:
            break
        r = r_of(float(h))
        if r > 0 and uzak_mi(r):
            secilen.append({"fiyat": float(h), "r_ham": r, "kaynak": "yapi"})

    # 2) Bosluklari R katlariyla doldur. Merdiven biterse birer R uzat —
    #    boylece yapisal hedefler cok uzaktayken bile bes kademe cikiyor.
    r_adayi = list(TP_R_MERDIVEN)
    ek = TP_R_MERDIVEN[-1]
    while len(r_adayi) < TP_ADET * 3:
        ek += 1.0
        r_adayi.append(ek)
    for r in r_adayi:
        if len(secilen) >= TP_ADET:
            break
        if uzak_mi(r):
            secilen.append({"fiyat": price + ileri * r * risk_mesafe,
                            "r_ham": r, "kaynak": "R"})

    if not secilen:
        return []

    secilen.sort(key=lambda x: x["r_ham"])

    # Paylar kademe sayisina gore normalize ediliyor; toplam her zaman 100.
    # On agirlikli: olculen ortalama MFE ~1R oldugu icin agirligin buyugu
    # fiyatin gercekten ulastigi kademelerde duruyor.
    paylar = list(TP_PAY[:len(secilen)])
    toplam = sum(paylar)
    paylar = [round(x * 100.0 / toplam) for x in paylar]
    paylar[-1] += 100 - sum(paylar)

    kademeler: List[Dict[str, Any]] = []
    for i, k in enumerate(secilen):
        mesafe = (k["fiyat"] - price) * ileri
        kademeler.append({
            "no": i + 1,
            "fiyat": round(k["fiyat"], 10),
            "r": round(k["r_ham"], 2),
            "yuzde": round(mesafe / price * 100.0, 2),
            "kaynak": k["kaynak"],
            "pay": paylar[i],
        })
    return kademeler


async def plan_kur(symbol: str, interval: str = "1h") -> Dict[str, Any]:
    """Tek sembol icin TAM plan. Kurulum yoksa acikca 'yok' doner."""
    from . import analysis, rsi_context, binance

    symbol = symbol.upper().strip()
    snap = await analysis.snapshot(symbol, interval, 500, include_series=False)
    if not snap.get("ok"):
        return {"ok": False, "symbol": symbol, "hata": snap.get("error", "analiz uretilemedi")}
    try:
        snap["context"] = await analysis.market_context(symbol)
    except Exception:  # noqa: BLE001
        pass
    d = analysis.detailed_analysis(snap)

    price = float(snap.get("price") or 0)
    ind = snap.get("indicators") or {}
    atr = float(ind.get("atr14") or 0)
    if price <= 0:
        return {"ok": False, "symbol": symbol, "hata": "fiyat okunamadi"}

    # --- YONU SISTEM SECER, kullaniciya sormaz ------------------------
    kod = str(d.get("direction_code") or "neutral")
    if kod == "bull":
        yon = "LONG"
    elif kod == "bear":
        yon = "SHORT"
    else:
        return {
            "ok": True, "symbol": symbol, "interval": interval, "var_mi": False,
            "price": price,
            "sebep": "Yapı, momentum ve akış tek yönde hizalanmıyor. Yönsüz "
                     "piyasada kurulum uydurmak en pahalı hatadır.",
            "ozet": d.get("summary"),
            "kalite": d.get("quality"),
        }

    # --- SEVIYELER ----------------------------------------------------
    seviyeler = (snap.get("levels") or {}).get("support_resistance") or []
    destekler = sorted((float(x["price"]) for x in seviyeler
                        if float(x.get("price") or 0) < price), reverse=True)
    dirençler = sorted(float(x["price"]) for x in seviyeler
                       if float(x.get("price") or 0) > price)

    if yon == "LONG":
        taban = destekler[0] if destekler else (price - 1.5 * atr if atr else None)
        stop = (taban - STOP_ATR_TAMPON * atr) if (taban and atr) else taban
        hedefler = dirençler[:2] or ([price + 2 * atr] if atr else [])
    else:
        tavan = dirençler[0] if dirençler else (price + 1.5 * atr if atr else None)
        stop = (tavan + STOP_ATR_TAMPON * atr) if (tavan and atr) else tavan
        hedefler = list(reversed(destekler[-2:])) if destekler else \
            ([price - 2 * atr] if atr else [])
        hedefler = sorted(hedefler, reverse=True)[:2]

    if not stop or not hedefler:
        return {"ok": True, "symbol": symbol, "interval": interval, "var_mi": False,
                "price": price,
                "sebep": "Yapısal seviye bulunamadı; stop veya hedef "
                         "dayanaksız kalırdı.",
                "ozet": d.get("summary")}

    risk_mesafe = (price - stop) if yon == "LONG" else (stop - price)

    # GURULTU KAPISI
    gurultu = stop_gurultude_mi(risk_mesafe, price, atr)
    if gurultu:
        return {"ok": True, "symbol": symbol, "interval": interval,
                "var_mi": False, "price": price, "yon_egilimi": yon,
                "sebep": gurultu, "ozet": d.get("summary")}

    # MALIYET KAPISI — gurultu kapisindan AYRI bir sey.
    # Gurultu kapisi "bu stop yenir mi" diye soruyor (oynakliga gore).
    # Maliyet kapisi "bu stop mesafesinde komisyon edge'i yer mi" diye
    # soruyor (aritmetige gore). Bir kurulum gurultuyu gecip maliyette
    # kalabilir: BTC gibi dusuk ATR'li bir kontratta %0,4'luk stop
    # 1.5xATR olabilir ama maliyeti 0,35R eder.
    mal_tavan = float(runtime.get().get("mentor_tarama", {}).get("maliyet_tavan_r", 0.08))
    mal_sebep = maliyet_kapisi(price, risk_mesafe, mal_tavan) if mal_tavan > 0 else None
    if mal_sebep:
        return {"ok": True, "symbol": symbol, "interval": interval,
                "var_mi": False, "price": price, "yon_egilimi": yon,
                "sebep": mal_sebep, "ozet": d.get("summary")}

    if risk_mesafe <= 0:
        return {"ok": True, "symbol": symbol, "interval": interval, "var_mi": False,
                "price": price,
                "sebep": "Fiyat yapısal stop seviyesinin yanlış tarafında — "
                         "hareket zaten yapılmış olabilir.",
                "ozet": d.get("summary")}

    son_hedef = hedefler[-1]
    odul_mesafe = (son_hedef - price) if yon == "LONG" else (price - son_hedef)
    rr = odul_mesafe / risk_mesafe if risk_mesafe > 0 else 0.0
    if odul_mesafe <= 0 or rr < MIN_RR:
        return {"ok": True, "symbol": symbol, "interval": interval, "var_mi": False,
                "price": price,
                "sebep": f"Risk/getiri {rr:.2f} — {MIN_RR} eşiğinin altında. "
                         f"Yapı doğru yönde ama fiyat, hedefe göre girişe çok "
                         f"yakın; kovalanmış olabilir.",
                "ozet": d.get("summary"), "yon_egilimi": yon}

    boyut = _boyut(price, stop, runtime.get()["risk"])

    # --- RSI baglami (ek istek maliyeti dusuk, karar degeri yuksek) ----
    rsi_not = None
    rows: List[list] = []
    try:
        rows = await binance.klines(symbol, interval, 220)
        fr = None
        try:
            pi = await binance.premium_index(symbol)
            fr = float(pi.get("lastFundingRate", 0) or 0) * 10_000
        except Exception:  # noqa: BLE001
            pass
        if rows and len(rows) >= 60:
            c = rsi_context.classify(symbol, rows, fr)
            if c:
                rsi_not = {
                    "rsi": c.get("rsi"), "bolge": c.get("zone"),
                    "okuma": rsi_context.verdict_label(c.get("zone", ""),
                                                       c.get("verdict", "")),
                    "funding_bp": round(fr, 2) if fr is not None else None,
                }
    except Exception:  # noqa: BLE001
        pass

    # --- BTC AKINTISI -------------------------------------------------
    # EKSIK OLAN UCUNCU BOYUT.
    # Buraya kadarki her sey sembolu TEK BASINA olcuyordu: kendi yapisi,
    # kendi RSI'i, kendi order flow'u. Ama bir altcoinin bir saatlik
    # getirisinin buyuk kismi o coinle ilgili degil, BTC'yle ilgili.
    # BTC %2 yukari giderse yuksek betali bir altcoin %3-4 yukari gider
    # ve o coinin "asagi baski" gosteren kendi gostergeleri bunu
    # durdurmaz. Sistem altcoin short'unu BTC'ye karsi aciyor ve bunu
    # ne kendisi biliyor ne de soyluyordu.
    #
    # Maliyeti neredeyse sifir: sembolun barlari zaten yukarida cekildi,
    # BTC'ninkiler tarama turu boyunca onbellekli.
    btc_uyum = None
    btc_rej = None
    try:
        from . import btc_rejim
        btc_rej = await btc_rejim.rejim(interval)
        if btc_rej.get("ok") and rows and symbol != btc_rejim.BTC:
            btc_rows = await btc_rejim.btc_barlari(interval, 220)
            kor = btc_rejim.korelasyon_beta(
                [float(k[4]) for k in rows[-btc_rejim.KORELASYON_BAR:]],
                [float(k[4]) for k in btc_rows[-btc_rejim.KORELASYON_BAR:]])
            btc_uyum = btc_rejim.uyum(yon, btc_rej["kod"], kor,
                                      btc_rej.get("genislik"))
    except Exception as exc:  # noqa: BLE001
        log.debug("btc uyumu hesaplanamadi %s: %s", symbol, exc)

    # --- DURUST UYARILAR ---------------------------------------------
    uyarilar: List[str] = []
    # BTC ile catisma en ust sirada: diger uyarilar kurulumun kendi
    # kirilganligini anlatiyor, bu ise kurulumun DISINDAKI en buyuk
    # tek etkeni.
    if btc_uyum and btc_uyum["durum"] == "carpisiyor":
        uyarilar.append(btc_uyum["mesaj"])
    if snap.get("last_bar_closed") is False:
        uyarilar.append(f"{interval} mumu henüz kapanmadı; teyit geri dönebilir.")
    if str(d.get("quality") or "").startswith("zayıf"):
        uyarilar.append("Veri aileleri zayıf hizalı — plan kırılgan.")
    if boyut.get("kaldirac_dusuruldu"):
        uyarilar.append(
            f"Stop {boyut['stop_yuzde']}% uzakta; kaldıraç "
            f"{boyut['istenen_kaldirac']}x yerine {boyut['kaldirac']}x "
            f"olmalı, yoksa stop likidasyondan sonra gelir.")
    if rsi_not and rsi_not.get("bolge") in ("overbought", "oversold"):
        uyarilar.append(f"RSI {rsi_not['rsi']} aşırı bölgede — {rsi_not['okuma']}.")

    # --- HEDEFLER, HER BIRI KENDI R DEGERIYLE -------------------------
    # "R" = bir islemde riske ettigin miktar; stop mesafesinin kendisi.
    # Hedefi R cinsinden yazmak, fiyat rakamindan cok daha okunakli:
    # "TP2 = 2.4R" demek "stopa carparsan kaybettiginin 2.4 katini
    # kazanirsin" demek. Kullanici sordugu icin arayuzde de aciklaniyor.
    hedef_detay = _tp_merdiveni(yon, price, risk_mesafe, hedefler)

    # --- MINI GRAFIK ---------------------------------------------------
    # EK MALIYET YOK: bu mumlar RSI baglami icin zaten cekildi. Son 60
    # kapanis, arayuzde tek satirlik bir cizgiye donusuyor. Amaci karar
    # vermek degil, KURULUMU YERINE OTURTMAK: %8 hedef, fiyat zaten iki
    # gundur duz giderken bambaska bir sey ifade eder.
    seri: List[float] = []
    try:
        for k in rows[-60:]:
            seri.append(float(k[4]))
    except (TypeError, ValueError, IndexError):
        seri = []

    return {
        "ok": True, "symbol": symbol, "interval": interval, "var_mi": True,
        "yon": yon, "price": price,
        "giris": round(price, 10),
        "stop": round(stop, 10),
        "stop_r_yuzde": round(risk_mesafe / price * 100.0, 2),
        # MALIYET HER PLANDA GORUNUR.
        # Kapiyi gecmis olmasi maliyetin sifir oldugu anlamina gelmiyor.
        # 0,05R'lik bir maliyet, olculen +0,058R brut beklentinin
        # neredeyse tamami demek — bunu gizlemek kullaniciya kurulumu
        # oldugundan iyi gostermek olurdu.
        "maliyet_r": round(maliyet_r(price, risk_mesafe) or 0.0, 3),
        "hedefler": [round(h, 10) for h in hedefler],
        "hedef_detay": hedef_detay,
        "seri": [round(x, 10) for x in seri],
        "rr": round(rr, 2),
        "boyut": boyut,
        "kalite": d.get("quality"),
        "yon_kodu": kod,
        "ozet": d.get("summary"),
        # HATA DUZELTMESI (06.09): burada listeleri yon'e gore ters
        # ceviriyordum. Ama detailed_analysis'in evidence_for'u ZATEN
        # kendi buldugu ana yone gore uretiliyor ve plan yonu de o ana
        # yonden geliyor (yon = direction_code). Bir kez daha cevirince
        # iki kez ters dondu: SHORT planinda "ana yonu destekliyor"
        # maddeleri KARSI GORUS olarak, "celisiyor" maddeleri GEREKCE
        # olarak gorunuyordu. Cevirme yok.
        "gerekce": d.get("evidence_for") or [],
        "karsi_gorus": d.get("evidence_against") or [],
        "teyit": d.get("confirmation"),
        "gecersizlik": d.get("invalidation"),
        "kor_nokta": (d.get("blind_spots") or [])[:4],
        "rsi": rsi_not,
        "btc": btc_uyum,
        "btc_rejim": ({"kod": btc_rej.get("kod"), "etiket": btc_rej.get("etiket"),
                       "degisim_24s": btc_rej.get("degisim_24s")}
                      if btc_rej and btc_rej.get("ok") else None),
        "uyarilar": uyarilar,
        "not": "Bu bir emir değil, yapılandırılmış bir plan. Kanıtlanmış "
               "kâr avantajı iddia edilmiyor; mekanik iş (seviye, boyut, "
               "geçersizlik) senin yerine yapıldı.",
    }


# rr'nin siralamadaki agirligini SINIRLAYAN tavan.
#
# Neden: rr = odul / risk. Risk paydada oldugu icin stop daraldikca rr
# patlar. 12R'lik bir kurulum 4R'likten uc kat iyi degildir; neredeyse her
# zaman stopu uc kat dardir, yani uc kat kirilgandir. Kirpmadan siralarsak
# liste sistematik olarak EN DAR STOPLU kurulumlarla doluyor.
# Kullanicinin kendi backtest'i de ayni yone isaret ediyordu: ortalama
# MAE -0.835R, stoplar zaten kil payi yetiyordu.
RR_TAVAN = 4.0


# BTC akintisina karsi duran plana verilen siralama cezasi.
#
# Neden ceza var ama VETO yok: korelasyon nedensellik degil ve beta sabit
# degil. Sakin donemde 0.9 olan korelasyon, coine ozel bir haberde 0.2'ye
# duser — o yuzden "BTC yukari, short'lari tamamen ele" demek yanlis
# olurdu; bazen dogru islem tam da odur. Ama esit sartlarda akintiya karsi
# duran kurulumu listenin basina koymak da yanlis. Ceza tam olarak bu iki
# yanlisin arasinda duruyor: gorunur kal, ama once gelme.
BTC_CATISMA_CEZASI = 0.6


def _siralama_puani(p: Dict[str, Any]) -> float:
    """Kucuk daha iyi. Once veri kalitesi, sonra kirpilmis risk/getiri,
    sonra BTC akintisiyla catisma."""
    kalite = str(p.get("kalite") or "")
    kalite_ceza = 0.0 if kalite.startswith("güçlü") else 0.5 if kalite.startswith("orta") else 1.2
    rr = min(float(p.get("rr") or 0.0), RR_TAVAN)
    btc = p.get("btc") or {}
    btc_ceza = BTC_CATISMA_CEZASI if btc.get("durum") == "carpisiyor" else 0.0

    # Zaman dilimi hizasi ODUL, hizasizlik CEZA. Catisanlar zaten
    # listeden eleniyor; burada kalan ayrim "tam hizali" ile "zayif"
    # arasinda.
    zd = (p.get("zaman_dilimleri") or {}).get("durum")
    zd_etki = -0.35 if zd == "hizali" else 0.25 if zd in ("zayif", "yonsuz") else 0.0

    # Zamanlama: uzamis fiyattan girmek kovalamaktir; listenin basinda
    # olmamali. "Simdi" durumu kucuk bir odul aliyor.
    gz = (p.get("giris_zamani") or {}).get("durum")
    gz_etki = -0.20 if gz == "simdi" else 0.40 if gz == "gec" else 0.0

    return kalite_ceza + btc_ceza + zd_etki + gz_etki - rr / RR_TAVAN


async def gunun_planlari(limit: int = 8, interval: str = "1h",
                         evren: int = 120, min_hacim_m: float = 0.0) -> Dict[str, Any]:
    """Evreni tarar ve KURULUMU OLAN sembolleri risk/getiriye gore siralar.

    Kullanici sembol secmek zorunda degil: "bugun ne var" sorusunun
    cevabi bu. Hicbir sey bulunmazsa bos liste doner ve bu da bir cevaptir.
    """
    # ADAY SECIMI ARTIK KUTUPHANEDEN.
    # Onceden burada dogrudan hacme gore ilk N sembol aliniyordu. O kural
    # yapisal olarak hep ayni buyuk coinleri secer: hareket kucuk bir
    # kontratta baslarsa liste onu HIC gormez. market_library butun evreni
    # (~500) ucuza izliyor ve her sembolun KENDI tabanina gore hacim
    # genislemesi + 1 saatlik ivme + bant konumuna gore siraliyor; burada
    # yalnizca o siralamanin basi pahali analize giriyor.
    from . import market_library
    semboller = market_library.adaylar(n=evren, min_hacim_m=min_hacim_m)
    if not semboller:
        # Kutuphane henuz dolmadi (ilk acilis). Eski yol emniyet supabi.
        from . import binance
        tickers = await binance.ticker_24h()
        perp = {r["symbol"] for r in await binance.perpetual_symbols()}
        uygun = [t for t in tickers if t.get("symbol") in perp
                 and float(t.get("quoteVolume", 0) or 0) >= min_hacim_m * 1e6]
        uygun.sort(key=lambda t: -float(t.get("quoteVolume", 0) or 0))
        semboller = [t["symbol"] for t in uygun[:evren]]

    sem = asyncio.Semaphore(4)
    bulunan: List[Dict[str, Any]] = []
    bakilan = 0

    async def bak(sym: str) -> None:
        nonlocal bakilan
        async with sem:
            try:
                p = await plan_kur(sym, interval)
                bakilan += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("plan %s: %s", sym, exc)
                return
        if p.get("ok") and p.get("var_mi"):
            bulunan.append(p)

    await asyncio.gather(*(bak(s) for s in semboller))

    # --- KADEME 3: COK ZAMAN DILIMI ------------------------------------
    # Yalnizca KURULUMU OLAN sembollere uygulaniyor. Ek zaman dilimi
    # basina bir snapshot ve zamanlama icin iki ucuz kline gerekiyor;
    # 150 sembolun hepsine uygulamak tarama basina ~4500 agirlik ederdi,
    # dakikalik butce 2400. Kurulumu olan sembol sayisi tipik olarak
    # 10-15 oldugu icin bu kademe ~300 agirlige iniyor.
    # Ayni huni mantigi, bir kat daha: ucuzdan pahaliya, genisten dara.
    if bulunan:
        from . import mtf

        async def zenginlestir(p: Dict[str, Any]) -> None:
            try:
                # Validate required fields before creating either coroutine.
                symbol, direction, entry = p["symbol"], p["yon"], p["giris"]
                teyit, zaman = await asyncio.gather(
                    mtf.yon_teyidi(symbol, direction, p.get("yon_kodu", "")),
                    mtf.giris_zamani(symbol, direction, entry),
                )
                p["zaman_dilimleri"] = teyit
                p["giris_zamani"] = zaman
                # Uyarilar TEK YERDE toplaniyor ki arayuz her birini ayri
                # ayri okumak zorunda kalmasin.
                if teyit.get("durum") == "catisiyor":
                    p.setdefault("uyarilar", []).insert(0, teyit["mesaj"])
                if zaman.get("durum") == "gec":
                    p.setdefault("uyarilar", []).append(zaman["mesaj"])
            except Exception as exc:  # noqa: BLE001
                log.debug("mtf zenginlestirme %s: %s", p.get("symbol"), exc)

        sem2 = asyncio.Semaphore(4)

        async def sirali(p: Dict[str, Any]) -> None:
            async with sem2:
                await zenginlestir(p)

        await asyncio.gather(*(sirali(p) for p in bulunan))

        # UST ZAMAN DILIMIYLE CATISAN KURULUM ELENIYOR.
        # Bu, BTC catismasindan FARKLI ve daha sert bir durum: orada
        # baska bir varligin akintisi soz konusuydu, burada AYNI varligin
        # daha guvenilir zaman dilimi tam tersini soyluyor. "1h short
        # diyor ama 4h guclu yukari" bir kurulum degil, bir gurultudur.
        eski_sayi = len(bulunan)
        bulunan = [p for p in bulunan
                   if (p.get("zaman_dilimleri") or {}).get("durum") != "catisiyor"]
        elenen = eski_sayi - len(bulunan)
    else:
        elenen = 0

    # --- YON SUZGECI --------------------------------------------------
    # Ayarlardan geliyor, varsayilani LONG. Gerekce koda degil OLCUME
    # dayaniyor: 27.08 backtestinde SHORT'un ortalamasi -0,1008R ve %95
    # guven araligi [-0,185, -0,016] sifiri icermiyordu. Elenenleri
    # sayiyoruz — arayuz "kac tane SHORT gizlendi" diyebilsin, cunku
    # sessizce eleyip sayi vermemek kullaniciyi piyasa hakkinda yaniltir.
    yon_suzgec = str(runtime.get().get("mentor_tarama", {}).get("yon", "LONG")).upper()
    yon_elenen = 0
    if yon_suzgec in ("LONG", "SHORT"):
        onceki = len(bulunan)
        bulunan = [x for x in bulunan if x.get("yon") == yon_suzgec]
        yon_elenen = onceki - len(bulunan)

    longlar = [x for x in bulunan if x.get("yon") == "LONG"]
    shortlar = [x for x in bulunan if x.get("yon") == "SHORT"]
    for grup in (longlar, shortlar):
        grup.sort(key=_siralama_puani)

    # LISTE YON DENGELI KURULUYOR.
    #
    # SORUN NEYDI: liste yalnizca rr'ye gore siralaniyordu ve rr YONE
    # GORE SIMETRIK DEGIL. Fiyat 24 saatlik aralıgin ustlerindeyse en
    # yakin direnc yakin (short'ta stop dar), destekler uzak (short'ta
    # hedef genis) -> short'un rr'si buyuk cikar. Ayni anda long'un
    # stopu genis, hedefi yakin -> rr kucuk. Sonuc: motor 50/50 bull-bear
    # bulsa bile ILK SEKIZ hep short olur ve kullanici sistemin "surekli
    # short buldugunu" gorur. Bu bir piyasa okumasi degil, siralama
    # kriterinin yan etkisiydi.
    #
    # Cozum iki parcali:
    #   1) _siralama_puani rr'yi 4R'de kirpiyor (asagida) — 12R'lik bir
    #      kurulum 4R'likten uc kat iyi degildir, genelde stopu uc kat
    #      dardir.
    #   2) Liste iki yondan SIRAYLA dolduruluyor. Tek yonde kurulum varsa
    #      liste yine tek yonlu kalir (piyasa gercekten tek tarafliysa
    #      onu gizlemek yanlis olurdu) ama bunu sayilarla soyluyoruz.
    # Iki listeden SIRAYLA aliyoruz. Hangi taraf once basliyorsa o
    # tarafin en iyi plani daha iyi puanli olan taraftir; sonrasi
    # donusumlu. Tek yonde kurulum varsa liste tek yonlu kalir — piyasa
    # gercekten tek tarafliysa onu gizlemek yanlis olurdu; ama bunu
    # asagidaki long_sayisi / short_sayisi ile acikca soyluyoruz.
    sirali: List[Dict[str, Any]] = []
    i = j = 0
    once_long = bool(longlar) and (not shortlar or
                                   _siralama_puani(longlar[0]) <= _siralama_puani(shortlar[0]))
    sira_long = once_long
    while len(sirali) < limit and (i < len(longlar) or j < len(shortlar)):
        if sira_long and i < len(longlar):
            sirali.append(longlar[i]); i += 1
        elif not sira_long and j < len(shortlar):
            sirali.append(shortlar[j]); j += 1
        elif i < len(longlar):
            sirali.append(longlar[i]); i += 1
        elif j < len(shortlar):
            sirali.append(shortlar[j]); j += 1
        sira_long = not sira_long

    return {"ok": True, "bakilan": bakilan, "bulunan": len(bulunan),
            "zd_elenen": elenen,
            "yon_suzgec": yon_suzgec, "yon_elenen": yon_elenen,
            "long_sayisi": len(longlar), "short_sayisi": len(shortlar),
            "interval": interval, "planlar": sirali[:limit]}


# =================================================================== #
# ACIK POZISYON DENETIMI — "hala gecerli mi?"
#
# NEDEN AYRI BIR SEY:
# Plan kurmak ile acik pozisyonu denetlemek farkli iki istir. Plan
# kurarken soru "girmeli miyim"; acikken soru "TEZIM HALA AYAKTA MI".
# Ikincisi cok daha sik ihmal edilir ve daha pahaliya mal olur: insan
# girerken dikkatli, tutarken umutludur.
#
# Denetim uc bagimsiz kaynaga bakiyor ve UCU DE OLCUM:
#   1. Ana analiz hala ayni yonu mu gosteriyor
#   2. Ust zaman dilimleri hala hizali mi (4h/1h/15m)
#   3. BTC akintisi lehte mi aleyhte mi
#
# Cikti bir EMIR degil. "Kapat" demiyor; "tezinin hangi ayagi kirildi"
# diyor. Kapatma karari kullanicinin, cunku pozisyonu tutmanin
# gerekcesi bu ucunun disinda da olabilir.
# =================================================================== #
async def pozisyon_denetimi(symbol: str, side: str, entry: float,
                            stop: Optional[float] = None,
                            interval: str = "1h") -> Dict[str, Any]:
    from . import analysis, btc_rejim, mtf

    symbol = symbol.upper().strip()
    kirik: List[str] = []
    saglam: List[str] = []
    ayrinti: Dict[str, Any] = {}

    # --- 1) Ana analiz hala ayni yonu mu gosteriyor? ------------------
    try:
        snap = await analysis.snapshot(symbol, interval, 300, include_series=False)
        if snap.get("ok"):
            d = analysis.detailed_analysis(snap)
            kod = str(d.get("direction_code") or "neutral")
            beklenen = "bull" if side == "LONG" else "bear"
            ayrinti["analiz"] = {"kod": kod, "ozet": d.get("summary"),
                                 "kalite": d.get("quality")}
            if kod == beklenen:
                saglam.append(f"{interval} analizi hâlâ {side} yönünde.")
            elif kod == "neutral":
                kirik.append(f"{interval} analizi artık yönsüz — girişteki tez zayıfladı.")
            else:
                kirik.append(f"{interval} analizi artık TERS yönde. Giriş gerekçen "
                             f"artık geçerli değil.")
            ayrinti["fiyat"] = snap.get("price")
    except Exception as exc:  # noqa: BLE001
        log.debug("denetim analiz %s: %s", symbol, exc)

    # --- 2) Ust zaman dilimleri ---------------------------------------
    try:
        zd = await mtf.yon_teyidi(symbol, side)
        ayrinti["zaman_dilimleri"] = zd
        if zd["durum"] == "catisiyor":
            kirik.append(zd["mesaj"])
        elif zd["durum"] == "hizali":
            saglam.append(zd["mesaj"])
    except Exception as exc:  # noqa: BLE001
        log.debug("denetim mtf %s: %s", symbol, exc)

    # --- 3) BTC akintisi ----------------------------------------------
    try:
        rej = await btc_rejim.rejim(interval)
        if rej.get("ok") and symbol != btc_rejim.BTC:
            from . import binance
            rows = await binance.klines(symbol, interval, btc_rejim.KORELASYON_BAR)
            btc_rows = await btc_rejim.btc_barlari(interval, btc_rejim.KORELASYON_BAR)
            kor = btc_rejim.korelasyon_beta([float(k[4]) for k in rows],
                                            [float(k[4]) for k in btc_rows])
            u = btc_rejim.uyum(side, rej["kod"], kor, rej.get("genislik"))
            ayrinti["btc"] = u
            if u["durum"] == "carpisiyor":
                kirik.append(u["mesaj"])
            elif u["durum"] == "destekliyor":
                saglam.append("BTC akıntısı pozisyonun yönünde.")
    except Exception as exc:  # noqa: BLE001
        log.debug("denetim btc %s: %s", symbol, exc)

    # --- Acik R: stop varsa pozisyonun su anki R degeri ----------------
    acik_r = None
    fiyat = ayrinti.get("fiyat")
    if stop and fiyat and entry:
        risk = abs(entry - stop)
        if risk > 0:
            yon = 1.0 if side == "LONG" else -1.0
            acik_r = round((float(fiyat) - entry) * yon / risk, 2)
    ayrinti["acik_r"] = acik_r
    if stop is None:
        kirik.append("Borsada stop emri yok — bu pozisyonun tanımlı bir "
                     "geçersizlik noktası yok ve R hesaplanamıyor.")

    # KARAR. Esikler bilincli olarak kaba: iki bagimsiz ayak kirildiysa
    # tez artik ayakta degildir. Tek ayak bir uyaridir, iki ayak bir
    # bulgudur.
    if len(kirik) >= 2:
        durum, baslik = "tez_bozuldu", "Giriş tezinin birden fazla ayağı kırıldı"
    elif kirik:
        durum, baslik = "zayifladi", "Tez zayıfladı"
    else:
        durum, baslik = "uygun", "Giriş tezi hâlâ ayakta"

    return {"durum": durum, "baslik": baslik, "kirik": kirik,
            "saglam": saglam, "ayrinti": ayrinti,
            "not": "Bu bir kapatma emri değil. Pozisyonu tutmanın gerekçesi "
                   "bu üç ölçümün dışında da olabilir; karar sende."}


# =================================================================== #
# ONBELLEK VE ARKA PLAN TARAMASI
#
# NEDEN VAR — olculmus bir sorun:
# gunun_planlari() 150 sembole derin analiz uyguluyor, yaklasik 1300
# agirlik ve 60-90 saniye. Arayuz bunu SAYFA ACILISINDA cagiriyordu.
# Kullanici sol menuden baska bir sayfaya gecip geri donunce tarayici tam
# sayfa yenilemesi yapiyor, JavaScript sifirdan basliyor ve tarama BASTAN
# calisiyordu. Bes gezinme = bes tam tarama = ~6500 agirlik. Binance'in
# dakikalik butcesi 2400; radar ve senkron da ayni butceden yiyor.
#
# Kullanicinin iki gozlemi de dogruydu ve ayni koke bagliydi:
#   "sistem yoruluyor gibi"          -> agirlik butcesi tukeniyordu
#   "her sayfa gecisinde veri kayip" -> sonuc yalnizca tarayicida duruyordu
#
# Cozum: sonucu SUNUCUDA tut. Arka plan gorevi belirli araliklarla tarayip
# onbellege yaziyor; arayuz yalnizca OKUYOR. Sayfa gecisi artik hicbir
# tarama tetiklemiyor, cevap aninda geliyor ve on gezinme ile bir gezinme
# ayni maliyete sahip: sifir.
#
# Kilit neden gerekli: onbellek sogukken es zamanli iki istek gelirse
# ikisi de taramaya baslardi. Kilit sayesinde ikincisi birincinin sonucunu
# bekliyor — ayni is iki kez yapilmiyor.
# =================================================================== #
ONBELLEK_TTL_MS = 180_000          # 3 dakika
ARKA_PLAN_ARALIK_SN = 180
ARKA_PLAN_INTERVAL = "1h"          # arka planda sicak tutulan varsayilan

_onbellek: Dict[str, Dict[str, Any]] = {}
_kilitler: Dict[str, asyncio.Lock] = {}
_arka_task: Optional[asyncio.Task] = None
_isinma: Dict[str, asyncio.Task] = {}


def _evren_ayari() -> tuple:
    """Evren buyuklugu ARTIK ISTEKTEN GELMIYOR — bu bir sistem karari.

    OLCULMUS HATA (08.09): arka plan gorevi onbellegi evren=120 ile
    isitiyordu, arayuz ise evren=150 istiyordu. Onbellek anahtari evreni
    de iceriyordu, dolayisiyla ARAYUZ HICBIR ZAMAN SICAK ONBELLEGE
    ISABET ETMIYORDU: her sayfa acilisi tam bir taramayi tetikliyordu.
    Cok zaman dilimi katmani eklendikten sonra o tarama cok daha
    pahalilastı ve canli sunucuda istek dakikalarca asili kaldi —
    kullanicinin gordugu sey "Bugun Ne Var calismiyor" oldu.

    Ders: ayni seyi iki yerde ayarlanabilir yapmak, er ya da gec ikisinin
    ayrisması demektir. Artik tek kaynak var: mentor_tarama ayarlari.
    """
    c = runtime.get().get("mentor_tarama", {})
    return int(c.get("evren", 150)), float(c.get("min_hacim_m", 0.5))


def _kilit(anahtar: str) -> asyncio.Lock:
    if anahtar not in _kilitler:
        _kilitler[anahtar] = asyncio.Lock()
    return _kilitler[anahtar]


def onbellek_durumu() -> Dict[str, Any]:
    from .. import db
    now = db.now_ms()
    return {a: {"yas_sn": int((now - v["ts"]) / 1000),
                "bulunan": v["veri"].get("bulunan"),
                "bakilan": v["veri"].get("bakilan")}
            for a, v in _onbellek.items()}


async def _hesapla(interval: str) -> Dict[str, Any]:
    from .. import db
    evren, min_hacim = _evren_ayari()
    async with _kilit(interval):
        kayit = _onbellek.get(interval)
        now = db.now_ms()
        if kayit and now - kayit["ts"] < ONBELLEK_TTL_MS:
            return kayit["veri"]
        veri = await gunun_planlari(20, interval, evren, min_hacim)
        _onbellek[interval] = {"ts": db.now_ms(), "veri": veri}

    # BILDIRIM KILIDIN DISINDA.
    # Icerde olsaydi push gonderiminin agi (her abonelik icin bir HTTPS
    # istegi) tarama kilidini tutardi ve o sirada gelen her sayfa istegi
    # bekletilirdi. Bildirim taramanin sonucu, on sarti degil.
    #
    # Hatasi taramayi KESMEZ: bildirim gonderilemedigi icin tarama
    # sonucunun kaybolmasi sorunu buyutmek olurdu.
    # BILDIRIM YALNIZCA ARKA PLAN DILIMINDEN.
    #
    # OLCULMUS HATA (10.09): burasi hangi zaman dilimi hesaplanirsa
    # hesaplansin bildirim gonderiyordu. `_hesapla` ise arayuz onbellekte
    # olmayan bir dilim isteyince de calisiyor. Sonuc: kullanici listede
    # 4h/15m sekmelerine tikladikca GERCEK bildirimler gidiyor ve gunluk
    # kota (gunluk_azami) yeni bir kurulum bulunmadan tukeniyordu.
    # Kullanicinin gordugu sey "bugun 12/12" olurdu; sebebini bilemezdi.
    #
    # Bildirim bir TARAMA sonucudur, bir goruntuleme sonucu degil.
    if interval == ARKA_PLAN_INTERVAL:
        try:
            from . import plan_bildirim
            await plan_bildirim.bildir(veri.get("planlar") or [])
        except Exception as exc:  # noqa: BLE001
            log.warning("plan bildirimi: %s", exc)
    return veri


def _isit(interval: str) -> None:
    """Onbellegi ARKA PLANDA doldur; istegi bekletme.

    Soguk onbellekte tarama 60-90 saniye suruyor. Istegi o kadar
    bekletmek tarayicida zaman asimina ve "calismiyor" izlenimine yol
    aciyordu. Artik istek hemen "hazir degil" donuyor, arayuz onu
    soyluyor ve birkac saniyede bir tekrar bakiyor.
    """
    mevcut = _isinma.get(interval)
    if mevcut and not mevcut.done():
        return

    async def calis():
        try:
            await _hesapla(interval)
            log.info("Plan onbellegi isitildi (%s)", interval)
        except Exception as exc:  # noqa: BLE001
            log.warning("plan isitma basarisiz (%s): %s", interval, exc)

    _isinma[interval] = asyncio.create_task(calis(), name=f"vortex-plan-warm-{interval}")


async def planlar_onbellekli(limit: int = 8, interval: str = "1h",
                             taze: bool = False) -> Dict[str, Any]:
    """Onbellekli plan listesi. Sayfa acilisi BUNU cagiriyor, taramayi degil.

    Onbellek anahtari YALNIZCA zaman dilimi. Evren ve hacim tabani sistem
    ayarindan geliyor; istekten gelmelerine izin vermek, arayuzle arka
    plan gorevinin farkli anahtarlar uretmesine ve onbellegin hic
    kullanilmamasina yol aciyordu.
    """
    from .. import db
    now = db.now_ms()
    kayit = _onbellek.get(interval)

    if taze:
        veri = await _hesapla(interval)
        return {**veri, "hazir": True, "onbellek": False, "yas_sn": 0,
                "planlar": veri["planlar"][:limit]}

    if kayit:
        yas = int((now - kayit["ts"]) / 1000)
        if now - kayit["ts"] >= ONBELLEK_TTL_MS:
            _isit(interval)          # bayat: arkada tazele, bayatı simdi ver
        return {**kayit["veri"], "hazir": True, "onbellek": True, "yas_sn": yas,
                "planlar": kayit["veri"]["planlar"][:limit]}

    # HIC VERI YOK. Istegi bekletmiyoruz.
    _isit(interval)
    return {"ok": True, "hazir": False, "onbellek": False,
            "interval": interval, "planlar": [], "bakilan": 0, "bulunan": 0,
            "mesaj": "İlk tarama sürüyor — 150 kontrat inceleniyor, "
                     "bu yaklaşık bir dakika alır."}


async def _arka_plan_dongu() -> None:
    # Acilis yukunu kutuphane ve tarama ile carpistirma.
    await asyncio.sleep(45)
    while True:
        try:
            await _hesapla(ARKA_PLAN_INTERVAL)
            log.info("Plan onbellegi tazelendi (%s)", ARKA_PLAN_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("plan onbellek dongusu: %s", exc)
        await asyncio.sleep(ARKA_PLAN_ARALIK_SN)


async def start() -> None:
    global _arka_task
    if _arka_task is None or _arka_task.done():
        _arka_task = asyncio.create_task(_arka_plan_dongu(), name="vortex-plan-cache")
        log.info("Plan arka plan taramasi baslatildi (her %ds)", ARKA_PLAN_ARALIK_SN)


async def stop() -> None:
    global _arka_task
    if _arka_task and not _arka_task.done():
        _arka_task.cancel()
        try:
            await _arka_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _arka_task = None
