"""BINANCE GECMISI ICERI AKTARMA — kapanmis islemleri gunluge tasir.

NEDEN VAR
---------
Gunluk yalnizca bugunden itibaren doldugu icin karne 20 islemlik esige
haftalarca ulasamiyordu. Oysa kullanicinin GERCEK sicili zaten Binance'te
duruyor. Bu modul onu ceker.

Asil kazanc hiz degil, TARAFSIZLIK: kullanici kazandigi islemleri hatirlar
ve paylasir, kaybettiklerini genelde paylasmaz. Elle girise dayali her
gunluk bu yanliligi tasir. Borsa gecmisi kazanani da kaybedeni de esit
sekilde tasidigi icin sicil ilk defa tarafsiz olur.

DURUSTLUK SINIRI — STOP YOK
---------------------------
Binance gecmisinde giris ve cikis var ama STOP YOK. Stop olmadan R
hesaplanamaz. Iki kotu secenek ve bir dogru secenek vardi:

  (a) Stopu tahmin etmek       -> olcumu kirletir, R'yi uydurma yapar
  (b) Islemleri hic almamak    -> tarafsiz sicili elden kacirmak
  (c) Almak ama R'yi BOS birakmak

(c) secildi. Bu islemlerde result_r NULL kalir; R tabanli metrikler
(SQN, bootstrap, R ortalamasi) onlari HIC SAYMAZ. Buna karsilik dolar
bazli metrikler, sure, sembol, yon ve saat kirilimlari calisir.
Kullanici isterse sonradan stopunu yazip R'yi acabilir.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from .. import db

log = logging.getLogger("vortex.mentor.import")

ETIKET = "binance-gecmis"
MS_GUN = 86_400_000


async def _pnl_sembolleri(baslangic: int, bitis: int) -> List[str]:
    """Bu araliкta gerceklesmis kar/zarari olan semboller.

    userTrades Binance'te SEMBOL ZORUNLU istiyor, yani once hangi
    sembollerde islem oldugunu bilmek gerekiyor. income ucu sembol
    istemiyor ve tam olarak bunu veriyor.
    """
    from . import binance_trade
    out: set = set()
    imlec = baslangic
    for _ in range(12):          # emniyet: sonsuz donguye girme
        kayitlar = await binance_trade.signed("/fapi/v1/income", {
            "incomeType": "REALIZED_PNL", "startTime": imlec,
            "endTime": bitis, "limit": 1000})
        if not isinstance(kayitlar, list) or not kayitlar:
            break
        for k in kayitlar:
            if k.get("symbol"):
                out.add(str(k["symbol"]))
        if len(kayitlar) < 1000:
            break
        imlec = int(kayitlar[-1].get("time", imlec)) + 1
    return sorted(out)


def _tur_ayikla(fills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tek tek dolumlardan TAM TUR (round-trip) islemler cikarir.

    Algoritma: dolumlar zaman sirasinda islenir, net pozisyon takip edilir.
    Net pozisyon sifira dondugunde bir tur kapanmis demektir.

    Giris fiyati = pozisyonu ACAN dolumlarin miktar agirlikli ortalamasi
    Cikis fiyati = pozisyonu KAPATAN dolumlarin miktar agirlikli ortalamasi
    Yon = turu acan ilk dolumun yonu

    Kismi kapanislar da dogru calisir: tur, net sifira dondugu anda biter.
    """
    turlar: List[Dict[str, Any]] = []
    net = 0.0
    acilis: List[tuple] = []     # (miktar, fiyat)
    kapanis: List[tuple] = []
    pnl = 0.0
    komisyon = 0.0
    acilis_ts: Optional[int] = None
    yon: Optional[str] = None

    for f in sorted(fills, key=lambda x: int(x.get("time", 0))):
        try:
            q = float(f.get("qty", 0) or 0)
            px = float(f.get("price", 0) or 0)
            ts = int(f.get("time", 0))
        except (TypeError, ValueError):
            continue
        if q <= 0 or px <= 0:
            continue
        isaret = 1.0 if str(f.get("side", "")).upper() == "BUY" else -1.0
        try:
            pnl += float(f.get("realizedPnl", 0) or 0)
            komisyon += float(f.get("commission", 0) or 0)
        except (TypeError, ValueError):
            pass

        onceki = net
        if abs(onceki) < 1e-12:
            # Yeni tur basliyor
            yon = "LONG" if isaret > 0 else "SHORT"
            acilis_ts = ts
            acilis, kapanis = [], []
            pnl = float(f.get("realizedPnl", 0) or 0)
            komisyon = float(f.get("commission", 0) or 0)

        if (isaret > 0 and net >= 0) or (isaret < 0 and net <= 0):
            acilis.append((q, px))      # pozisyonu buyutuyor
        else:
            kapanis.append((q, px))     # pozisyonu kapatiyor

        net += isaret * q

        if abs(net) < 1e-9 and acilis and kapanis:
            ag_a = sum(m for m, _ in acilis) or 1.0
            ag_k = sum(m for m, _ in kapanis) or 1.0
            turlar.append({
                "side": yon,
                "entry": sum(m * p for m, p in acilis) / ag_a,
                "exit_price": sum(m * p for m, p in kapanis) / ag_k,
                "qty": ag_a,
                "opened_at": acilis_ts,
                "closed_at": ts,
                "pnl_usdt": round(pnl - komisyon, 6),
                "brut_pnl": round(pnl, 6),
                "komisyon": round(komisyon, 6),
            })
            net = 0.0
            acilis, kapanis, pnl, komisyon = [], [], 0.0, 0.0
            acilis_ts, yon = None, None
    return turlar


async def onizle(user_id: int, gun: int = 30) -> Dict[str, Any]:
    """Iceri aktarilabilecek kapanmis islemleri LISTELER, yazmaz."""
    from . import binance_trade
    if not binance_trade.has_keys():
        return {"ok": False, "sebep": "anahtar_yok",
                "mesaj": "Binance API anahtari bagli degil.", "islemler": []}

    bitis = db.now_ms()
    baslangic = bitis - max(1, min(gun, 180)) * MS_GUN
    try:
        semboller = await _pnl_sembolleri(baslangic, bitis)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "sebep": "baglanti",
                "mesaj": f"Gecmis okunamadi: {type(exc).__name__}", "islemler": []}

    sem = asyncio.Semaphore(3)
    hepsi: List[Dict[str, Any]] = []

    async def cek(sym: str) -> None:
        async with sem:
            try:
                fills = await binance_trade.signed("/fapi/v1/userTrades", {
                    "symbol": sym, "startTime": baslangic,
                    "endTime": bitis, "limit": 1000})
            except Exception as exc:  # noqa: BLE001
                log.debug("userTrades %s: %s", sym, exc)
                return
        if not isinstance(fills, list):
            return
        for t in _tur_ayikla(fills):
            t["symbol"] = sym
            hepsi.append(t)

    await asyncio.gather(*(cek(s) for s in semboller))

    # ZATEN ICERI ALINMIS OLANLARI ISARETLE.
    # Ayni islemi iki kez almak karneyi bozar; kimlik olarak
    # sembol + kapanis zamani kullaniyoruz (saniye toleransiyla).
    mevcut = db.query(
        "SELECT symbol, closed_at FROM mentor_trades WHERE user_id=? "
        "AND closed_at IS NOT NULL", (user_id,))
    anahtarlar = {(r["symbol"], int(r["closed_at"]) // 1000) for r in mevcut}
    for t in hepsi:
        t["zaten_var"] = (t["symbol"], int(t["closed_at"]) // 1000) in anahtarlar

    hepsi.sort(key=lambda x: -x["closed_at"])
    yeni = [t for t in hepsi if not t["zaten_var"]]
    return {
        "ok": True, "gun": gun,
        "sembol_sayisi": len(semboller),
        "toplam": len(hepsi), "yeni": len(yeni),
        "toplam_pnl": round(sum(t["pnl_usdt"] for t in yeni), 4),
        "islemler": hepsi[:300],
    }


def ice_aktar(user_id: int, islemler: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Secilen turlari gunluge yazar.

    STOP YOKSA result_r NULL kalir — R tabanli hicbir istatistige
    girmez. Bu bilincli: eksik veriyi tahminle doldurmak olcumu kirletir.
    """
    from . import mentor_journal
    eklendi = atlandi = r_li = 0
    now = db.now_ms()

    mevcut = db.query(
        "SELECT symbol, closed_at FROM mentor_trades WHERE user_id=? "
        "AND closed_at IS NOT NULL", (user_id,))
    anahtarlar = {(r["symbol"], int(r["closed_at"]) // 1000) for r in mevcut}

    for t in islemler:
        try:
            sym = str(t["symbol"]).upper()
            side = str(t["side"]).upper()
            entry = float(t["entry"])
            exit_price = float(t["exit_price"])
            opened = int(t["opened_at"])
            closed = int(t["closed_at"])
        except (KeyError, TypeError, ValueError):
            atlandi += 1
            continue
        if (sym, closed // 1000) in anahtarlar:
            atlandi += 1
            continue

        stop = t.get("initial_stop")
        try:
            stop = float(stop) if stop not in (None, "", 0) else None
        except (TypeError, ValueError):
            stop = None
        # Stop yonu tutarsizsa yok say — yanlis stop, yanlis R demek.
        if stop is not None:
            if (side == "LONG" and stop >= entry) or (side == "SHORT" and stop <= entry):
                stop = None

        r = mentor_journal.r_multiple(side, entry, stop, exit_price) if stop else None
        if r is not None:
            r_li += 1

        def _yaz(stop_degeri: Optional[float]) -> None:
            db.execute(
                "INSERT INTO mentor_trades(user_id,symbol,side,opened_at,closed_at,"
                "entry,initial_stop,qty,exit_price,exit_reason,gerekce,etiket,"
                "kural_uyumu,baglam,result_r,pnl_usdt,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, sym, side, opened, closed, entry,
                 stop_degeri,
                 t.get("qty"), exit_price, "binance",
                 "Binance gecmisinden aktarildi", ETIKET,
                 None,                               # kural uyumu BILINMIYOR
                 json.dumps({"kaynak": "binance_import",
                             "brut_pnl": t.get("brut_pnl"),
                             "komisyon": t.get("komisyon"),
                             "stop_yok": stop is None}, ensure_ascii=False),
                 round(r, 4) if r is not None else None,
                 t.get("pnl_usdt"), "closed", now))

        try:
            _yaz(stop)                               # stop yoksa NULL
        except Exception as exc:                     # noqa: BLE001
            # ESKI VERITABANI GERI DUSUSU: mentor_trades daha once
            # "initial_stop REAL NOT NULL" ile olusmus olabilir; o semada
            # NULL insert patlar. Islemi kaybetmemek icin giris fiyatiyla
            # yaziyoruz. result_r zaten None oldugu icin R istatistikleri
            # etkilenmiyor, ve baglam.stop_yok bayragi gercegi soyluyor.
            if stop is None:
                log.debug("NULL stop reddedildi (eski sema), giris fiyatiyla "
                          "yaziliyor: %s", exc)
                _yaz(entry)
            else:
                raise
        anahtarlar.add((sym, closed // 1000))
        eklendi += 1

    log.info("Binance gecmisi aktarildi: %d islem (%d tanesi R'li), %d atlandi",
             eklendi, r_li, atlandi)
    return {"ok": True, "eklendi": eklendi, "r_li": r_li, "atlandi": atlandi}
