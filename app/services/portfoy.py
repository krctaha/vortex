"""PORTFOY — Binance hesabinin CANLI ozeti: bakiye + acik pozisyonlar.

NEDEN AYRI BIR SERVIS
=====================
Bakiye verisi sistemde vardi ama uc sorun yuzunden kullaniciya hic
ulasmiyordu:

  1. Yalnizca /api/trading/status uzerinden veriliyordu ve o uc
     `admin_user` ile kilitli.
  2. Arayuzde tek bir yere, kapali bir <details> icine, BIR KEZ
     ciziliyordu — tazeleme yoktu.
  3. Ana ekranda "portfoy" diye bir kavram yoktu.

Sonuc: kullanici "bakiyemi Binance'ten otomatik gorsun" dedi, cunku
sistemde duran veri ona hic gorunmuyordu.

TEK CAGRI, IKI CEVAP
====================
/fapi/v2/account hem bakiyeyi hem pozisyonlari ayni yanitta veriyor.
Ayri ayri cekmek ayni bilgiyi iki kez odemek olurdu; ustelik iki cagri
arasinda hesap degisirse bakiye ile pozisyonlar birbirini tutmaz ve
ekranda tutarsiz bir tablo cikar.

ONBELLEK NEDEN VAR
==================
Arayuz bunu 15 saniyede bir soruyor ve birden fazla panel ayni veriyi
kullaniyor. Onbelleksiz her panel ayri bir imzali istek acardi; Binance'in
agirlik butcesi (2400/dk) boyle seyler icin harcanmamali.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from . import binance_trade

log = logging.getLogger("vortex.portfoy")

# 10 saniye: fiyat WebSocket'ten zaten canli akiyor, bu uc yalnizca
# BAKIYE ve pozisyon LISTESI icin. Bakiye saniyede bir degismiyor.
ONBELLEK_SN = 10.0
_onbellek: Dict[str, Any] = {"ts": 0.0, "veri": None}


def _f(x: Any, varsayilan: float = 0.0) -> float:
    """Binance sayilari STRING dondurur.

    `float(x or 0)` yeterli degil: "0.00000" bos degil ama sifir. Bu
    ayrimi kacirmak canli islem tarafinda giris fiyatini 0 yazdiran
    bir hataya yol acmisti.
    """
    try:
        return float(x)
    except (TypeError, ValueError):
        return varsayilan


def _pozisyon(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    miktar = _f(p.get("positionAmt"))
    if miktar == 0:
        return None
    giris = _f(p.get("entryPrice"))
    mark = _f(p.get("markPrice")) or giris
    yon = "LONG" if miktar > 0 else "SHORT"
    isaret = 1.0 if miktar > 0 else -1.0
    # Getiri yuzdesi MARJA gore, notional'a gore degil: kullanicinin
    # Binance ekraninda gordugu ROI da boyle hesaplaniyor. Ikisi farkli
    # olsa kullanici hangisinin dogru oldugunu bilemezdi.
    kaldirac = _f(p.get("leverage")) or 1.0
    notional = abs(miktar) * mark
    marj = notional / kaldirac if kaldirac > 0 else 0.0
    pnl = _f(p.get("unRealizedProfit"))
    return {
        "symbol": str(p.get("symbol") or ""),
        "yon": yon,
        "miktar": abs(miktar),
        "giris": giris,
        "mark": mark,
        "kaldirac": int(kaldirac) if kaldirac else None,
        "notional": round(notional, 2),
        "marj": round(marj, 2),
        "pnl": round(pnl, 2),
        "roi": round(pnl / marj * 100.0, 2) if marj > 0 else None,
        # Fiyat girise gore ne kadar lehte/aleyhte gitti.
        "degisim": round((mark - giris) / giris * 100.0 * isaret, 2) if giris > 0 else None,
    }


async def ozet(taze: bool = False) -> Dict[str, Any]:
    """Bakiye + acik pozisyonlar. Tek imzali cagri, kisa onbellekli."""
    simdi = time.monotonic()
    if not taze and _onbellek["veri"] and simdi - _onbellek["ts"] < ONBELLEK_SN:
        return {**_onbellek["veri"], "onbellek": True,
                "yas_sn": round(simdi - _onbellek["ts"], 1)}

    if not binance_trade.has_keys():
        # HATA DEGIL, DURUM. Arayuz bunu "bağla" cagrisina cevirebilsin
        # diye ayri bir sebep kodu donuyor; genel bir hata mesaji
        # kullaniciya ne yapacagini soylemez.
        return {"ok": False, "bagli": False, "sebep": "anahtar_yok",
                "mesaj": "Binance API anahtarı bağlı değil.",
                "pozisyonlar": []}

    try:
        acc = await binance_trade.signed("/fapi/v2/account")
    except Exception as exc:  # noqa: BLE001
        log.warning("portfoy cekilemedi: %s", exc)
        # BAYAT VERI, HIC VERIDEN IYI — ama BAYAT OLDUGU YAZILARAK.
        # Sessizce eski bakiyeyi gostermek, kullaniciya guncel olmayan bir
        # sayiyi guncelmis gibi sunmak olurdu.
        if _onbellek["veri"]:
            return {**_onbellek["veri"], "bayat": True,
                    "mesaj": f"Binance'e ulaşılamadı ({type(exc).__name__}) — "
                             f"aşağıdaki rakamlar son başarılı okumadan.",
                    "yas_sn": round(simdi - _onbellek["ts"], 1)}
        return {"ok": False, "bagli": True, "sebep": "baglanti",
                "mesaj": f"Binance'e ulaşılamadı: {type(exc).__name__}",
                "pozisyonlar": []}

    pozisyonlar: List[Dict[str, Any]] = []
    for p in (acc.get("positions") or []):
        satir = _pozisyon(p)
        if satir:
            pozisyonlar.append(satir)
    # Buyuk pozisyon uste: goz once en cok para bagli olani gormeli.
    pozisyonlar.sort(key=lambda x: -x["notional"])

    cuzdan = _f(acc.get("totalWalletBalance"))
    gerceklesmemis = _f(acc.get("totalUnrealizedProfit"))
    veri = {
        "ok": True, "bagli": True,
        "cuzdan": round(cuzdan, 2),
        "kullanilabilir": round(_f(acc.get("availableBalance")), 2),
        "gerceklesmemis": round(gerceklesmemis, 2),
        # Ozkaynak = cuzdan + gerceklesmemis. Portfoy degeri budur;
        # cuzdan tek basina acik pozisyonlarin kar/zararini icermez ve
        # kullaniciya oldugundan farkli bir toplam gosterir.
        "ozkaynak": round(cuzdan + gerceklesmemis, 2),
        "marj_bakiyesi": round(_f(acc.get("totalMarginBalance")), 2),
        "kullanilan_marj": round(_f(acc.get("totalPositionInitialMargin")), 2),
        "islem_yetkisi": bool(acc.get("canTrade")),
        "pozisyon_sayisi": len(pozisyonlar),
        "pozisyonlar": pozisyonlar,
        "toplam_notional": round(sum(x["notional"] for x in pozisyonlar), 2),
        "guncelleme_ms": int(time.time() * 1000),
    }
    # Kaldirac orani: acik notional / ozkaynak. Kullanicinin GERCEK
    # riski bu; tek tek pozisyon kaldiraclari bunu gostermez.
    veri["kaldirac_orani"] = (round(veri["toplam_notional"] / veri["ozkaynak"], 2)
                              if veri["ozkaynak"] > 0 else None)
    _onbellek.update(ts=simdi, veri=veri)
    return veri
