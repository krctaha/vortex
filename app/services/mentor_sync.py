"""BINANCE OTOMATIK SENKRON — gunluk artik elle doldurulmuyor.

NEDEN VAR
---------
Gunluk formu su alanlari istiyordu: sembol, yon, giris, stop, hedef, miktar,
kaldirac, etiket, gerekce, bes maddelik kontrol listesi. On bir alan. Bir
islem acmis, telefonuyla bakan, isten kacamak yapan biri bunu doldurmaz —
ve doldurmadigi icin sicil bos kalir, sicil bos kalinca da karne hicbir sey
olcemez. Kullanicinin "her boku ben mi gireyim" tepkisi tam olarak buydu.

Daha kotusu: elle giris SESSIZ BIR YANLILIK uretir. Insan kazandigi islemi
yazar, kaybettigini "zaten kucuktu" diye atlar. O gunluge bakip cikarilan
her sonuc yanlis olur.

Bu modul on bir alanin dokuzunu borsadan okuyor:
    sembol, yon, giris, miktar, kaldirac -> /fapi/v3/positionRisk
    stop, hedef                          -> /fapi/v1/openAlgoOrders
    cikis fiyati, kapanis zamani         -> /fapi/v1/userTrades
Geriye kullanicidan istenen sey kaliyor: GEREKCE. O da zorunlu degil.

NE UYDURMUYOR
-------------
Borsada stop emri yoksa stop NULL kalir. Girise ya da "tahmini" bir
seviyeye yazmak tum R hesabini bozardi (0 risk = sonsuz R). Stopsuz islem
kaydedilir, dolar sonucu ve sure olculur, ama R tabanli hicbir istatistige
(SQN, bootstrap, R ortalamasi) girmez.

Kontrol listesi de uydurulmuyor. "Kurallarima uydum mu" sorusunun cevabini
borsa bilmez; otomatik kayitlarda kural_uyumu NULL kalir ve kural
kohortu kiyaslamasina girmez. Bos birakmak, varsayimla doldurmaktan iyidir:
uydurulmus bir "uydum" isareti kohort karsilastirmasini tamamen anlamsiz
yapardi.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from .. import db, runtime

log = logging.getLogger("vortex.mentor.sync")

ARALIK_SANIYE = 90
KAYNAK = "binance-oto"

_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()
_state: Dict[str, Any] = {
    "son": None, "acilan": 0, "kapanan": 0, "guncellenen": 0,
    "acik": 0, "hata": None, "aktif": False,
}


def durum() -> Dict[str, Any]:
    return dict(_state)


def _f(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f else None
    except (TypeError, ValueError):
        return None


def borsa_stop_hedef(algolar: List[dict], sym: str, side: str,
                     entry: float) -> tuple:
    """Borsadaki kosullu emirlerden stop ve hedefi ayikla.

    YON TUTARLILIGI KAPISI: LONG'ta stop girisin ALTINDA, SHORT'ta
    USTUNDE olmak zorunda. Borsada ters duran bir emir (eski bir hedge
    kalintisi, yanlis kurulmus bir TP) stop diye kaydedilirse R isareti
    ters doner ve karne kazanci kayip, kaybi kazanc sayar. Boyle bir
    emri "stop yok" saymak, yanlis stop saymaktan cok daha iyidir.
    """
    stop = hedef = None
    for a in algolar:
        if str(a.get("symbol")) != sym:
            continue
        tip = str(a.get("algoType") or a.get("type") or "").upper()
        fiyat = _f(a.get("triggerPrice") or a.get("stopPrice"))
        if not fiyat:
            continue
        if "PROFIT" in tip or "TAKE" in tip:
            hedef = fiyat
        elif "STOP" in tip:
            stop = fiyat
    if stop is not None and entry:
        if side == "LONG" and stop >= entry:
            stop = None
        elif side == "SHORT" and stop <= entry:
            stop = None
    return stop, hedef


async def _cikis_fiyati(sym: str, opened_at: int) -> Optional[Dict[str, Any]]:
    """Kapanan pozisyonun GERCEK cikis fiyati — dolgulardan agirlikli ortalama.

    Mark fiyatini kullanmak kolay olurdu ama yanlis olurdu: pozisyon iki
    tarama arasinda kapandiysa mark o anki fiyat, gercek cikis degil.
    Kapanis dolgulari realizedPnl != 0 tasir; onlarin miktar agirlikli
    ortalamasi gercek cikistir.
    """
    from . import binance_trade
    try:
        fills = await binance_trade.signed("/fapi/v1/userTrades", {
            "symbol": sym, "startTime": max(0, opened_at - 60_000), "limit": 1000})
    except Exception as exc:  # noqa: BLE001
        log.debug("userTrades %s: %s", sym, exc)
        return None
    top_q = top_qp = 0.0
    son_ts = 0
    pnl = 0.0
    for f in (fills or []):
        try:
            rp = float(f.get("realizedPnl", 0) or 0)
            q = abs(float(f.get("qty", 0) or 0))
            p = float(f.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if rp == 0.0 or q <= 0 or p <= 0:
            continue
        top_q += q
        top_qp += q * p
        pnl += rp
        son_ts = max(son_ts, int(f.get("time", 0) or 0))
    if top_q <= 0:
        return None
    return {"price": top_qp / top_q, "ts": son_ts or db.now_ms(),
            "pnl": round(pnl, 4)}


def _son_hatalar() -> List[Dict[str, Any]]:
    """Sunucuda son yakalanan beklenmeyen istisnalar.

    Teshis panelinin bir parcasi: "Internal Server Error" cumlesi
    kullaniciya hicbir sey soylemiyordu; artik hangi ucun hangi istisnayla
    kirildigi burada gorunuyor.
    """
    try:
        from .. import hata_kaydi
        return hata_kaydi.son(8)
    except Exception:  # noqa: BLE001
        return []


async def teshis() -> Dict[str, Any]:
    """BAGLANTI TESHISI — "cekemiyorum" cumlesini bir sebebe cevirir.

    Onceki hali tek bir genel mesaj veriyordu ("Binance'e ulasilamadi").
    Bu, kullaniciya hicbir sey soylemiyor: anahtar mi yok, anahtar mi
    yanlis, IP kisiti mi var, izin mi eksik, sunucu mu erisemiyor —
    hepsi ayni cumleye cikiyordu ve hicbiri duzeltilemiyordu.

    Bu fonksiyon her adimi AYRI AYRI deniyor ve ilk kirilan yeri
    isaretliyor. Cikti bir hata mesaji degil, bir YAPILACAK IS.
    """
    from . import binance_trade
    adimlar: List[Dict[str, Any]] = []

    def ekle(ad: str, ok: Optional[bool], detay: str, cozum: str = "") -> None:
        adimlar.append({"ad": ad, "ok": ok, "detay": detay, "cozum": cozum})

    if not binance_trade.has_keys():
        ekle("API anahtarı", False, "Kayıtlı anahtar yok.",
             "Ayarlar › Binance bölümünden API anahtarını ve gizli anahtarı "
             "yapıştır. Anahtarı Binance'te oluştururken 'Enable Futures' "
             "iznini açman gerekiyor.")
        return {"ok": False, "asama": "anahtar", "adimlar": adimlar,
                "son_hatalar": _son_hatalar()}
    ekle("API anahtarı", True, f"Kayıtlı (…{binance_trade.key_hint() or '????'}).")

    # 1) Hesap okuma — anahtar gecerli mi, imza dogru mu, IP kisiti var mi?
    try:
        await binance_trade.account_v2()
        ekle("Hesap okuma", True, "Futures hesabı okunabiliyor.")
    except Exception as exc:  # noqa: BLE001
        metin = f"{type(exc).__name__}: {exc}"
        dusuk = metin.lower()
        if "-2015" in metin or "invalid api-key" in dusuk or "ip" in dusuk:
            cozum = ("Bu hata genellikle ÜÇ şeyden biridir: (a) anahtar yanlış "
                     "kopyalanmış, (b) anahtarda Futures izni kapalı, "
                     "(c) Binance'te IP kısıtı var ve sunucunun IP'si listede "
                     "değil. Sunucudan `curl -s https://api.ipify.org` ile IP'yi "
                     "öğrenip Binance'teki anahtarın izin listesine ekle.")
        elif "-1021" in metin or "timestamp" in dusuk:
            cozum = ("Sunucu saati Binance'ten sapmış. Sunucuda "
                     "`timedatectl set-ntp true` çalıştır.")
        elif "-2014" in metin or "signature" in dusuk:
            cozum = "Gizli anahtar (secret) hatalı; ikisini birlikte yeniden gir."
        else:
            cozum = ("Binance'e ulaşılamıyor olabilir. Sunucudan "
                     "`curl -s -o /dev/null -w '%{http_code}' "
                     "https://fapi.binance.com/fapi/v1/ping` çıktısı 200 mü bak; "
                     "451 dönüyorsa sunucunun bulunduğu ülke engelli demektir.")
        ekle("Hesap okuma", False, metin, cozum)
        return {"ok": False, "asama": "hesap", "adimlar": adimlar,
                "son_hatalar": _son_hatalar()}

    # 2) Pozisyon okuma — senkronun asil ihtiyaci.
    try:
        rows = await binance_trade.position_rows()
        acik = [r for r in (rows or []) if _f(r.get("positionAmt"))]
        ekle("Pozisyonlar", True,
             f"{len(acik)} açık pozisyon görülüyor "
             f"({', '.join(str(r.get('symbol')) for r in acik[:5]) or 'yok'}).")
    except Exception as exc:  # noqa: BLE001
        ekle("Pozisyonlar", False, f"{type(exc).__name__}: {exc}",
             "Anahtarda Futures okuma izni kapalı olabilir.")
        return {"ok": False, "asama": "pozisyon", "adimlar": adimlar,
                "son_hatalar": _son_hatalar()}

    # 3) Kosullu emirler — stop/hedef buradan okunuyor. Basarisiz olursa
    #    senkron yine calisir, sadece stoplar bos kalir. O yuzden
    #    OLUMCUL DEGIL; ayri isaretleniyor.
    try:
        algo = await binance_trade.open_algo_orders()
        ekle("Stop/hedef emirleri", True, f"{len(algo or [])} koşullu emir okundu.")
    except Exception as exc:  # noqa: BLE001
        ekle("Stop/hedef emirleri", None, f"{type(exc).__name__}: {exc}",
             "Senkron yine çalışır ama stop seviyeleri boş kalır; "
             "stopsuz işlemler R istatistiklerine girmez.")

    # 4) Islem gecmisi — kapanislari gercek cikis fiyatiyla kapatmak icin.
    try:
        await binance_trade.signed("/fapi/v1/income",
                                   {"incomeType": "REALIZED_PNL", "limit": 1})
        ekle("İşlem geçmişi", True, "Gerçekleşmiş kâr/zarar okunabiliyor.")
    except Exception as exc:  # noqa: BLE001
        ekle("İşlem geçmişi", None, f"{type(exc).__name__}: {exc}",
             "Kapanan işlemler gerçek çıkış fiyatıyla kapatılamayabilir.")

    return {"ok": True, "asama": "tamam", "adimlar": adimlar,
            "son_hatalar": _son_hatalar(), **durum()}


async def esitle(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Borsadaki acik pozisyonlarla gunlugu esitle."""
    from . import binance_trade, mentor_journal
    if _lock.locked():
        # KILIT SIZINTISI KORUMASI.
        # Bir cagri Binance'te asili kalirsa kilit sonsuza kadar tutulur
        # ve o andan sonra HER senkron "zaten calisiyor" der; arayuz de
        # sonsuza kadar "ilk okuma bekleniyor" gosterir. Asili kalan
        # cagriyi iptal edemeyiz ama durumu SOYLEYEBILIRIZ.
        bekleyen = (db.now_ms() - int(_state.get("kilit_ts") or db.now_ms())) / 1000
        return {"ok": False, "sebep": "zaten calisiyor",
                "bekleme_sn": int(bekleyen),
                "uyari": ("Önceki okuma 2 dakikadan uzun süredir bitmedi — "
                          "Binance yanıt vermiyor olabilir. Sunucuyu yeniden "
                          "başlatmak bu kilidi açar.") if bekleyen > 120 else None}
    if not binance_trade.has_keys():
        _state.update(aktif=False, hata=None)
        return {"ok": False, "sebep": "anahtar_yok"}

    async with _lock:
        _state["kilit_ts"] = db.now_ms()
        t0 = time.monotonic()
        if user_id is None:
            row = db.query_one("SELECT id FROM users ORDER BY id LIMIT 1")
            user_id = int(row["id"]) if row else None
        if user_id is None:
            return {"ok": False, "sebep": "kullanici yok"}

        # IKI CAGRI AYRI TRY ICINDE — bilincli.
        #
        # OLCULMUS HATA (10.09): ikisi ayni try icindeydi. Kosullu emir
        # ucu (/fapi/v1/openAlgoOrders) izin eksikligi, endpoint degisikligi
        # ya da hesap tipi yuzunden hata verdiginde BUTUN SENKRON dusuyordu
        # ve hicbir pozisyon yazilmiyordu. Kullanicinin gordugu sey "acik
        # islemin yok" oluyordu — oysa pozisyonlar cekilebilmisti.
        #
        # teshis() bu cagriyi zaten "olumcul degil" diye siniflandiriyordu;
        # yani teshis ile gercek kod birbirinin tersini soyluyordu. Ikisi
        # ayrisirsa teshise guvenilmez.
        #
        # Pozisyon cekilemezse senkron ANLAMSIZ -> durur.
        # Stop/hedef cekilemezse senkron EKSIK -> devam eder, eksigi soyler.
        try:
            ham = await binance_trade.position_rows()
        except Exception as exc:  # noqa: BLE001
            _state.update(hata=f"{type(exc).__name__}: {exc}", aktif=False)
            return {"ok": False, "sebep": "baglanti", "hata": str(exc)}

        algolar = []
        algo_hata = None
        try:
            algolar = await binance_trade.open_algo_orders()
        except Exception as exc:  # noqa: BLE001
            algo_hata = f"{type(exc).__name__}: {exc}"
            log.warning("kosullu emirler alinamadi (senkron devam ediyor): %s", algo_hata)
        _state["algo_hata"] = algo_hata

        borsada: Dict[str, Dict[str, Any]] = {}
        for p in (ham or []):
            miktar = _f(p.get("positionAmt"))
            if not miktar:
                continue
            sym = str(p.get("symbol") or "")
            borsada[sym] = {
                "symbol": sym,
                "side": "LONG" if miktar > 0 else "SHORT",
                "entry": _f(p.get("entryPrice")) or 0.0,
                "qty": abs(miktar),
                "leverage": int(_f(p.get("leverage")) or 0) or None,
                "mark": _f(p.get("markPrice")),
            }

        acik = {r["symbol"]: r for r in mentor_journal.acik_islemler(user_id)}
        acilan = kapanan = guncellenen = 0

        # --- 1) BORSADA VAR, GUNLUKTE YOK -> AC ------------------------
        for sym, poz in borsada.items():
            if sym in acik:
                continue
            if not poz["entry"]:
                continue
            stop, hedef = borsa_stop_hedef(algolar, sym, poz["side"],
                                           poz["entry"])
            try:
                baglam = await mentor_journal.baglam_al(sym)
            except Exception:  # noqa: BLE001
                baglam = {}
            now = db.now_ms()
            try:
                db.execute(
                    "INSERT INTO mentor_trades(user_id,symbol,side,opened_at,entry,"
                "initial_stop,initial_target,qty,leverage,gerekce,etiket,"
                "kural_uyumu,kontrol_listesi,baglam,bayraklar,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, sym, poz["side"], now, poz["entry"], stop, hedef,
                     poz["qty"], poz["leverage"], "", KAYNAK,
                     None, None, json.dumps(baglam, ensure_ascii=False),
                     json.dumps([], ensure_ascii=False), "open", now))
            except Exception as exc:  # noqa: BLE001
                # TEK BIR SEMBOL butun senkronu dusurmemeli. Onceden bu
                # INSERT korumasizdi: bir sembolde sema ya da veri sorunu
                # olsa cagri 500 donuyor ve DIGER pozisyonlar da hic
                # kaydedilmiyordu. Kullanicinin gordugu sey buydu.
                log.warning("gunluge alinamadi %s: %s", sym, exc)
                continue
            acilan += 1
            log.info("Gunluge otomatik alindi: %s %s giris=%s stop=%s",
                     sym, poz["side"], poz["entry"], stop or "YOK")

        # --- 2) GUNLUKTE ACIK, BORSADA YOK -> KAPAT --------------------
        for sym, kayit in acik.items():
            if sym in borsada:
                # Borsada stop sonradan kurulduysa ve kayitta yoksa ekle.
                # ONEMLI: var olan bir stop ASLA guncellenmez. initial_stop
                # tanimi geregi DONDURULMUS; kullanici stopu yukari cektiginde
                # R'yi yeniden hesaplamak klasik kendini kandirma bicimidir.
                if kayit.get("initial_stop") is None:
                    stop, hedef = borsa_stop_hedef(
                        algolar, sym, kayit["side"], float(kayit["entry"]))
                    if stop is not None:
                        db.execute(
                            "UPDATE mentor_trades SET initial_stop=?, "
                            "initial_target=COALESCE(initial_target,?) WHERE id=?",
                            (stop, hedef, kayit["id"]))
                        guncellenen += 1
                continue

            cikis = await _cikis_fiyati(sym, int(kayit["opened_at"]))
            if not cikis:
                # Cikis fiyati okunamadi: kaydi UYDURMA fiyatla kapatmaktansa
                # acik birakip bir sonraki turda tekrar dene.
                log.debug("cikis fiyati okunamadi, acik birakildi: %s", sym)
                continue
            try:
                await mentor_journal.kapat(user_id, int(kayit["id"]),
                                           cikis["price"], "borsa")
                kapanan += 1
                log.info("Gunlukte otomatik kapatildi: %s cikis=%s",
                         sym, round(cikis["price"], 8))
            except Exception as exc:  # noqa: BLE001
                log.warning("otomatik kapatma basarisiz %s: %s", sym, exc)

        _state.update(son=db.now_ms(), acilan=acilan, kapanan=kapanan,
                      guncellenen=guncellenen, acik=len(borsada), hata=None,
                      aktif=True, sure_ms=int((time.monotonic() - t0) * 1000))
        return {"ok": True, "acilan": acilan, "kapanan": kapanan,
                "guncellenen": guncellenen, "acik": len(borsada)}


async def _loop() -> None:
    await asyncio.sleep(25)
    while True:
        try:
            if runtime.get().get("mentor_gunluk", {}).get("otomatik_senkron", True):
                await esitle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("mentor senkron dongusu hatasi (dongu devam ediyor): %s", exc)
        await asyncio.sleep(ARALIK_SANIYE)


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="vortex-mentor-sync")
        log.info("Mentor Binance senkronu baslatildi (her %ds)", ARALIK_SANIYE)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None
