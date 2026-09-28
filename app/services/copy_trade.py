"""Copy Trade — PARA katmani.

29.08.2026 ayrimi. Oncesinde hesap buyuklugune bagli tum sinirlar (islem
basina risk, margin, kaldirac, gunluk kayip) ANA MOTORUN
icindeydi. Sonucu suydu: gunluk kayip limiti dolunca motor yeni kurulum
bulmayi da birakiyor gibi gorunuyordu ve olcum akisi hesap bakiyesine
takiliyordu.

Ayrim su:

    ANA MOTOR (tsmom_engine)   -> kurulum bulur, arastirma kaydi acar,
                                  kapatir. PARAYA BAKMAZ. Sinirlari
                                  strateji sinirlaridir (kac pozisyon,
                                  ayni yonde kac tane, taramada kac yeni).

    COPY TRADE (bu dosya)      -> o sinyalleri PARAYLA aynalar. Hesap
                                  buyuklugune bagli her sinir burada.

Neden onemli: olcum akisi ile uygulama akisi ayni seye baglanirsa,
kucuk bir hesap kotu bir istatistik uretir. Strateji "bugun 3 kurulum
buldu" der, hesap "param yetmedi" der — ikisi ayri cumle olmali.

Bu katman daima kagit esi tutar; canli emir bunun arkasindaki ayri ve
asenkron ``live_trade`` katmaninda acilir. Boylece canli uygulama ile
stratejinin ileri testi birbirine karismaz.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .. import db, runtime

log = logging.getLogger("vortex.copy")

ENGINE_TAG = "tsmom"          # meta.engine degeri: kimin actigini soyler
MS_DAY = 86_400_000


# --------------------------------------------------------------------------- #
# Sayaclar — hepsi YALNIZCA motorun kendi actigi kagit islemleri sayar
# --------------------------------------------------------------------------- #
def _is_mine(row: Dict[str, Any]) -> bool:
    """Bu kagit islem motorun mu.

    Kapatma islemi meta.research_id uzerinden calisiyor; yani motor yalnizca
    KENDI actigini kapatabiliyor. O yuzden kapasite sayarken de yalnizca
    kendininkini saymali. Aksi halde elle acilmis bir pozisyon kontenjani
    sonsuza kadar isgal ediyordu: motor onu kapatamaz, kapatamadigi icin
    yer acilmaz, yer acilmadigi icin yeni kurulum aynalanmaz.
    """
    try:
        meta = json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        return False
    return meta.get("engine") == ENGINE_TAG


def open_rows() -> List[Dict[str, Any]]:
    return [dict(r) for r in db.query(
        "SELECT * FROM trades WHERE status='open' AND mode='paper'") if _is_mine(r)]


def open_count() -> int:
    return len(open_rows())


def open_risk() -> float:
    """Acik pozisyonlarin toplam stop riskini bilgi amacli hesapla."""
    return round(sum(float(r["risk_usdt"] or 0) for r in open_rows()), 4)


def day_start_ms() -> int:
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                            0, 0, 0, lt.tm_wday, lt.tm_yday, -1)) * 1000)


def realized_today() -> float:
    """Bugun KAPANMIS kagit islemlerin net sonucu (dolar). Negatif = zarar."""
    row = db.query_one(
        "SELECT COALESCE(SUM(pnl_usdt),0) s FROM trades "
        "WHERE status='closed' AND mode='paper' AND closed_at>=?", (day_start_ms(),))
    return round(float(row["s"]) if row else 0.0, 4)


def daily_pnl(days: int = 14) -> List[Dict[str, Any]]:
    """Gun gun gerceklesmis kagit sonucu. Ana paneldeki karne bunu kullaniyor."""
    rows = db.query(
        "SELECT closed_at, pnl_usdt, r_multiple FROM trades "
        "WHERE status='closed' AND mode='paper' AND closed_at>=? ORDER BY closed_at",
        (day_start_ms() - (days - 1) * MS_DAY,))
    buckets: Dict[str, Dict[str, float]] = {}
    for r in rows:
        key = time.strftime("%Y-%m-%d", time.localtime((r["closed_at"] or 0) / 1000))
        b = buckets.setdefault(key, {"pnl": 0.0, "r": 0.0, "n": 0})
        b["pnl"] += float(r["pnl_usdt"] or 0)
        b["r"] += float(r["r_multiple"] or 0)
        b["n"] += 1
    out = []
    for i in range(days - 1, -1, -1):
        key = time.strftime("%Y-%m-%d", time.localtime((day_start_ms() - i * MS_DAY) / 1000))
        b = buckets.get(key, {"pnl": 0.0, "r": 0.0, "n": 0})
        out.append({"day": key, "pnl": round(b["pnl"], 2),
                    "r": round(b["r"], 3), "n": int(b["n"])})
    return out


# --------------------------------------------------------------------------- #
# Aynalama
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    """Copy Trade aynalamasi acik mi. Motordan AYRI anahtar.

    Kapatildiginda motor calismaya ve olcmeye devam eder; sadece para
    tarafi durur. Bu ayrim bilincli: olcumu durdurmadan uygulamayi
    durdurabilmek gerekiyor.
    """
    return bool(db.get_setting("copy_trade_enabled", True))


def set_enabled(value: bool) -> None:
    db.set_setting("copy_trade_enabled", bool(value))


def _risk_plan(t_abs: float, ccfg: Dict[str, Any], max_risk: float) -> Dict[str, Any]:
    """Sinyal gücüne göre tam veya keşif riski üret."""
    full_at = max(float(ccfg.get("min_t_to_mirror", 0) or 0),
                  float(ccfg.get("full_risk_min_t", 1.5) or 1.5))
    if t_abs >= full_at:
        return {"tier": "standard", "risk_cap": float(max_risk), "full_at": full_at}
    fraction = max(.1, min(float(ccfg.get("exploration_risk_fraction", .35)), 1.0))
    return {"tier": "exploration", "risk_cap": float(max_risk) * fraction,
            "full_at": full_at, "fraction": fraction}


def _exploration_open_count() -> int:
    count = 0
    for row in open_rows():
        try:
            if json.loads(row.get("meta") or "{}").get("risk_tier") == "exploration":
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def mirror(decision: Dict[str, Any], research_id: int) -> Dict[str, Any]:
    """Motorun bulduğu kurulumu PARAYLA aynalar.

    Doner: {"ok": bool, "reason": str, "margin": float, "risk": float}
    ok=False olmasi motorun basarisiz oldugu anlamina GELMEZ — arastirma
    kaydi zaten acildi ve olcum devam ediyor. Yalnizca "bu sinyali paranla
    takip etmedik" demektir.

    Boyutlandirma: pozisyon, stop mesafesi ile islem basina izin verilen
    risk dolara esitlenecek sekilde olculuyor, sonra max_position_margin
    ile tavanlaniyor. Yani stop genisse pozisyon kucuk, darsa buyuk —
    risk sabit kaliyor.
    """
    rcfg = runtime.get()["risk"]
    ccfg = runtime.get()["copy"]

    def no(reason: str) -> Dict[str, Any]:
        return {"ok": False, "reason": reason, "margin": 0.0, "risk": 0.0}

    if not enabled():
        return no("copy trade kapali")
    if not bool(ccfg.get("auto_mirror", True)):
        return no("otomatik aynalama kapali")

    # GUVEN ESIGI — para tarafina gecis citasi.
    # Motor |t| >= 1.0 olan HER kurulumu acar ve olcer; bu dogru, cunku
    # olcum akisini daraltmak istatistigi bozar. Ama parayla takip etmek
    # ayri bir karar: yalnizca sinyalin gucune yeterince guvendiklerimiz
    # gecer. Bu esik motorun kendi esiginden BAGIMSIZ ve daha yuksek.
    cita = float(ccfg.get("min_t_to_mirror", 0) or 0)
    try:
        t_abs = abs(float(decision.get("t_stat") or 0))
    except (TypeError, ValueError):
        t_abs = 0.0
    if cita > 0 and t_abs < cita:
        return no(f"guven esiginin altinda (|t|={t_abs:.2f} < {cita:.2f}) — "
                  f"motor izlemeye devam ediyor, parayla takip edilmiyor")

    plan = _risk_plan(t_abs, ccfg, float(rcfg["max_risk_per_trade"]))
    if plan["tier"] == "exploration":
        exploration_cap = int(ccfg.get("max_exploration_open", 1))
        if exploration_cap <= 0:
            return no("kontrollü agresif işlem bandı kapalı")
        if _exploration_open_count() >= exploration_cap:
            return no(f"kontrollü agresif slot dolu ({exploration_cap})")

    admin = db.query_one(
        "SELECT * FROM users WHERE role='admin' AND is_active=1 ORDER BY id LIMIT 1")
    if not admin:
        return no("yonetici hesabi yok")

    mine = open_count()
    cap_n = int(ccfg.get("max_open_trades", 6))
    if mine >= cap_n:
        return no(f"ayni anda acik islem siniri dolu ({mine}/{cap_n})")

    # GUNLUK ZARAR DURDURUCUSU — GERCEKLESMIS zarari olcer, acik riski degil.
    realized = realized_today()
    limit = float(rcfg["daily_loss_limit"])
    if realized <= -limit:
        return no(f"gunluk zarar durdurucusu ({realized:.2f}$ gerceklesmis zarar "
                  f">= {limit:.2f}$ limit) — yarin sifirlanir")

    entry, stop = float(decision["entry"]), float(decision["stop"])
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return no("gecersiz stop mesafesi")

    # LIKIDASYON KONTROLU BURADA — motorda degil.
    # Stop, likidasyon mesafesinin icinde kalmali; yoksa stop hicbir zaman
    # tetiklenmez, pozisyon once likide olur. Ama bunun cozumu kurulumu
    # ATMAK degil KALDIRACI DUSURMEK: ayni kurulum 3x'te tasinabiliyorsa
    # 5x'te tasinamadigi icin cope atilmasi anlamsizdi.
    stop_pct = stop_distance / entry * 100
    tcfg = runtime.get()["tsmom"]
    guvenli = float(tcfg.get("liq_safety", 0.65))
    istenen = int(rcfg["default_leverage"])
    # stop_pct <= guvenli * 100/lev  ->  lev <= guvenli * 100 / stop_pct
    max_lev = int(guvenli * 100.0 / stop_pct) if stop_pct > 0 else istenen
    leverage = max(1, min(istenen, max_lev))
    if max_lev < 1:
        return no(f"stop likidasyondan uzak (%{stop_pct:.1f}) — 1x'te bile taşınamaz")
    dusuruldu = leverage < istenen

    risk_sized_margin = plan["risk_cap"] * entry / (stop_distance * leverage)
    margin = round(min(rcfg["max_position_margin"], risk_sized_margin), 2)
    if margin < 1:
        return no("margin 1$ altinda kaldi")
    qty = margin * leverage / entry
    risk = stop_distance * qty

    paper_trade_id = db.execute(
        "INSERT INTO trades(user_id,symbol,side,status,mode,entry,stop,take_profit,margin_usdt,"
        "leverage,margin_type,qty,risk_usdt,opened_at,interval,note,meta) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (admin["id"], decision["symbol"], decision["side"], "open", "paper", entry, stop,
         decision["target"], margin, leverage, "ISOLATED", qty, risk, db.now_ms(), "1d",
         f"TSMOM otomatik · t={decision['t_stat']:+.2f}",
         json.dumps({"engine": ENGINE_TAG, "research_id": research_id,
                     "setup_source": ("rsi_radar+tsmom" if decision.get("rsi_lead") else "tsmom"),
                     "rsi_lead": decision.get("rsi_lead"),
                     "t_stat": decision["t_stat"],
                     "total_cost_r": decision["total_cost_r"],
                     "funding_bp": decision["funding"].get("bp"),
                     "horizon_days": decision["horizon_days"],
                     "risk_tier": plan["tier"],
                     "risk_cap_usdt": round(plan["risk_cap"], 4),
                     "stop_pct": round(stop_pct, 2),
                     "leverage_requested": istenen,
                     "leverage_used": leverage}, ensure_ascii=False)))
    log.info("%s %s kagit pozisyon aynalandi (risk %.2f$, margin %.2f$, %dx%s)",
             decision["symbol"], decision["side"], risk, margin, leverage,
             " [kaldirac dusuruldu]" if dusuruldu else "")
    return {"ok": True, "reason": "", "margin": margin, "risk": round(risk, 4),
            "risk_tier": plan["tier"], "risk_cap": round(plan["risk_cap"], 4),
            "leverage": leverage, "leverage_reduced": dusuruldu,
            "qty": qty, "user_id": int(admin["id"]),
            "paper_trade_id": paper_trade_id}


def close_for_research(research_id: int, exit_price: float, result_r: float,
                       closed_at: int, reason: str) -> bool:
    """Arastirma kaydi kapaninca ona bagli kagit pozisyonu da kapatir.

    Bagi meta.research_id tasiyor. Ayri kapatilsalardi acik pozisyon listesi
    gercekte kapanmis kurulumlari gostermeye devam eder, risk butcesi de
    bosalmazdi.
    """
    for r in open_rows():
        try:
            meta = json.loads(r["meta"] or "{}")
        except (TypeError, ValueError):
            continue
        if int(meta.get("research_id") or 0) != int(research_id):
            continue
        direction = 1 if r["side"] == "LONG" else -1
        qty = float(r["qty"] or 0)
        gross_pnl = (float(exit_price) - float(r["entry"])) * direction * qty
        # ``result_r`` arastirma defterinin maliyet sonrasi net R degeridir.
        # Kagit portfoyde brut fiyat hareketini yazmak performansi sisiriyor ve
        # karar motorunu gercekte olmayan bir edge ile besliyordu.
        risk_usdt = float(r["risk_usdt"] or 0)
        if risk_usdt <= 0:
            risk_usdt = abs(float(r["entry"]) - float(r["stop"] or r["entry"])) * qty
        pnl = float(result_r) * risk_usdt
        meta["exit_reason"] = reason
        meta["gross_pnl_usdt"] = round(gross_pnl, 6)
        # Fonlama lehteyse toplam maliyet negatif olabilir; bunu sıfıra
        # kırpmak da muhasebeyi çarpıtır.
        meta["estimated_cost_usdt"] = round(gross_pnl - pnl, 6)
        meta["pnl_accounting"] = "net_estimated_costs"
        db.execute(
            "UPDATE trades SET status='closed',exit_price=?,pnl_usdt=?,r_multiple=?,"
            "closed_at=?,meta=? WHERE id=?",
            (exit_price, round(pnl, 4), result_r, closed_at,
             json.dumps(meta, ensure_ascii=False), r["id"]))
        log.info("%s kagit islem kapandi (%s, net %.3fR, brut %.2f$, net %.2f$)",
                 r["symbol"], reason, result_r, gross_pnl, pnl)
        return True
    return False


# --------------------------------------------------------------------------- #
# Durum
# --------------------------------------------------------------------------- #
def status() -> Dict[str, Any]:
    rcfg = runtime.get()["risk"]
    ccfg = runtime.get()["copy"]
    from .binance_trade import has_keys, key_hint          # gec import: dongu olmasin
    from .live_trade import enabled as live_enabled, open_rows as live_rows
    live = live_rows()
    live_on = live_enabled()
    live_started = db.get_setting("live_execution_changed_at")
    fresh_qualified = 0
    if live_started:
        for row in db.query(
                "SELECT meta FROM research_trades WHERE source=? AND created_at>=?",
                (ENGINE_TAG, int(live_started))):
            try:
                m = json.loads(row["meta"] or "{}")
                if abs(float(m.get("t_stat") or 0)) >= float(ccfg.get("min_t_to_mirror", 0) or 0):
                    fresh_qualified += 1
            except (TypeError, ValueError):
                continue
    live_open = sum(1 for r in live if r["status"] == "open")
    live_reason = ""
    if live_on and live_open == 0 and fresh_qualified == 0:
        live_reason = ("canlı açıldıktan sonra güven eşiğini geçen yeni kurulum oluşmadı; "
                       "eski sinyaller geriye dönük açılmadı")
    return {
        "enabled": enabled(),
        "auto_mirror": bool(ccfg.get("auto_mirror", True)),
        "open_trades": open_count(),
        "max_open_trades": int(ccfg.get("max_open_trades", 6)),
        "min_t_to_mirror": float(ccfg.get("min_t_to_mirror", 0) or 0),
        "full_risk_min_t": float(ccfg.get("full_risk_min_t", 1.5)),
        "exploration_risk_fraction": float(ccfg.get("exploration_risk_fraction", .35)),
        "max_exploration_open": int(ccfg.get("max_exploration_open", 1)),
        "exploration_open": _exploration_open_count(),
        "open_risk": open_risk(),
        "realized_today": realized_today(),
        "daily_loss_limit": float(rcfg["daily_loss_limit"]),
        "max_risk_per_trade": float(rcfg["max_risk_per_trade"]),
        "max_position_margin": float(rcfg["max_position_margin"]),
        "default_leverage": int(rcfg["default_leverage"]),
        "total_margin": float(rcfg.get("total_margin", 380.0)),
        "daily_pnl": daily_pnl(14),
        "has_keys": has_keys(),
        "key_hint": key_hint(),
        "live_enabled": live_on,
        "live_open": live_open,
        "live_pending": sum(1 for r in live if r["status"] == "candidate"),
        "live_started_at": live_started,
        "fresh_qualified_since_live": fresh_qualified,
        "live_reason": live_reason,
        "last_block": db.get_setting("copy_last_block", ""),
    }


def note_block(reason: str) -> None:
    """Son aynalama engelini sakla — panelde 'neden aynalamadi' bunu gosteriyor."""
    db.set_setting("copy_last_block", reason or "")
