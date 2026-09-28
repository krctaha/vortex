"""VORTEX Mentor: emir vermeyen, kullanicinin tezini sorgulayan karar destegi."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, runtime
from ..deps import admin_user, check_origin, current_user
from ..services import (analysis, binance_trade, binance_ws, btc_rejim,
                        market_library, mentor_import, mentor_journal, mentor_plan,
                        mentor_scan, mentor_sync, plan_bildirim, portfoy)

router = APIRouter(prefix="/api/mentor", tags=["mentor"])
VALID_INTERVALS = {"5m", "15m", "30m", "1h", "4h", "1d"}


class ReviewBody(BaseModel):
    symbol: str = Field(min_length=3, max_length=24)
    interval: str = "15m"
    direction: str = Field(default="IZLE", pattern="^(IZLE|LONG|SHORT)$")
    entry: Optional[float] = Field(default=None, gt=0)
    stop: Optional[float] = Field(default=None, gt=0)
    # Kullanicidan ARTIK ISTENMIYOR. Ayarlar > Risk icindeki
    # max_risk_per_trade degeri kullaniliyor; her sorguda ayni sayiyi
    # tekrar yazdirmanin bir faydasi yoktu. Geriye donuk uyumluluk icin
    # alan duruyor: gonderilirse kullanilir, gonderilmezse ayardan okunur.
    risk_usdt: Optional[float] = Field(default=None, ge=0.25, le=1000)


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


@router.post("/review")
async def review(body: ReviewBody, user=Depends(current_user), _: None = Depends(check_origin)):
    symbol = body.symbol.upper().replace("/", "").strip()
    if not re.fullmatch(r"[A-Z0-9]{3,20}", symbol):
        raise HTTPException(status_code=422, detail="Geçersiz sembol")
    if body.interval not in VALID_INTERVALS:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")

    binance_ws.watch([symbol])
    snap = await analysis.snapshot(symbol, body.interval, 500, include_series=False)
    if not snap.get("ok"):
        raise HTTPException(status_code=422, detail=snap.get("error", "Analiz üretilemedi"))
    snap["context"] = await analysis.market_context(symbol)
    detailed = analysis.detailed_analysis(snap)

    price = float(snap.get("price") or 0)
    indicators = snap.get("indicators") or {}
    atr = float(indicators.get("atr14") or 0)
    levels = (snap.get("levels") or {}).get("support_resistance") or []
    supports = sorted(
        (float(x["price"]) for x in levels if float(x.get("price") or 0) < price),
        reverse=True,
    )
    resistances = sorted(
        float(x["price"]) for x in levels if float(x.get("price") or 0) > price
    )
    support = supports[0] if supports else (price - 1.5 * atr if atr else None)
    resistance = resistances[0] if resistances else (price + 1.5 * atr if atr else None)

    side = body.direction
    entry = float(body.entry or price)
    proposed_stop = body.stop
    if proposed_stop is None and side == "LONG":
        proposed_stop = (support - .25 * atr) if support and atr else support
    elif proposed_stop is None and side == "SHORT":
        proposed_stop = (resistance + .25 * atr) if resistance and atr else resistance
    target = resistance if side == "LONG" else support if side == "SHORT" else None

    blockers = []
    cautions = []
    positives = []
    rr = None
    direction_code = str(detailed.get("direction_code") or "neutral")
    expected_code = "bull" if side == "LONG" else "bear" if side == "SHORT" else "neutral"

    if side == "IZLE":
        cautions.append("Yön seçilmedi; VORTEX piyasa bağlamı verir fakat işlem planı onaylamaz.")
    else:
        # 06.09 DUZELTMESI — "STOP YAZMADIN" ARTIK RET SEBEBI DEGIL.
        # Onceki hali her stop'suz sorguyu blockers'a atiyordu; blockers dolu
        # olunca karar otomatik "PLANI REDDET" oluyordu. Sonuc: kullanici LONG
        # dese de SHORT dese de RET aliyordu. Reddedilen sey plan degildi,
        # FORMUN EKSIK OLMASIYDI. Ikisi ayni sey degil.
        # Artik sistem stop ONERIYOR, bunu acikca isaretliyor ve uyari
        # seviyesinde tutuyor.
        if body.stop is None:
            cautions.append(
                f"Stop yazmadın; sistem yapısal seviyeden {_fmt(proposed_stop)} öneriyor. "
                f"Bu ÖNERİ, onay değil — emri açmadan önce kendi geçersizlik "
                f"seviyeni belirle.")
        elif side == "LONG" and body.stop >= entry:
            blockers.append("LONG stop girişin altında olmalı.")
        elif side == "SHORT" and body.stop <= entry:
            blockers.append("SHORT stop girişin üstünde olmalı.")

        if direction_code == expected_code:
            positives.append("Ana veri aileleri düşündüğün yönle uyumlu.")
        elif direction_code == "neutral":
            cautions.append("Piyasa yönü kararsız; teyit gelmeden giriş kovalamak zayıf karar.")
        else:
            blockers.append("Planladığın yön, mevcut yapı ve akışın ana yönüne ters.")

        if proposed_stop and target:
            risk_distance = (entry - proposed_stop) if side == "LONG" else (proposed_stop - entry)
            reward_distance = (target - entry) if side == "LONG" else (entry - target)
            if risk_distance <= 0:
                blockers.append("Stop tarafı geçersiz; risk mesafesi pozitif değil.")
            elif reward_distance <= 0:
                blockers.append("En yakın yapısal hedef girişin avantajlı tarafında değil; fiyat kovalanmış olabilir.")
            else:
                rr = reward_distance / risk_distance
                # RR ARTIK RET SEBEBI DEGIL.
                # Hedef, kullanicinin hedefi degil; sistemin buldugu EN YAKIN
                # yapisal seviye. En yakin seviye tanimi geregi yakindir, bu
                # yuzden RR sik sik 1.5'in altina duser ve her plani reddederdi.
                # Ince RR bir BILGIdir, veto degil.
                if rr < 1.5:
                    cautions.append(
                        f"En yakın yapısal hedefe risk/getiri {rr:.2f}. Bu hedef "
                        f"senin hedefin değil, sistemin bulduğu ilk seviye — "
                        f"daha uzağını hedefliyorsan bu sayı seni bağlamaz.")
                elif rr < 2:
                    cautions.append(f"Risk/getiri {rr:.2f}; hata payı dar.")
                else:
                    positives.append(f"Yapısal hedefe göre brüt risk/getiri {rr:.2f}.")

        if atr and abs(entry - price) > .75 * atr:
            cautions.append("Yazdığın giriş anlık fiyattan 0,75 ATR'den uzak; eski fiyatla plan kuruyor olabilirsin.")
        if snap.get("last_bar_closed") is False:
            cautions.append("Son mum açık; kapanmadan görülen teyit geri dönebilir.")
        if detailed.get("quality") == "zayıf / çelişkili":
            cautions.append("Bağımsız veri aileleri yeterince hizalı değil.")

    if side == "IZLE":
        verdict, verdict_code = "SADECE İZLE", "observe"
    elif blockers:
        verdict, verdict_code = "PLANI REDDET", "blocked"
    elif cautions:
        verdict, verdict_code = "TEYİT BEKLE", "wait"
    else:
        verdict, verdict_code = "PLAN İNCELENEBİLİR", "reviewable"

    # ------------------------------------------------------------------ #
    # NE YAPMALI — 06.09'da eklendi.
    #
    # Onceki hali yalnizca RET/BEKLE diyordu ve kullanici hakli olarak
    # "peki ne yapacagim" diye sordu. Sadece hayir diyen bir mentor
    # ise yaramaz. Her karar artik SOMUT bir sonraki adim tasiyor:
    # neyin degismesi gerektigi, hangi seviyede, hangi teyitle.
    # ------------------------------------------------------------------ #
    ne_yapmali: list = []
    if side == "IZLE":
        ne_yapmali.append("Bir yön seç (LONG/SHORT) — plan değerlendirmesi ancak o zaman yapılır.")
    else:
        ters_yon = "SHORT" if side == "LONG" else "LONG"
        if any("ana yönüne ters" in b for b in blockers):
            ne_yapmali.append(
                f"Yapı ve akış şu an {ters_yon} tarafında. Üç seçeneğin var: "
                f"(1) bu işlemi atla, (2) {ters_yon} tarafını değerlendir, "
                f"(3) {side} için yapının dönmesini bekle — yani "
                f"{_fmt(resistance if side == 'LONG' else support)} seviyesinin "
                f"kapanışla {'aşılması' if side == 'LONG' else 'kırılması'}.")
        if body.stop is None:
            ne_yapmali.append(
                f"Stop belirle. Sistem {_fmt(proposed_stop)} öneriyor; kendi "
                f"seviyeni yazıp tekrar sorgularsan değerlendirme netleşir.")
        if snap.get("last_bar_closed") is False:
            ne_yapmali.append(
                f"{body.interval} mumu henüz kapanmadı. Kapanışı bekle — "
                f"kapanmadan görünen teyit geri dönebilir.")
        if atr and abs(entry - price) > .75 * atr:
            ne_yapmali.append(
                f"Girişin anlık fiyattan uzak. Ya {_fmt(price)} civarına "
                f"güncelle ya da fiyatın senin seviyene gelmesini bekle — "
                f"peşinden koşma.")
        if detailed.get("confirmation"):
            ne_yapmali.append(f"Teyit şartı: {detailed['confirmation']}")
        if detailed.get("invalidation"):
            ne_yapmali.append(f"Fikrin şurada çürür: {detailed['invalidation']}")
        if verdict_code == "reviewable":
            ne_yapmali.append(
                "Yapısal engel yok. Kendi kontrol listeni geç, pozisyon "
                "boyutunu risk kuralından hesapla, stopu emirle birlikte kur.")
        if not ne_yapmali:
            ne_yapmali.append(
                "Belirleyici bir engel de teyit de yok. Beklemek de bir karardır.")

    mentor_questions = [
        "Bu işlem çalışmazsa hangi kapanışta fikrinin yanlış olduğunu kabul edeceksin?",
        "Aynı yönde açık başka pozisyonların varsa gerçekte kaç ayrı bahis taşıyorsun?",
        "Giriş kaçarsa peşinden koşmadan vazgeçebilecek misin?",
        "Bu planı şimdi görmeseydin yalnızca kaçırma korkusuyla yine açar mıydın?",
    ]
    result: Dict[str, Any] = {
        "ok": True, "symbol": symbol, "interval": body.interval,
        "verdict": verdict, "verdict_code": verdict_code,
        "direction": side, "price": price, "entry": entry,
        "support": support, "resistance": resistance,
        "proposed_stop": proposed_stop, "target": target,
        "reward_risk": round(rr, 2) if rr is not None else None,
        "risk_usdt": (body.risk_usdt if body.risk_usdt is not None
                      else float(runtime.get()["risk"].get("max_risk_per_trade", 1.0))),
        "summary": detailed.get("summary"),
        "confirmation": detailed.get("confirmation"),
        "invalidation": detailed.get("invalidation"),
        "bull_scenario": detailed.get("bull_scenario"),
        "bear_scenario": detailed.get("bear_scenario"),
        "evidence_for": detailed.get("evidence_for") or [],
        "evidence_against": detailed.get("evidence_against") or [],
        "blind_spots": detailed.get("blind_spots") or [],
        "blockers": blockers, "cautions": cautions, "positives": positives,
        "ne_yapmali": ne_yapmali,
        "mentor_questions": mentor_questions,
        "metrics": detailed.get("metrics") or {},
        "disclaimer": "VORTEX emir vermez; bu çıktı karar desteğidir.",
    }
    db.execute(
        "INSERT INTO mentor_reviews(user_id,symbol,interval,direction,verdict,price,"
        "entry_price,stop_price,target_price,reward_risk,created_at,payload) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (user["id"], symbol, body.interval, side, verdict, price, entry,
         body.stop, target, result["reward_risk"], db.now_ms(),
         json.dumps(result, ensure_ascii=False)),
    )
    return result


@router.get("/recent")
def recent(limit: int = 8, user=Depends(current_user)):
    limit = max(1, min(int(limit), 30))
    rows = db.query(
        "SELECT id,symbol,interval,direction,verdict,price,reward_risk,created_at "
        "FROM mentor_reviews WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user["id"], limit),
    )
    return {"items": rows}


# =================================================================== #
# GUNLUK — "ne yaptim, ise yariyor mu"
#
# Yukaridaki /review "bu isleme gireyim mi" sorusunu cevapliyor ve verdigi
# tavsiyeyi mentor_reviews'a yaziyor. Ama o tavsiyenin DOGRU CIKIP CIKMADIGI
# hicbir yerde olculmuyordu — RSI cagrilarinda kapattigimiz aynı boslugun
# tekrari. Buradaki uclar o halkayi kapatiyor:
#   ac()   -> kullanici gercekten girdiginde kaydeder, varsa review'a baglar
#   kapat()-> sonucu R cinsinden olcer, yol metriklerini hesaplar
#   karne()-> KULLANICIYI puanlar (motoru degil)
# =================================================================== #
def _gcfg() -> Dict[str, Any]:
    return runtime.get().get("mentor_gunluk", {})


class GunlukAcBody(BaseModel):
    symbol: str = Field(min_length=3, max_length=24)
    side: str = Field(pattern="^(LONG|SHORT)$")
    entry: float = Field(gt=0)
    initial_stop: float = Field(gt=0)
    initial_target: Optional[float] = Field(default=None, gt=0)
    qty: Optional[float] = Field(default=None, gt=0)
    leverage: Optional[int] = Field(default=None, ge=1, le=125)
    gerekce: str = Field(default="", max_length=400)
    etiket: str = Field(default="", max_length=60)
    kontrol: Dict[str, bool] = Field(default_factory=dict)


class GunlukKapatBody(BaseModel):
    exit_price: float = Field(gt=0)
    exit_reason: str = Field(default="elle", max_length=60)


@router.get("/gunluk/durum")
async def gunluk_durum(user=Depends(current_user)):
    acik = mentor_journal.acik_islemler(user["id"])

    # ACIK POZISYONLARA BTC AKINTISI EKLENIYOR.
    #
    # Kullanicinin tarifi: "su anda acik short islemlerimiz var, BTC yukari
    # gidiyor ve ona gore zarar ediyoruz." Bu bilgi ISLEM ACILIRKEN de
    # gerekli ama asil ACIKKEN gerekli: rejim islem acildiktan SONRA
    # donebilir ve pozisyon bir anda akintiya karsi kalir. Plan kurulurken
    # bir kez bakip gecmek o donusu kaciririr.
    #
    # Maliyet: BTC barlari 45 saniye onbellekli, sembol barlari acik islem
    # basina bir cagri. Acik islem sayisi tanimi geregi kucuk.
    rejim = None
    try:
        rejim = await btc_rejim.rejim("1h")
    except Exception:  # noqa: BLE001
        pass
    # ACIK POZISYON DENETIMI.
    # Plan kurmak ile acik pozisyonu denetlemek farkli iki istir: girerken
    # soru "girmeli miyim", acikken soru "TEZIM HALA AYAKTA MI". Ikincisi
    # cok daha sik ihmal edilir ve daha pahaliya mal olur — insan girerken
    # dikkatli, tutarken umutludur. Denetim BTC akintisini de iceriyor.
    if acik:
        import asyncio as _aio
        sem = _aio.Semaphore(3)

        async def denetle(t):
            async with sem:
                try:
                    t["denetim"] = await mentor_plan.pozisyon_denetimi(
                        t["symbol"], t["side"], float(t["entry"]),
                        float(t["initial_stop"]) if t.get("initial_stop") else None)
                    t["btc"] = (t["denetim"].get("ayrinti") or {}).get("btc")
                except Exception:  # noqa: BLE001
                    pass

        try:
            await _aio.gather(*(denetle(t) for t in acik))
        except Exception:  # noqa: BLE001
            pass

    return {
        "kapi": mentor_journal.kapi(user["id"], _gcfg()),
        "acik": acik,
        "btc_rejim": rejim if (rejim and rejim.get("ok")) else None,
        "kontrol_maddeleri": [{"anahtar": k, "metin": m}
                              for k, m in mentor_journal.KONTROL_MADDELERI],
        "config": _gcfg(),
    }


@router.post("/gunluk/ac")
async def gunluk_ac(body: GunlukAcBody, user=Depends(current_user),
                    _: None = Depends(check_origin)):
    # DEVRE KESICI sunucu tarafinda. Arayuzde gizlemek yetmez: kilidi asmak
    # isteyen kisi kullanicinin kendisi.
    kapi = mentor_journal.kapi(user["id"], _gcfg())
    if not kapi["acik"]:
        raise HTTPException(status_code=423, detail=kapi["mesaj"])
    out = await mentor_journal.ac(
        user["id"], body.symbol, body.side, body.entry, body.initial_stop,
        body.initial_target, body.qty, body.leverage,
        body.gerekce, body.etiket, body.kontrol)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("hata", "kayit basarisiz"))
    return out


@router.post("/gunluk/{trade_id}/kapat")
async def gunluk_kapat(trade_id: int, body: GunlukKapatBody,
                       user=Depends(current_user), _: None = Depends(check_origin)):
    out = await mentor_journal.kapat(user["id"], trade_id,
                                     body.exit_price, body.exit_reason)
    if not out.get("ok"):
        raise HTTPException(status_code=404, detail=out.get("hata", "bulunamadi"))
    return out


@router.get("/gunluk/karne")
async def gunluk_karne(gun: int = 0, user=Depends(current_user)):
    k = mentor_journal.karne(user["id"], gun)
    return {"karne": k, "ogutler": mentor_journal.ogutler(k)}


@router.get("/gunluk/gecmis")
async def gunluk_gecmis(limit: int = 100, user=Depends(current_user)):
    return {"islemler": mentor_journal.gecmis(user["id"], min(max(limit, 1), 500))}


@router.get("/gunluk/binance")
async def gunluk_binance(user=Depends(current_user)):
    """Binance'teki ACIK pozisyonlari, borsadaki stop/hedef emirleriyle birlikte.

    NEDEN VAR
    Kullanicidan sembol, yon, giris, miktar, kaldirac, stop ve hedefi elle
    istemek anlamsizdi: bunlarin HEPSI zaten borsada duruyor. Elle giris hem
    yorucu hem hatali (yanlis yazilan giris fiyati tum R hesabini bozar).
    Burasi hepsini cekiyor; kullaniciya kalan tek is GEREKCE yazmak.

    Anahtar yoksa hata degil, bos liste + aciklama doner — arayuz elle
    girise geri duser.
    """
    if not binance_trade.has_keys():
        return {"ok": False, "sebep": "anahtar_yok",
                "mesaj": "Binance API anahtarı bağlı değil.",
                "pozisyonlar": []}
    try:
        ham = await binance_trade.position_rows()
        algolar = await binance_trade.open_algo_orders()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "sebep": "baglanti",
                "mesaj": f"Binance'e ulaşılamadı: {type(exc).__name__}",
                "pozisyonlar": []}

    # Bu kullanicinin gunlukte ZATEN acik kaydi olan semboller: tekrar
    # kaydetmesin diye isaretliyoruz.
    kayitli = {r["symbol"] for r in mentor_journal.acik_islemler(user["id"])}

    out = []
    for p in (ham or []):
        try:
            miktar = float(p.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            continue
        if abs(miktar) <= 0:
            continue
        sym = str(p.get("symbol", ""))
        yon = "LONG" if miktar > 0 else "SHORT"

        # Borsadaki kosullu emirlerden stop ve hedefi cikar. Kullanici
        # bunlari zaten Binance'te kurmus; tekrar yazdirmanin anlami yok.
        stop = hedef = None
        for a in algolar:
            if str(a.get("symbol")) != sym:
                continue
            tip = str(a.get("algoType") or a.get("type") or "").upper()
            try:
                fiyat = float(a.get("triggerPrice") or a.get("stopPrice") or 0) or None
            except (TypeError, ValueError):
                fiyat = None
            if not fiyat:
                continue
            if "STOP" in tip:
                stop = fiyat
            elif "PROFIT" in tip or "TAKE" in tip:
                hedef = fiyat

        out.append({
            "symbol": sym,
            "side": yon,
            "entry": float(p.get("entryPrice", 0) or 0),
            "qty": abs(miktar),
            "leverage": int(float(p.get("leverage", 0) or 0)) or None,
            "mark": float(p.get("markPrice", 0) or 0),
            "unrealized": float(p.get("unRealizedProfit", 0) or 0),
            "initial_stop": stop,
            "initial_target": hedef,
            "zaten_kayitli": sym in kayitli,
            "stop_borsada_yok": stop is None,
        })
    out.sort(key=lambda x: -abs(x["unrealized"]))
    return {"ok": True, "pozisyonlar": out}


# =================================================================== #
# OTOMATIK TARAMA — kullanici sormadan biriken gozlemler
# =================================================================== #
@router.get("/tarama/durum")
async def tarama_durum(_user=Depends(current_user)):
    return mentor_scan.durum()


@router.get("/tarama/gozlemler")
async def tarama_gozlemler(limit: int = 20, _user=Depends(current_user)):
    """Sistemin KENDILIGINDEN kaydettigi gozlemler."""
    return {"gozlemler": mentor_scan.son_gozlemler(min(max(limit, 1), 100))}


@router.post("/tarama/simdi")
async def tarama_simdi(user=Depends(current_user), _: None = Depends(check_origin)):
    return await mentor_scan.tara_bir_kez(user["id"])


# =================================================================== #
# BINANCE GECMISI ICERI AKTARMA
# Gunluk sadece bugunden dolunca 20 islemlik esige haftalarca
# ulasilamiyordu. Asil kazanc hiz degil TARAFSIZLIK: borsa gecmisi
# kazanani da kaybedeni de esit tasir, elle giris tasimaz.
# =================================================================== #
class IceAktarBody(BaseModel):
    islemler: list = Field(default_factory=list, max_length=500)


@router.get("/gunluk/binance-gecmis")
async def gunluk_binance_gecmis(gun: int = 30, user=Depends(current_user)):
    """Aktarilabilir kapanmis islemleri LISTELER — hicbir sey yazmaz."""
    return await mentor_import.onizle(user["id"], gun)


@router.post("/gunluk/ice-aktar")
async def gunluk_ice_aktar(body: IceAktarBody, user=Depends(current_user),
                           _: None = Depends(check_origin)):
    if not body.islemler:
        raise HTTPException(status_code=400, detail="Aktarilacak islem secilmedi")
    return mentor_import.ice_aktar(user["id"], body.islemler)


# =================================================================== #
# PLAN — AKISIN TERSI
# /review kullanicinin planini yargilar. Burasi PLANI KURAR.
# Kullanici "ne yapayim" diye soruyorsa ondan stop istemek anlamsizdi;
# stopun ne olmasi gerektigini bilse zaten sormazdi.
# =================================================================== #
@router.get("/plan/{symbol}")
async def plan_tek(symbol: str, interval: str = "1h", _user=Depends(current_user)):
    if not re.fullmatch(r"[A-Za-z0-9]{3,20}", symbol):
        raise HTTPException(status_code=422, detail="Gecersiz sembol")
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    return await mentor_plan.plan_kur(symbol, interval)


@router.get("/planlar")
async def planlar(limit: int = 8, interval: str = "1h",
                  taze: int = 0, _user=Depends(current_user)):
    """"Bugun ne var" — ONBELLEKTEN.

    Bu uc eskiden her cagrida 150 sembole derin analiz yapiyordu ve
    arayuz onu sayfa acilisinda cagiriyordu. Sol menuden gezinmek tam
    sayfa yenilemesi oldugu icin her gezinme yeni bir tam tarama
    tetikliyordu: bes gezinme ~6500 agirlik, dakikalik butce 2400.
    Artik tarama arka planda calisiyor ve burasi yalnizca okuyor —
    gezinme maliyeti sifir, cevap anlik.

    taze=1 yalnizca kullanici "Yenile"ye bastiginda gonderiliyor.
    """
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    # evren ARTIK PARAMETRE DEGIL: sistem ayarindan geliyor. Arayuzle
    # arka plan gorevinin farkli evren degerleri gondermesi, onbellek
    # anahtarlarini ayirip onbellegi tamamen ise yaramaz hale getiriyordu.
    return await mentor_plan.planlar_onbellekli(
        min(max(limit, 1), 20), interval, taze=bool(taze))


# =================================================================== #
# PIYASA KUTUPHANESI — ucuz ve EVRENSEL katman
#
# Derin analiz sembol basina ~10 agirlik harciyor, o yuzden hepsine
# uygulanamiyor. Ama /fapi/v1/ticker/24hr sembolsuz cagrildiginda TEK
# istekte (agirlik 40) butun evreni veriyor. Kutuphane o veriyi saklayip
# her sembolun KENDI tabanina gore kiyasliyor; pahali analiz yalnizca
# onun one cikardigi adaylara gidiyor.
# =================================================================== #
@router.get("/kutuphane/ozet")
async def kutuphane_ozet(_user=Depends(current_user)):
    return {"ok": True, **market_library.ozet()}


@router.get("/kutuphane")
async def kutuphane(limit: int = 60, sirala: str = "ilgi",
                    min_hacim_m: float = 0.0, _user=Depends(current_user)):
    if sirala not in ("ilgi", "hacim", "degisim", "ivme", "hacim_orani"):
        raise HTTPException(status_code=422, detail="Gecersiz siralama")
    return {"ok": True, "sirala": sirala,
            "satirlar": market_library.kutuphane(limit, sirala, min_hacim_m),
            "ozet": market_library.ozet()}


@router.post("/kutuphane/tazele")
async def kutuphane_tazele(_user=Depends(current_user), _: None = Depends(check_origin)):
    return await market_library.tazele()


@router.get("/nabiz")
async def nabiz(_user=Depends(current_user)):
    """BTC, ETH ve futures konumlanma orani — sayfanin ust seridi.

    Fiyatlar kutuphaneden okunuyor, ek Binance cagrisi yok. Long/short
    orani 5 dakika onbellekli ve hacme gore ilk 6 sembolun agirlikli
    ortalamasi. Oranin HESAP sayisini olctugu (pozisyon buyuklugunu
    degil) arayuzde acikca yaziyor.
    """
    return await market_library.nabiz()


# =================================================================== #
# OTOMATIK SENKRON — gunlugu borsadan doldurur
#
# Kullanicidan istenen alan sayisi 11'den 1'e (istege bagli gerekce)
# dustu. Sembol, yon, giris, miktar, kaldirac, stop, hedef ve cikis
# fiyati borsadan okunuyor.
# =================================================================== #
@router.get("/senkron/durum")
async def senkron_durum(_user=Depends(current_user)):
    return {"ok": True, "anahtar": binance_trade.has_keys(),
            **mentor_sync.durum()}


@router.post("/senkron/simdi")
async def senkron_simdi(user=Depends(current_user), _: None = Depends(check_origin)):
    return await mentor_sync.esitle(user["id"])


class NotBody(BaseModel):
    gerekce: str = Field(default="", max_length=400)
    etiket: str = Field(default="", max_length=60)


@router.post("/gunluk/{trade_id}/not")
async def gunluk_not(trade_id: int, body: NotBody, user=Depends(current_user),
                     _: None = Depends(check_origin)):
    """Otomatik alinan bir kayda gerekce/etiket ekle.

    Senkron servisi islemi borsadan aliyor ama NEDEN girildigini bilemez.
    Tek eksik alan bu ve zorunlu degil; kullanici isterse sonradan yaziyor.
    Giris, stop, miktar gibi OLCUM alanlari buradan degistirilemez —
    initial_stop dondurulmus olmak zorunda.
    """
    row = db.query_one("SELECT id FROM mentor_trades WHERE id=? AND user_id=?",
                       (trade_id, user["id"]))
    if not row:
        raise HTTPException(status_code=404, detail="Kayit bulunamadi")
    db.execute("UPDATE mentor_trades SET gerekce=?, etiket=COALESCE(NULLIF(?,''),etiket) "
               "WHERE id=?",
               (body.gerekce.strip()[:400], body.etiket.strip()[:60], trade_id))
    return {"ok": True}


# =================================================================== #
# BTC REJIMI — altcoin planlarinin ucuncu boyutu
#
# Bir altcoinin bir saatlik getirisinin buyuk kismi o coinle ilgili
# degil, BTC'yle ilgili. Sistem her sembolu tek basina olcuyordu ve
# altcoin short'unu farkinda olmadan BTC'ye karsi aciyordu.
# =================================================================== #
@router.get("/btc-rejim")
async def btc_rejim_ucu(interval: str = "1h", _user=Depends(current_user)):
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    return await btc_rejim.rejim(interval)


@router.get("/senkron/teshis")
async def senkron_teshis(_user=Depends(current_user)):
    """Binance baglantisinin HANGI adimda kirildigini soyler.

    "Cekemiyorum" cumlesini bir sebebe ve bir yapilacak ise ceviriyor:
    anahtar yok mu, anahtar yanlis mi, IP kisiti mi, izin mi eksik,
    ulke engeli mi. Hicbir emir gondermez, yalnizca okur.
    """
    return await mentor_sync.teshis()


# =================================================================== #
# PLAN BILDIRIMI
# Kullanicinin sorunu sinyal uretmek degil, girisleri kacirmakti.
# Bu uclar bildirimi ACIP KAPATMIYOR (o ayarlardan), yalnizca durumu
# gosteriyor ve tek seferlik deneme gonderiyor.
# =================================================================== #
@router.get("/bildirim/durum")
def bildirim_durum(_user=Depends(current_user)):
    return plan_bildirim.durum_ozeti()


@router.get("/bildirim/onizleme")
async def bildirim_onizleme(interval: str = "1h", _user=Depends(current_user)):
    """Su anki listeye gore HANGI kurulum bildirilirdi, hangisi neden elenirdi.

    "Neden bildirim gelmiyor" sorusunun tahminle degil olcumle
    cevaplanmasi icin. Hicbir sey GONDERMEZ.
    """
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    veri = await mentor_plan.planlar_onbellekli(20, interval)
    if not veri.get("hazir", True):
        return {"hazir": False, "mesaj": veri.get("mesaj")}
    s = plan_bildirim.secim(veri.get("planlar") or [])
    return {
        "hazir": True,
        "gonderilecek": [plan_bildirim.metin(p) | {"symbol": p.get("symbol")}
                         for p in s["gonderilecek"]],
        "elenen": s["elenen"],
        "durum": plan_bildirim.durum_ozeti(),
    }


@router.post("/bildirim/dene")
async def bildirim_dene(interval: str = "1h", _user=Depends(admin_user),
                        _: None = Depends(check_origin)):
    """Listedeki EN IYI kurulumu ornek bildirim olarak gonderir.

    Tekrar kapisini ve gunluk sayaci ETKILEMEZ — bu bir deneme, gercek
    bir uyari degil; saydigimiz an gercek uyarilardan birini calardi.
    """
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=422, detail="Desteklenmeyen zaman dilimi")
    veri = await mentor_plan.planlar_onbellekli(1, interval)
    planlar = veri.get("planlar") or []
    if not planlar:
        return {"ok": False, "hata": "Şu an listede kurulum yok; önce tarama tamamlansın."}
    m = plan_bildirim.metin(planlar[0])
    from ..services import webpush
    r = await webpush.send(m["baslik"], m["govde"], url="/workspace", tag="plan-deneme")
    return {"ok": bool(r.get("ok")), "onizleme": m, "sonuc": r}


# =================================================================== #
# PORTFOY — Binance bakiyesi + acik pozisyonlar, CANLI
#
# Bu veri sistemde zaten vardi ama yalnizca /api/trading/status
# uzerinden, admin kilidi arkasinda ve tek seferlik ciziliyordu.
# Kullanicinin "bakiyemi Binance'ten otomatik gorsun" istegi, aslinda
# var olan verinin ona hic ulasmadigini soyluyordu.
#
# Burasi current_user ile aciliyor: kendi bakiyesini gormek yonetici
# yetkisi gerektiren bir is degil. Anahtar yazma islemi admin'de kaliyor.
# =================================================================== #
@router.get("/portfoy")
async def portfoy_ozeti(taze: bool = False, _user=Depends(current_user)):
    return await portfoy.ozet(taze=taze)
