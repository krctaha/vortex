"""TSMOM + carry — VORTEX'in tek karar motoru.

Eski bes-gosterge skor motoru V2.4.0'da kaldirildi. Bu motor gunluk kapali
barlardan goreli momentum siralar; RSI yalnizca aday besler. Portfoy her dort
saatte yeniden degerlendirilir, fakat sinyali koruyan kazananlar sirf saat
doldu diye kesilmez. Daha guclu aday zayif bir acigi kontrollu rotasyonla
degistirebilir.

NE ISLEM ACAR
-------------
Her zaman KAGIT (paper) esi acar. Canli anahtar ayrica aciksa ve para
kapilari gecilmisse ayni yeni sinyal Binance USD-M'e de uygulanir.

BU BIR KANIT DEGILDIR. Motorun olculmus bir edge'i hala yok — kagit
islem acmasi "calisiyor" demek degil, "calistigini gorebiliyorsun" demek.
Canli emirler ayri kayit, borsada duran zorunlu stop ve acil kapatma
kurallariyla ``live_trade`` katmaninda tutulur.

RISK KILITLERI ayarlardan geliyor ve asilamaz: islem basina max risk,
gunluk kayip butcesi, pozisyon basina max margin, ayni anda acik islem
sayisi. Butce dolduysa kayit yine acilir ama pozisyon acilmaz.

RITIM
-----
Gunluk barlarla calisir, yani sinyal gunde bir kez degisir. Saatte bir
taramak fazlasiyla yeterli; daha sik taramak ayni sinyali tekrar tekrar
gormekten baska bir sey yapmaz. Cikislar ayri ve daha sik kontrol edilir.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from .. import db, runtime
from ..strategies import carry_tsmom as strat, regime_router
from . import (binance, copy_trade, decision_v4, feature_store, live_trade,
               research_metrics, rsi_context, telegram, webpush)

log = logging.getLogger("vortex.tsmom")

SOURCE = "tsmom"
DAILY_BARS = 300                # strat.MIN_BARS (191) + rahat pay
MS_DAY = 86_400_000

_task: Optional[asyncio.Task] = None
_watch_task: Optional[asyncio.Task] = None
_selftest_task: Optional[asyncio.Task] = None
_scan_lock = asyncio.Lock()
_state: Dict[str, Any] = {
    "scanning": False,
    "last_scan_at": None,
    "last_duration": None,
    "last_error": None,
    "last_universe": 0,
    "last_evaluated": 0,
    "last_passed": 0,
    "watchlist": [], "last_watch_at": None, "last_watch_opened": 0,
    "last_watch_checked": 0, "last_watch_exits": {}, "last_watch_ms": 0,
    "last_watch_saved": 0,
    "last_watch_error": None,
    "rsi_leads": [], "last_rsi_scan_at": None, "last_rsi_new_calls": 0,
    "last_rsi_checked": 0, "last_rsi_aligned": 0, "rsi_blocks": {},
    "last_saved": 0,
    "last_opened": 0,
    "last_candidates": 0,
    "block_reasons": {},
    "evren_teshis": {},
    "open_blocks": {},
    "open_detail": "",
    "top": [],
    "data_paused": False,
    "data_pause_reason": "",
    "last_rotation": {},
}

# Gunluk mumlar icin motor ici onbellek. binance.klines'in TTL'i 12 saniye —
# gunluk barda bu anlamsiz, her taramada butun evreni yeniden indirtir.
_watch_lock = asyncio.Lock()
_daily_cache: Dict[str, Any] = {}
_DAILY_TTL = 1800.0


def status() -> Dict[str, Any]:
    # Intraday Avci ana karar motorunun hizli piyasa katmanidir. Gec import,
    # servisler yuklenirken olusabilecek dairesel importu engeller.
    from . import hunter
    open_rows = db.query_one(
        "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    closed = db.query_one(
        "SELECT COUNT(*) c, AVG(COALESCE(net_result_r,result_r)) avg_r FROM research_trades "
        "WHERE source=? AND status='closed' AND result_r IS NOT NULL", (SOURCE,))
    rcfg = runtime.get()["risk"]
    return {
        **_state,
        "enabled": enabled(),
        "open": int(open_rows["c"]) if open_rows else 0,
        "open_trades": _open_paper_trades(),
        "candidates": int((db.query_one(
            "SELECT COUNT(*) c FROM trades WHERE status='candidate' AND note LIKE 'TSMOM%'")
            or {"c": 0})["c"]),
        # Acik risk artik yalnizca bilgilendirme; pozisyon kapasitesini
        # copy.max_open_trades belirler.
        "open_risk": _open_risk(),
        "realized_today": _realized_today(),             # bugun gerceklesmis kar/zarar
        "daily_loss_limit": rcfg["daily_loss_limit"],
        "max_risk_per_trade": rcfg["max_risk_per_trade"],
        "risk_used_today": _open_risk(),                 # eski ad, geriye uyum
        # Acik pozisyonlarin "neden hala acik" okumasi (son cikis turundan).
        "holds": _state.get("holds", []),
        "holds_at": _state.get("holds_at"),
        "exit_errors": _state.get("exit_errors", []),
        "rotation": db.get_setting("tsmom_rotation_last", {}) or {},
        "daily_pnl": daily_pnl(14),
        "closed": int(closed["c"]) if closed else 0,
        "avg_result_r": round(float(closed["avg_r"]), 4) if closed and closed["avg_r"] is not None else None,
        "max_open": int(runtime.get()["tsmom"]["max_open"]),
        "copy": copy_trade.status(),
        # Gozcu: tam tarama arasinda surekli calisan hizli dongu
        "last_watch_at": _state.get("last_watch_at"),
        "last_watch_checked": _state.get("last_watch_checked", 0),
        "last_watch_saved": _state.get("last_watch_saved", 0),
        "watchlist": _state.get("watchlist", []),
        "rsi_leads": _state.get("rsi_leads", []),
        "last_rsi_scan_at": _state.get("last_rsi_scan_at"),
        "last_rsi_checked": _state.get("last_rsi_checked", 0),
        "last_rsi_aligned": _state.get("last_rsi_aligned", 0),
        "rsi_blocks": _state.get("rsi_blocks", {}),
        "config": runtime.get()["tsmom"],
        "scorecard": scorecard(90),
        "selftest": selftest_result(),
        "hunter": hunter.status(),
        "v4": decision_v4.status(),
        "feature_store": feature_store.status(),
    }


def pulse() -> Dict[str, Any]:
    """Hafif durum ozeti. status() agir (90 gunluk karne + oz-test); bu uc
    her sayfa yuklenisinde cagrildigi icin yalnizca gereken alanlari doner.
    """
    from . import hunter
    rcfg = runtime.get()["risk"]
    orow = db.query_one(
        "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    mr = float(rcfg["max_risk_per_trade"])
    return {
        "enabled": enabled(),
        "scanning": bool(_state.get("scanning")),
        "last_scan_at": _state.get("last_scan_at"),
        # GOZCU — "sistem surekli mi ariyor" sorusunun cevabi.
        "scan_interval_seconds": int(runtime.get()["tsmom"]["scan_interval_seconds"]),
        "watch_interval_seconds": int(runtime.get()["tsmom"]["watch_interval_seconds"]),
        "last_watch_at": _state.get("last_watch_at"),
        "last_watch_opened": _state.get("last_watch_opened", 0),
        "last_watch_saved": _state.get("last_watch_saved", 0),
        "last_watch_checked": _state.get("last_watch_checked", 0),
        "watchlist": _state.get("watchlist", []),
        "rsi_leads": _state.get("rsi_leads", []),
        "last_rsi_scan_at": _state.get("last_rsi_scan_at"),
        "api_weight": binance.status().get("used_weight"),
        "open": int(orow["c"]) if orow else 0,
        "max_open": int(runtime.get()["tsmom"]["max_open"]),
        # Motorun KENDI sinirlari — strateji sinirlari, para degil.
        "block_reasons": _state.get("block_reasons", {}),
        "evren_teshis": _state.get("evren_teshis", {}),
        "open_blocks": _state.get("open_blocks", {}),
        "open_detail": _state.get("open_detail", ""),
        "exit_errors": _state.get("exit_errors", []),
        "holds": _state.get("holds", []),
        "scorecard": scorecard(90),
        # PARA katmani ayri bir nesne. Ayni kutuda gostermek 29.08'e kadar
        # "motor calismiyor" yanilgisini uretiyordu: para bitince motor
        # durmus gibi gorunuyordu.
        "copy": copy_trade.status(),
        # Geriye donuk alanlar (eski panel parcalari icin)
        "open_trades": copy_trade.open_count(),
        "open_risk": copy_trade.open_risk(),
        "realized_today": copy_trade.realized_today(),
        "daily_loss_limit": float(rcfg["daily_loss_limit"]),
        "max_risk_per_trade": mr,
        "daily_pnl": copy_trade.daily_pnl(14),
        "hunter": hunter.status(),
        "v4": decision_v4.status(),
        "feature_store": feature_store.status(),
    }


def control_summary() -> Dict[str, Any]:
    """Motor Merkezi için insan-okur karar ve RSI besleme özeti."""
    from . import hunter
    cfg = runtime.get()["tsmom"]
    ccfg = runtime.get()["copy"]
    leads = list(_state.get("rsi_leads") or [])
    if not leads:
        stored = db.get_setting("tsmom_rsi_leads", {}) or {}
        if db.now_ms() - int(stored.get("at") or 0) <= 2 * RSI_TARAMA_ARALIGI_MS:
            leads = list(stored.get("items") or [])
    return {
        "criteria": {
            "universe": int(cfg.get("universe_size", 0)),
            "min_listing_days": int(cfg.get("min_listing_days", 220)),
            "min_quote_volume_m": float(cfg.get("min_quote_volume_m", 4)),
            "min_abs_t": float(cfg.get("min_abs_t", 1.0)),
            "atr_range": [float(cfg.get("min_atr_pct", .8)),
                          float(cfg.get("max_atr_pct", 15))],
            "max_chase_atr": float(cfg.get("max_chase_atr", .75)),
            "max_cost_r": float(cfg.get("max_cost_r", .1)),
            "funding_extreme_bp": float(cfg.get("funding_extreme_bp", 5)),
            "copy_min_t": float(ccfg.get("min_t_to_mirror", 0)),
            "copy_full_risk_min_t": float(ccfg.get("full_risk_min_t", 1.5)),
            "copy_exploration_fraction": float(ccfg.get("exploration_risk_fraction", .35)),
            "copy_exploration_slots": int(ccfg.get("max_exploration_open", 1)),
            "max_open_trades": int(ccfg.get("max_open_trades", 6)),
            "rotation_enabled": bool(cfg.get("rotation_enabled", True)),
            "rotation_review_hours": float(cfg.get("rotation_review_hours", 4)),
            "rotation_min_candidate_t": float(cfg.get("rotation_min_candidate_t", 1.75)),
            "rotation_min_t_improvement": float(cfg.get("rotation_min_t_improvement", .4)),
            "symbol_cooldown_hours": float(cfg.get("symbol_cooldown_hours", 24)),
            "max_hold_hours": float(cfg.get("max_hold_hours", 4)),
            "side_flex_slots": int(cfg.get("side_flex_slots", 1)),
        },
        "rsi": {
            "enabled": bool(cfg.get("rsi_leads_enabled", True)),
            "interval": "1h",
            "universe": int(cfg.get("rsi_leads_universe_size", 80)),
            "limit": int(cfg.get("rsi_leads_limit", 12)),
            "last_scan_at": _state.get("last_rsi_scan_at"),
            "last_checked": int(_state.get("last_rsi_checked", 0)),
            "last_aligned": int(_state.get("last_rsi_aligned", 0)),
            "blocks": _state.get("rsi_blocks", {}),
            "leads": leads,
        },
        "hunter": hunter.status(),
        "v4": decision_v4.status(),
        "feature_store": feature_store.status(),
    }


def recent(limit: int = 20) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM research_trades WHERE source=? ORDER BY created_at DESC LIMIT ?",
        (SOURCE, limit))
    out = []
    for r in rows:
        d = dict(r)
        for key in ("meta", "components"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (TypeError, ValueError):
                    d[key] = {}
        # API tüketicileri için "result_r" artık karar verilen net sonuçtur;
        # geriye dönük brüt alan ayrıca korunur.
        if d.get("status") == "closed" and d.get("result_r") is not None:
            if d.get("gross_result_r") is None:
                d["gross_result_r"] = d["result_r"]
            if d.get("net_result_r") is not None:
                d["result_r"] = d["net_result_r"]
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# Veri
# --------------------------------------------------------------------------- #
async def _daily(symbol: str) -> List[list]:
    now = time.monotonic()
    hit = _daily_cache.get(symbol)
    if hit and now - hit[0] < _DAILY_TTL:
        return hit[1]
    rows = await binance.klines(symbol, "1d", DAILY_BARS)
    # SON BAR HENUZ KAPANMADI. Kapanmamis bari momentum hesabina katmak
    # gun ici oynamayla sinyalin gun boyunca gidip gelmesine yol acar —
    # ve backtest'te asla goremeyecegin bir davranistir.
    rows = rows[:-1] if rows else []
    _daily_cache[symbol] = (now, rows)
    return rows


async def _funding(symbol: str) -> tuple:
    try:
        pi, hist = await asyncio.gather(binance.premium_index(symbol),
                                        binance.funding_history(symbol, 200))
    except Exception:  # noqa: BLE001
        return None, None
    rate = None
    if isinstance(pi, dict):
        try:
            rate = float(pi.get("lastFundingRate"))
        except (TypeError, ValueError):
            rate = None
    return rate, hist


# --------------------------------------------------------------------------- #
# Kayit
# --------------------------------------------------------------------------- #
def _has_open(symbol: str) -> bool:
    row = db.query_one(
        "SELECT 1 FROM research_trades WHERE source=? AND symbol=? AND status='open' LIMIT 1",
        (SOURCE, symbol))
    return row is not None


def _cooldown_remaining_hours(symbol: str, cfg: Dict[str, Any],
                              now: Optional[int] = None) -> float:
    """Kapanan gunluk sinyali ayni gun tekrar tekrar acmayi engelle."""
    hours = float(cfg.get("symbol_cooldown_hours", 24) or 0)
    if hours <= 0:
        return 0.0
    row = db.query_one(
        "SELECT closed_at FROM research_trades WHERE source=? AND symbol=? "
        "AND status='closed' AND closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 1",
        (SOURCE, symbol))
    if not row:
        return 0.0
    remaining = int(row["closed_at"]) + int(hours * 3_600_000) - int(now or db.now_ms())
    return round(max(0, remaining) / 3_600_000, 2)


def _open_strengths(side: Optional[str] = None) -> List[float]:
    sql = "SELECT meta FROM research_trades WHERE source=? AND status='open'"
    params: List[Any] = [SOURCE]
    if side in ("LONG", "SHORT"):
        sql += " AND side=?"
        params.append(side)
    strengths: List[float] = []
    for row in db.query(sql, params):
        try:
            meta = json.loads(row["meta"] or "{}")
            strengths.append(abs(float(meta.get("t_stat") or 0)))
        except (TypeError, ValueError):
            continue
    return strengths


def _side_limit_for(decision: Dict[str, Any], cfg: Dict[str, Any]) -> int:
    """Elit aday icin yalnizca arastirma tarafinda kontrollu yumusak limit."""
    base = int(cfg["max_per_side"])
    flex = int(cfg.get("side_flex_slots", 0) or 0)
    if flex <= 0:
        return base
    candidate_t = abs(float(decision.get("t_stat") or 0))
    strengths = _open_strengths(decision.get("side"))
    weakest = min(strengths) if strengths else 0.0
    if (candidate_t >= float(cfg.get("rotation_min_candidate_t", 1.75))
            and candidate_t >= weakest + float(cfg.get("rotation_min_t_improvement", .4))):
        return min(int(cfg["max_open"]), base + flex)
    return base


def _open_count() -> int:
    row = db.query_one(
        "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    return int(row["c"]) if row else 0


def _open_sides() -> Dict[str, int]:
    rows = db.query(
        "SELECT side, COUNT(*) c FROM research_trades WHERE source=? AND status='open' GROUP BY side",
        (SOURCE,))
    return {r["side"]: int(r["c"]) for r in rows}


def enabled() -> bool:
    """Motor acik mi. Eski motordan AYRI anahtar: ikisi bagimsiz calisiyor.

    Varsayilan ACIK. Eski motorun varsayilani kapaliydi ve kullanici
    sistemin neden hicbir sey yapmadigini anlayamiyordu — bir motorun
    sessizce kapali durmasi, calisip sonuc uretmemesinden daha kotu.
    """
    return (db.get_setting("primary_engine", "smc_ict") == "tsmom"
            and bool(db.get_setting("tsmom_engine_enabled", True)))


def set_enabled(value: bool) -> None:
    db.set_setting("tsmom_engine_enabled", bool(value))


def _day_start_ms() -> int:
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                            0, 0, 0, lt.tm_wday, lt.tm_yday, -1)) * 1000)


# --------------------------------------------------------------------------- #
# PARA KATMANI ARTIK BURADA DEGIL.
# 29.08: hesap buyuklugune bagli her sinir copy_trade.py'ye tasindi. Motor
# kurulum bulur ve arastirma kaydi acar; parayla aynalamak ayri bir karar.
# Asagidakiler yalnizca geriye donuk cagrilar icin ince gecis fonksiyonlari.
# --------------------------------------------------------------------------- #
def _day_start_ms() -> int:
    return copy_trade.day_start_ms()


def _open_paper_trades() -> int:
    return copy_trade.open_count()


def _open_risk() -> float:
    return copy_trade.open_risk()


def _realized_today() -> float:
    return copy_trade.realized_today()


def _risk_used_today() -> float:
    return copy_trade.open_risk()


def daily_pnl(days: int = 14) -> List[Dict[str, Any]]:
    return copy_trade.daily_pnl(days)


async def _open_position(decision: Dict[str, Any], research_id: int,
                         cfg: Dict[str, Any], rcfg: Dict[str, Any]) -> str:
    """Kurulumu PARAYLA aynalamayi dener. Bos string = aynalandi.

    Onemli: burasi motorun basarisi degil. Arastirma kaydi zaten acildi ve
    olcum devam ediyor; bu adim yalnizca "sinyali paranla da takip ettik mi"
    sorusunu cevapliyor. Sonuc ne olursa olsun motor taramaya devam eder.
    """
    res = copy_trade.mirror(decision, research_id)
    if res["ok"]:
        live = await live_trade.open_from_signal(decision, research_id, res)
        if live_trade.enabled() and not live.get("ok") and not live.get("skipped"):
            copy_trade.note_block("CANLI: " + str(live.get("reason") or "emir reddedildi"))
        admin = db.query_one(
            "SELECT * FROM users WHERE role='admin' AND is_active=1 ORDER BY id LIMIT 1")
        if admin:
            await _notify_open(decision, admin, res["margin"], res["risk"], live)
        return ""
    copy_trade.note_block(res["reason"])
    return res["reason"]


async def _notify_open(decision: Dict[str, Any], admin: Dict[str, Any],
                       margin: float, risk: float,
                       live: Optional[Dict[str, Any]] = None) -> None:
    arrow = "🟢 LONG" if decision["side"] == "LONG" else "🔴 SHORT"
    source_label = "TSMOM + RSI" if decision.get("rsi_lead") else "TSMOM"
    try:
        await webpush.send(
            f"{decision['symbol']} · {arrow} · {source_label}",
            f"Giriş {decision['entry']} · SL −%{decision['stop_pct']:.1f}\n"
            f"t={decision['t_stat']:+.2f} · ATR %{decision['atr_pct']:.1f} · risk {risk:.2f}$ · "
            f"{'CANLI + KÂĞIT' if live and live.get('ok') else 'KÂĞIT'}",
            url="/copy-trade", tag=f"tsmom-{decision['symbol']}",
            data={"symbol": decision["symbol"], "side": decision["side"]})
    except Exception:  # noqa: BLE001 — bildirim gonderilemezse islem yine acik
        log.debug("push gonderilemedi", exc_info=True)

    if not runtime.get()["notifications"].get("send_signals", True):
        return
    # Telegram, kullanicinin acip kapattigi bildirim kanalidir. Ileri-test
    # karnesi Motor Merkezi'nde bilgi olarak gorunur; yeni bir kagit sinyalin
    # gruba ulasmasini engellemez. Canli Binance emri ise bundan bagimsizdir
    # ve decision_v4/live_trade katmanindaki kati para kontrollerini korur.
    if not telegram.configured(admin.get("telegram_token", ""), admin.get("telegram_chat_id", "")):
        return
    # YUZDE ZORUNLU. Ciplak fiyat hicbir sey anlatmiyor: "Stop 664.64"
    # bakip stopun genis mi dar mi oldugunu anlamak icin kafadan bolme
    # yapmak gerekiyordu. "%18.9" tek bakista okunuyor.
    fbp = decision["funding"].get("bp")
    rsi_line = None
    if decision.get("rsi_lead"):
        lead = decision["rsi_lead"]
        rsi_line = (f"RSI Radar <b>{lead.get('rsi', '—')}</b> · "
                    f"{telegram.esc(str(lead.get('label') or 'aday'))} · "
                    "günlük motorla yön teyitli")
    text = "\n".join([
        f"<b>VORTEX · {source_label}</b> {arrow} <b>{decision['symbol']}</b>",
        "",
        f"Giriş  <code>{decision['entry']}</code>",
        f"Stop   <code>{decision['stop']}</code>  <b>−%{decision['stop_pct']:.1f}</b>",
        f"Tavan  <code>{decision['target']}</code>  +%{decision['target_pct']:.1f} "
        f"({decision['target_r']:.0f}R)",
        "",
        f"Günlük ATR %{decision['atr_pct']:.1f} → stop = {decision['atr_pct']:.1f} × 2,5",
        f"Momentum t = <b>{decision['t_stat']:+.2f}</b>",
        *([rsi_line] if rsi_line else []),
        f"Fonlama {'—' if fbp is None else format(fbp, '+.2f') + 'bp'}"
        f" · maliyet {decision['total_cost_r']:.4f}R",
        f"Margin {margin:.2f}$ · risk {risk:.2f}$ · {decision['leverage']}x",
        "",
        "<i>Tavan bir tahmin değildir — asıl çıkış momentum dönüşü, rotasyon ya da "
        f"en fazla {float(runtime.get()['tsmom'].get('max_hold_hours', 4)):g} saat. "
        "Kâğıt işlemdir, yatırım tavsiyesi değildir.</i>",
    ])
    try:
        await telegram.send(text, token=admin["telegram_token"],
                            chat_id=admin["telegram_chat_id"])
    except Exception:  # noqa: BLE001
        log.debug("telegram gonderilemedi", exc_info=True)


def _save(decision: Dict[str, Any], cfg: Dict[str, Any]) -> int:
    now = db.now_ms()
    strategy_horizon_ms = int(cfg["horizon_days"]) * MS_DAY
    hard_hold_ms = int(float(cfg.get("max_hold_hours", 4)) * 3_600_000)
    horizon_ms = min(strategy_horizon_ms, hard_hold_ms)
    meta = {
        "t_stat": decision["t_stat"],
        "per_lookback": decision["per_lookback"],
        "lookbacks_agree": decision["lookbacks_agree"],
        "sigma_annual_pct": decision["sigma_annual_pct"],
        "atr_pct": decision["atr_pct"],
        "stop_pct": decision.get("stop_pct"),
        "target_pct": decision.get("target_pct"),
        "leverage": decision.get("leverage"),
        "signal_close": decision.get("signal_close"),
        "drift_pct": decision.get("drift_pct"),
        "live_used": decision.get("live_used"),
        "cost_basis": decision["cost_basis"],
        "fee_cost_r": decision["fee_cost_r"],
        "funding_cost_r": decision["funding_cost_r"],
        "total_cost_r": decision["total_cost_r"],
        "funding_bp": decision["funding"].get("bp"),
        "funding_percentile": decision["funding"].get("percentile"),
        "notes": decision["notes"],
        "max_hold_hours": float(cfg.get("max_hold_hours", 4)),
        "horizon_days": float(decision.get("horizon_days") or cfg.get("horizon_days", 14)),
        "decision_v4": decision.get("v4"),
    }
    # RSI tek başına işlem açmaz. Bu alan yalnızca kurulumun RSI Radar'dan
    # geldiğini ve ana TSMOM kapılarıyla aynı yönde doğrulandığını kanıtlar.
    if decision.get("rsi_lead"):
        lead = decision["rsi_lead"]
        meta["setup_source"] = "rsi_radar+tsmom"
        meta["rsi_lead"] = {
            "side": lead.get("side"), "rsi": lead.get("rsi"),
            "label": lead.get("label"), "score": lead.get("score"),
            "t_stat_1h": lead.get("t_stat"), "zone": lead.get("zone"),
        }
    rid = db.execute(
        "INSERT INTO research_trades(symbol,interval,side,score,entry,stop,target,reasons,"
        "status,created_at,expires_at,source,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision["symbol"], "1d", decision["side"],
         # 'score' kolonu eski motorun kalintisi. Burada |t| x 10 olarak
         # dolduruluyor ki tablo siralanabilsin; skor mantigiyla ilgisi yok.
         int(round(abs(decision["t_stat"]) * 10)),
         decision["entry"], decision["stop"], decision["target"],
         json.dumps(([f"TSMOM t={decision['t_stat']:+.2f}",
                      f"maliyet {decision['total_cost_r']:.4f}R"]
                    + ([f"RSI Radar: {decision['rsi_lead'].get('label', 'aday')}"]
                       if decision.get("rsi_lead") else [])), ensure_ascii=False),
         "open", now, now + horizon_ms, SOURCE, json.dumps(meta, ensure_ascii=False)))
    decision_v4.record(rid, decision)
    return rid


def scorecard(days: int = 90) -> Dict[str, Any]:
    """Motorun karnesi. Tahmin yok, yalnizca KAPANMIS islemlerin ozeti.

    Acik pozisyonlarin kagit uzerindeki kari karneye GIRMEZ: henuz
    gerceklesmemis bir kar, karne degil temennidir. Ayri gosteriliyor.
    """
    since = db.now_ms() - days * MS_DAY
    rows = [dict(r) for r in db.query(
        "SELECT * FROM research_trades WHERE source=? AND status='closed' "
        "AND result_r IS NOT NULL AND closed_at>=? "
        "AND COALESCE(exit_reason,'') <> 'elle' ORDER BY closed_at", (SOURCE, since))]
    manual_row = db.query_one(
        "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='closed' "
        "AND result_r IS NOT NULL AND closed_at>=? AND exit_reason='elle'", (SOURCE, since))
    out: Dict[str, Any] = {"days": days, "closed": len(rows), "open": 0,
                           "manual_closed": int(manual_row["c"]) if manual_row else 0}

    orow = db.query_one(
        "SELECT COUNT(*) c FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    out["open"] = int(orow["c"]) if orow else 0

    if not rows:
        out["note"] = "Henüz kapanmış işlem yok — karne için sonuç bekleniyor."
        return out

    import math as _m
    rs = research_metrics.values(rows)
    gross_values = [float(r.get("gross_result_r") if r.get("gross_result_r") is not None
                          else r["result_r"]) for r in rows]
    n = len(rs)
    mean = sum(rs) / n
    sd = (sum((x - mean) ** 2 for x in rs) / (n - 1)) ** .5 if n > 1 else 0.0
    se = sd / _m.sqrt(n) if n else 0.0
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]

    # Sermaye egrisi ve en derin dusus — R cinsinden
    eq = peak = mdd = 0.0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)

    # Maliyet: her islemin kendi cost_basis'i meta'da
    costs = [float(research_metrics.breakdown(r)["cost_r"]) for r in rows]
    avg_cost = sum(costs) / len(costs) if costs else None

    out.update({
        "entry_days": len({int(r["created_at"]) // MS_DAY for r in rows}),
        "total_r": round(sum(rs), 2),
        "avg_r": round(mean, 4),
        "ci95": [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)],
        # Anlamlilik: guven araligi sifiri icermiyorsa sonuc sifirdan
        # ayirt edilebilir. Icerıyorsa "kazaniyor" da "kaybediyor" da
        # denemez — en sik yapilan hata bu.
        # Anlamlilik icin en az 5 gozlem VE sifir olmayan dagilim sart.
        # Hepsi ayni degerde kapanmis bir seri (ornegin 9 islemin 9'u da tam
        # stopta) sd=0 uretir; guven araligi tek noktaya coker ve kod bunu
        # "kanitlandi" sanirdi. Kanit degil, tekrar.
        "significant": bool(n >= 5 and se > 0
                            and ((mean - 1.96 * se) > 0 or (mean + 1.96 * se) < 0)),
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_win": round(sum(wins) / len(wins), 3) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 3) if losses else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else None,
        "max_drawdown_r": round(mdd, 2),
        "avg_cost_r": round(avg_cost, 4) if avg_cost is not None else None,
        "gross_r": round(sum(gross_values) / len(gross_values), 4) if gross_values else None,
        "net_accounting": True,
        "exits": dict(Counter((r["exit_reason"] or "?") for r in rows)),
        "by_side": {},
        "equity": [round(x, 3) for x in _cum(rs)],
        # Bu buyuklukteki bir farki kanitlamak icin gereken islem sayisi.
        # "az veri" demek yerine KAC gerektigini soylemek daha durust.
        "n_needed": int((2 * sd / abs(mean)) ** 2) if mean and sd else None,
    })
    for side in ("LONG", "SHORT"):
        sub = [float(r["net_result_r"] if r["net_result_r"] is not None
                     else research_metrics.breakdown(r)["net_r"])
               for r in rows if r["side"] == side]
        if sub:
            m2 = sum(sub) / len(sub)
            out["by_side"][side] = {"n": len(sub), "avg_r": round(m2, 4),
                                    "total_r": round(sum(sub), 2)}
    return out


def _cum(values: List[float]) -> List[float]:
    total = 0.0
    out = []
    for v in values:
        total += v
        out.append(total)
    return out


# --------------------------------------------------------------------------- #
# Cikislar
# --------------------------------------------------------------------------- #
async def evaluate_open() -> Dict[str, int]:
    """Acik TSMOM kayitlarini kapatir: stop / hedef / momentum donusu / sure.

    Stop ve hedef GUNLUK barla degil, 1 saatlik barla taranir: gunluk barin
    icinde stop yenmis olabilir ve gunluk kapanisa bakmak bunu gormezden
    gelir — sonucu sistematik olarak iyimser gosterirdi.
    """
    rows = db.query("SELECT * FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    counts = {"stop": 0, "target": 0, "flip": 0, "fade": 0, "sure": 0, "durgun": 0, "kaldi": 0}
    holds: List[Dict[str, Any]] = []
    if not rows:
        _state["holds"] = []
        return counts
    if binance.status().get("mode") != "live":
        _state.update(data_paused=True, data_pause_reason="Canlı Binance verisi bekleniyor")
        counts["veri_bekle"] = len(rows)
        return counts
    now = db.now_ms()

    # CANLI FIYAT YEDEGI — 27.08 hata duzeltmesi.
    # Onceki surum cikis fiyatini yalnizca "son kontrolden bu yana gelen"
    # 1h barlardan aliyordu. Tarama araligi da 1 saat oldugu icin o liste
    # cogu zaman BOS geliyordu; last_price None kaliyor, hem momentum
    # cikisi hem 14 gunluk sure stopu sessizce calismiyordu. Sonuc:
    # pozisyonlar yalnizca stop/hedefe carparsa kapaniyor, aksi halde
    # sonsuza kadar acik kaliyor ve yeni kurulumlara yer kalmiyordu.
    try:
        raw_tickers = await binance.ticker_24h()
        if binance.status().get("mode") != "live":
            _state.update(data_paused=True, data_pause_reason="Ticker çağrısı canlı veriden düştü")
            counts["veri_bekle"] = len(rows)
            return counts
        tick = {t["symbol"]: float(t.get("lastPrice", 0) or 0) for t in raw_tickers}
    except Exception:  # noqa: BLE001
        tick = {}

    async def one(row: Dict[str, Any]) -> None:
        symbol = row["symbol"]
        direction = 1 if row["side"] == "LONG" else -1
        risk = abs(float(row["entry"]) - float(row["stop"]))
        if risk <= 0:
            db.execute("UPDATE research_trades SET status='closed',closed_at=?,exit_reason=? WHERE id=?",
                       (now, "gecersiz_risk", row["id"]))
            return
        tcfg = dict(runtime.get()["tsmom"])
        effective_expiry = min(
            int(row["expires_at"]),
            int(row["created_at"]) + int(float(tcfg.get("max_hold_hours", 4)) * 3_600_000),
        )
        timed_out = now >= effective_expiry

        # Pencere en az 2 saat geriye baksin: tam tarama araliginda bir
        # bar bile gelmeyebiliyordu. Ustuste sayim zararsiz — stop/hedef
        # kontrolu idempotent.
        since = min(int(row.get("last_checked_at") or row["created_at"]),
                    now - 2 * 3_600_000)
        try:
            bars = await binance.klines(symbol, "1h", 500)
            if binance.status().get("mode") != "live":
                return
        except Exception:  # noqa: BLE001
            # SERT TIMEOUT tarihsel bar servisine bagli olamaz. Klines
            # gecici olarak patlasa bile canli ticker ile pozisyonu kapat.
            # Timeout dolmadiysa stop/hedef siralamasini kaybetmemek icin
            # eskisi gibi bu turu atla.
            if not timed_out:
                return
            bars = []
        bars = [b for b in bars if int(b[0]) >= since]

        hit = exit_price = exit_at = None
        mfe = float(row.get("mfe_r") or 0.0)
        mae = float(row.get("mae_r") or 0.0)
        for b in bars:
            high, low = float(b[2]), float(b[3])
            best = (high - row["entry"]) * direction / risk if direction > 0 else (row["entry"] - low) / risk
            worst = (low - row["entry"]) * direction / risk if direction > 0 else (row["entry"] - high) / risk
            mfe, mae = max(mfe, best), min(mae, worst)
            stop_touch = low <= row["stop"] if direction > 0 else high >= row["stop"]
            target_touch = high >= row["target"] if direction > 0 else low <= row["target"]
            if stop_touch:                       # ayni barda ikisi de -> STOP
                hit, exit_price, exit_at = "stop", row["stop"], int(b[6])
                break
            if target_touch:
                hit, exit_price, exit_at = "target", row["target"], int(b[6])
                break

        # Bar listesi bos olsa bile canli fiyat var: cikis artik hicbir
        # kosulda "fiyat yok" diye engellenmiyor.
        last_price = float(bars[-1][4]) if bars else (tick.get(symbol) or None)
        if timed_out and not last_price:
            # Tum ticker listesi alinamadiysa sembol bazinda son bir canli
            # fiyat denemesi yap. Fiyatsiz piyasa emri/defter kapanisi yok.
            try:
                latest = await binance.ticker_24h(symbol)
                if binance.status().get("mode") == "live":
                    last_price = float(latest.get("lastPrice", 0) or 0) or None
            except Exception:  # noqa: BLE001
                last_price = None
        if hit is None:
            # Sert sure tavani günlük momentum verisinden bagimsizdir.
            # Stop/hedef bar kontrolunden sonra, günlük veri isteginden once
            # uygulanir; veri servisi aksasa da dört saati asamaz.
            if timed_out and last_price:
                hit, exit_price, exit_at = "sure", last_price, now
            else:
                # TSMOM'un ASIL cikisi: pozisyonu tutma sebebi hala duruyor mu?
                daily = await _daily(symbol)
                if binance.status().get("mode") != "live":
                    return
                sig = strat.exit_signal(row["side"], daily, tcfg)
                if sig.get("flip") and last_price:
                    hit, exit_price, exit_at = "flip", last_price, now
                elif sig.get("exit") and last_price:
                    # Trend soldu: yon donmedi ama girmemizi saglayan momentum
                    # neredeyse kalmadi. Bunu beklemek pozisyonu 14 gun bos yere
                    # tutmak demek.
                    hit, exit_price, exit_at = "fade", last_price, now
                elif (tcfg.get("stale_exit", True) and last_price
                      and (now - row["created_at"]) >= int(tcfg["stale_days"]) * MS_DAY
                      and mfe < float(tcfg["stale_max_mfe_r"])):
                    # DURGUN. Ufkunun yarisini harcadi, momentum hala yonde diye
                    # aciktu ama fiyat lehte kayda deger hicbir yere gitmedi.
                    hit, exit_price, exit_at = "durgun", last_price, now

            if hit is None:
                db.execute("UPDATE research_trades SET last_checked_at=?,mfe_r=?,mae_r=? WHERE id=?",
                           (now, round(mfe, 3), round(mae, 3), row["id"]))
                counts["kaldi"] += 1
                # NEDEN HALA ACIK. Bu bilgi zaten burada hesaplaniyordu ama
                # hicbir yere yazilmiyordu; kullanici "motor islemi neden
                # kapatmiyor" sorusunun cevabini goremiyordu. Ek ag istegi
                # yok — sig ve barlar elimizde.
                floor = float(tcfg["exit_t_floor"])
                t_dir = float(sig.get("t_dir") or 0)
                d2 = 1 if row["side"] == "LONG" else -1
                holds.append({
                    "id": row["id"], "symbol": symbol, "side": row["side"],
                    "entry": float(row["entry"]), "stop": float(row["stop"]),
                    "target": float(row["target"]), "price": last_price,
                    "t_dir": round(t_dir, 3), "exit_floor": floor,
                    "r_now": (round((last_price - float(row["entry"])) * d2 / risk, 3)
                              if last_price else None),
                    "stop_pct": (round(abs(last_price - float(row["stop"])) / last_price * 100, 2)
                                 if last_price else None),
                    "target_pct": (round(abs(float(row["target"]) - last_price) / last_price * 100, 2)
                                   if last_price else None),
                    "hours_left": round((effective_expiry - now) / 3_600_000, 1),
                    "days_left": round((effective_expiry - now) / MS_DAY, 1),
                    "age_days": round((now - row["created_at"]) / MS_DAY, 1),
                    "stale_in": (round(int(tcfg["stale_days"])
                                       - (now - row["created_at"]) / MS_DAY, 1)
                                 if tcfg.get("stale_exit", True) else None),
                    "mfe_r": round(mfe, 3), "mae_r": round(mae, 3),
                    "reason": (f"momentum hâlâ yönde (|t|={t_dir:.2f} ≥ çıkış eşiği {floor:.2f})"
                               if t_dir >= floor else "çıkış koşulu oluşmadı"),
                })
                return

        result_r = round((exit_price - row["entry"]) * direction / risk, 3)
        closed_at = exit_at or now
        net = research_metrics.close_values(row, result_r, closed_at)
        # Para katmani once kapanir. Binance kapatma basarisizsa arastirma
        # kaydini acik birak ki hizli gozcu 30 saniye sonra tekrar denesin.
        # Tersi siralama "panelde kapali, borsada acik" hayalet riski yaratiyordu.
        live_result = await live_trade.close_for_research(
            int(row["id"]), float(exit_price), float(result_r), str(hit))
        if not live_result.get("ok"):
            db.execute("UPDATE research_trades SET last_checked_at=?,mfe_r=?,mae_r=? WHERE id=?",
                       (now, round(mfe, 3), round(mae, 3), row["id"]))
            counts["kapatma_hatasi"] = counts.get("kapatma_hatasi", 0) + 1
            log.error("%s canli cikis ertelendi: %s", row["symbol"], live_result.get("reason"))
            return

        db.execute(
            "UPDATE research_trades SET status='closed',closed_at=?,exit_price=?,result_r=?,"
            "gross_result_r=?,cost_r=?,net_result_r=?,outcome=?,exit_reason=?,"
            "last_checked_at=?,mfe_r=?,mae_r=? WHERE id=?",
            (closed_at, exit_price, result_r, net["gross_r"], net["cost_r"], net["net_r"],
             "win" if net["net_r"] > 0 else "loss" if net["net_r"] < 0 else "flat",
             hit, now, round(mfe, 3), round(mae, 3), row["id"]))
        _close_paper(row["id"], exit_price, float(net["net_r"]), closed_at, hit)
        counts[hit] = counts.get(hit, 0) + 1

    # Onceden hatalar sessizce yutuluyordu: bir sembolde surekli hata alsak
    # bile panelde "kaldi" gorunuyordu ve sebep hicbir yere yazilmiyordu.
    results = await asyncio.gather(*(one(dict(r)) for r in rows), return_exceptions=True)
    hatalar = [f"{rows[i]['symbol']}: {type(x).__name__}: {x}"
               for i, x in enumerate(results) if isinstance(x, BaseException)]
    if hatalar:
        counts["hata"] = len(hatalar)
        _state["exit_errors"] = hatalar[:5]
        log.error("TSMOM cikis degerlendirmesi %d sembolde hata verdi: %s",
                  len(hatalar), "; ".join(hatalar[:3]))
    else:
        _state["exit_errors"] = []
    _state["holds"] = sorted(holds, key=lambda h: -(h.get("t_dir") or 0))
    _state["holds_at"] = db.now_ms()
    if binance.status().get("mode") == "live":
        _state.update(data_paused=False, data_pause_reason="")
    return counts


async def close_manual(research_id: int, reason: str = "elle") -> Dict[str, Any]:
    """Acik bir arastirma kaydini ANLIK fiyattan kapatir ve bagli kagit
    pozisyonu da kapatir.

    Neden var: motor bir pozisyonu kurali geregi haftalarca tutabiliyor ve
    kullanicinin "ben bunu istemiyorum" diyebilecegi hicbir yer yoktu.
    Otomatik cikislari degistirmiyor; yalnizca elle mudahale kapisi aciyor.
    """
    row = db.query_one(
        "SELECT * FROM research_trades WHERE id=? AND source=? AND status='open'",
        (research_id, SOURCE))
    if not row:
        return {"ok": False, "error": "Açık kayıt bulunamadı"}
    row = dict(row)
    if binance.status().get("mode") != "live":
        return {"ok": False, "error": "Canlı Binance verisi yok; güvenli kapanış için bekleyin"}
    try:
        tick = await binance.ticker_24h(row["symbol"])
        if binance.status().get("mode") != "live":
            return {"ok": False, "error": "Fiyat kaynağı canlı moddan düştü; kayıt kapatılmadı"}
        price = float(tick["lastPrice"])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Anlık fiyat alınamadı: {exc}"}

    direction = 1 if row["side"] == "LONG" else -1
    risk = abs(float(row["entry"]) - float(row["stop"]))
    if risk <= 0:
        return {"ok": False, "error": "Geçersiz stop mesafesi"}
    result_r = round((price - float(row["entry"])) * direction / risk, 3)
    now = db.now_ms()
    net = research_metrics.close_values(row, result_r, now)
    live_result = await live_trade.close_for_research(
        research_id, price, result_r, reason)
    if not live_result.get("ok"):
        return {"ok": False, "error":
                "Canlı Binance pozisyonu kapatılamadı; araştırma kaydı açık bırakıldı: "
                + str(live_result.get("reason") or "bilinmeyen hata")}
    db.execute(
        "UPDATE research_trades SET status='closed',closed_at=?,exit_price=?,result_r=?,"
        "gross_result_r=?,cost_r=?,net_result_r=?,outcome=?,exit_reason=?,last_checked_at=? WHERE id=?",
        (now, price, result_r, net["gross_r"], net["cost_r"], net["net_r"],
         "win" if net["net_r"] > 0 else "loss" if net["net_r"] < 0 else "flat",
         reason, now, research_id))
    aynalandi = copy_trade.close_for_research(
        research_id, price, float(net["net_r"]), now, reason)
    # Kapanan kayit "hala acik" listesinden de dussun, yoksa panel bir
    # sonraki taramaya kadar eski bilgiyi gosterir.
    _state["holds"] = [h for h in _state.get("holds", []) if h.get("id") != research_id]
    log.info("%s kapatildi reason=%s (brut %.3fR, net %.3fR, fiyat %.8f)",
             row["symbol"], reason, result_r, net["net_r"], price)
    return {"ok": True, "symbol": row["symbol"], "side": row["side"],
            "result_r": net["net_r"], "gross_result_r": result_r,
            "cost_r": net["cost_r"], "exit_price": price, "mirrored_closed": aynalandi}


async def close_all_manual(reason: str = "elle") -> Dict[str, Any]:
    """Tum acik TSMOM kayitlarini kapatir."""
    rows = db.query(
        "SELECT id FROM research_trades WHERE source=? AND status='open'", (SOURCE,))
    sonuc = []
    for r in rows:
        sonuc.append(await close_manual(int(r["id"]), reason))
    return {"ok": True, "closed": sum(1 for x in sonuc if x.get("ok")),
            "results": sonuc}


async def _rotate_for_better(passed: List[Dict[str, Any]],
                             live_prices: Dict[str, float],
                             cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Dort saatlik portfoy incelemesi.

    Risk tavanini buyutmek yerine, kapasite doluysa mevcut en zayif kaydi
    belirgin guclu yeni adayla degistirir. Buyuk kazanca gecmis pozisyonlar
    sirf yeni bir sinyal cikti diye kesilmez.
    """
    if not cfg.get("rotation_enabled", True):
        return {"action": "disabled"}
    now = db.now_ms()
    previous = db.get_setting("tsmom_rotation_last", {}) or {}
    interval_ms = int(float(cfg.get("rotation_review_hours", 4)) * 3_600_000)
    if now - int(previous.get("at") or 0) < interval_ms:
        return previous

    open_rows = [dict(r) for r in db.query(
        "SELECT * FROM research_trades WHERE source=? AND status='open'", (SOURCE,))]
    side_counts = Counter(r["side"] for r in open_rows)
    copy_full = copy_trade.open_count() >= int(runtime.get()["copy"].get("max_open_trades", 6))
    max_rotate = int(cfg.get("rotation_max_per_review", 1) or 0)
    min_age_ms = int(float(cfg.get("rotation_min_age_hours", 4)) * 3_600_000)
    min_candidate = float(cfg.get("rotation_min_candidate_t", 1.75))
    min_improvement = float(cfg.get("rotation_min_t_improvement", .4))
    protect_r = float(cfg.get("rotation_protect_winner_r", .5))

    candidates = [d for d in passed
                  if not _has_open(d["symbol"])
                  and _cooldown_remaining_hours(d["symbol"], cfg, now) <= 0
                  and abs(float(d.get("t_stat") or 0)) >= min_candidate]
    candidates.sort(key=lambda d: -abs(float(d.get("t_stat") or 0)))
    passed_by = {d["symbol"]: d for d in passed}

    mirrored_ids = set()
    for trade in db.query("SELECT meta FROM trades WHERE status='open' AND mode IN ('paper','live')"):
        try:
            rid = int(json.loads(trade["meta"] or "{}").get("research_id") or 0)
            if rid:
                mirrored_ids.add(rid)
        except (TypeError, ValueError):
            continue

    replacements = []
    for candidate in candidates:
        if len(replacements) >= max_rotate:
            break
        side = candidate["side"]
        pressure = (len(open_rows) >= int(cfg["max_open"])
                    or side_counts.get(side, 0) >= int(cfg["max_per_side"])
                    or copy_full)
        if not pressure:
            continue
        eligible = []
        for row in open_rows:
            if now - int(row["created_at"]) < min_age_ms:
                continue
            # Ayni yon tavani doluysa diger yonu kesmek bu sorunu cozmez.
            if side_counts.get(side, 0) >= int(cfg["max_per_side"]) and row["side"] != side:
                continue
            if copy_full and int(row["id"]) not in mirrored_ids:
                continue
            try:
                meta = json.loads(row.get("meta") or "{}")
            except (TypeError, ValueError):
                meta = {}
            current = passed_by.get(row["symbol"])
            strength = abs(float((current or {}).get("t_stat") or meta.get("t_stat") or 0))
            price = float(live_prices.get(row["symbol"]) or 0)
            risk = abs(float(row["entry"]) - float(row["stop"]))
            direction = 1 if row["side"] == "LONG" else -1
            r_now = ((price - float(row["entry"])) * direction / risk
                     if price > 0 and risk > 0 else 0.0)
            if r_now > protect_r:
                continue
            eligible.append((strength, r_now, row))
        if not eligible:
            continue
        eligible.sort(key=lambda x: (x[0], x[1]))
        weak_t, weak_r, weak = eligible[0]
        candidate_t = abs(float(candidate["t_stat"]))
        if candidate_t < weak_t + min_improvement:
            continue
        result = await close_manual(int(weak["id"]), "rotasyon")
        if not result.get("ok"):
            continue
        replacements.append({
            "closed_id": int(weak["id"]), "closed_symbol": weak["symbol"],
            "closed_t": round(weak_t, 3), "closed_r": round(weak_r, 3),
            "candidate_symbol": candidate["symbol"],
            "candidate_t": round(candidate_t, 3),
        })
        open_rows = [r for r in open_rows if int(r["id"]) != int(weak["id"])]
        side_counts[weak["side"]] -= 1

    report = {
        "at": now,
        "action": "rotated" if replacements else "no_change",
        "review_hours": float(cfg.get("rotation_review_hours", 4)),
        "replacements": replacements,
        "candidate_count": len(candidates),
        "reason": ("daha güçlü aday için zayıf slot boşaltıldı" if replacements
                   else "kapasite baskısı veya yeterli güç farkı yok"),
    }
    db.set_setting("tsmom_rotation_last", report)
    _state["last_rotation"] = report
    if replacements:
        log.warning("TSMOM ROTASYON: %s", replacements)
    return report


def _close_paper(research_id: int, exit_price: float, result_r: float,
                 closed_at: int, reason: str) -> None:
    """Arastirma kaydi kapaninca ona bagli kagit pozisyonu da kapatir."""
    copy_trade.close_for_research(research_id, exit_price, result_r, closed_at, reason)


def _write_candidates(passed: List[Dict[str, Any]], reasons_by_symbol: Dict[str, str],
                      cfg: Dict[str, Any], rcfg: Dict[str, Any]) -> int:
    """Kapilari GECEN ama acilamayan kurulumlari aday olarak yazar.

    NEDEN: motor bir kurulumu begenip kapasite yuzunden acamadiginda o bilgi
    su ana kadar yalnizca bir sayacta kaliyordu. Kullanici panelde bos bir
    "Aday Islemler" kutusu goruyordu — oysa motor birkac saniye once o
    kurulumlari bulmustu. Aday listesi bir IZLEME listesidir: motor acmadi,
    ama bakip elle acabilirsin ("Aç" dugmesi anlik fiyattan acar).

    ESKILER SILINIYOR: aday satirlari FIYAT tasiyor. Onceki taramadan kalan
    bir aday, saatler once gecerli olan bir giris fiyati gosterir — daha once
    duzelttigimiz bayat fiyat hatasinin baska bir kilikta hali. Bu yuzden
    liste her taramada sifirdan yaziliyor.

    LISTE ARASTIRMA KAYDINA BAGLI DEGIL. Ilk surumde bir sembol bir kez
    arastirmaya kaydedilince bir daha aday olarak gorunmuyordu — oysa motor
    onu hala begeniyordu, sadece muhasebe yuzunden atliyordu. Aday listesi
    "motorun SU AN begendigi ve elde TUTMADIGI" kurulumlardir; arastirma
    defterinin ne dedigiyle ilgisi yok.
    """
    admin = db.query_one("SELECT * FROM users WHERE role='admin' AND is_active=1 ORDER BY id LIMIT 1")
    if not admin:
        return 0
    db.execute("DELETE FROM trades WHERE status='candidate' AND mode='paper' "
               "AND note LIKE 'TSMOM%'")
    if not bool(cfg.get("show_candidates", True)):
        return 0

    # Elde tutulan / arastirmada zaten acik sembol aday olamaz. Eski kod
    # yalnizca acik PAPER satirina bakiyordu; kullanici paper satirini
    # kapatinca arastirma acik kalmis olsa bile ayni sembol tekrar "aday"
    # diye yaziliyordu. Bu hem kullaniciyi yaniltiyor hem de gercekte yeni
    # firsat olmayan satirlari listeyi dolduruyordu.
    held = {r["symbol"] for r in db.query(
        "SELECT DISTINCT symbol FROM trades WHERE status='open' AND mode='paper'")}
    held.update(r["symbol"] for r in db.query(
        "SELECT DISTINCT symbol FROM research_trades WHERE source=? AND status='open'",
        (SOURCE,)))
    items = [d for d in passed if d["symbol"] not in held]

    leverage = int(rcfg["default_leverage"])
    limit = int(cfg.get("max_candidates", 8))
    now = db.now_ms()
    written = 0
    for d in items[:limit]:
        why = reasons_by_symbol.get(d["symbol"], "kapasite sınırı — izlemede")
        stop_distance = abs(float(d["entry"]) - float(d["stop"]))
        if stop_distance <= 0 or leverage <= 0:
            continue
        margin = round(min(rcfg["max_position_margin"],
                           rcfg["max_risk_per_trade"] * d["entry"] / (stop_distance * leverage)), 2)
        qty = margin * leverage / d["entry"] if d["entry"] else 0
        db.execute(
            "INSERT INTO trades(user_id,symbol,side,status,mode,entry,stop,take_profit,margin_usdt,"
            "leverage,margin_type,qty,risk_usdt,opened_at,interval,note,meta) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (admin["id"], d["symbol"], d["side"], "candidate", "paper", d["entry"], d["stop"],
             d["target"], margin, leverage, "ISOLATED", qty, stop_distance * qty, now, "1d",
             f"TSMOM adayı · {why}" if why else "TSMOM adayı",
             json.dumps({"engine": SOURCE, "t_stat": d["t_stat"],
                         "stop_pct": d.get("stop_pct"), "atr_pct": d.get("atr_pct"),
                         "total_cost_r": d["total_cost_r"],
                         "funding_bp": d["funding"].get("bp"),
                         "blocked_by": why or "tarama başına yeni işlem sınırı"},
                        ensure_ascii=False)))
        written += 1
    return written


# --------------------------------------------------------------------------- #
# Kendi kendini backtest
# --------------------------------------------------------------------------- #
SELFTEST_KEY = "tsmom_selftest"


def selftest_result() -> Dict[str, Any]:
    """En son otomatik backtest sonucu (varsa)."""
    return db.get_setting(SELFTEST_KEY, {}) or {}


async def run_selftest(days: int = 120, symbols: int = 12) -> Dict[str, Any]:
    """Motoru GECMIS veriyle test eder ve sonucu saklar.

    NEDEN GEREKLI: ileri test dogru olcum ama yavas — 14 gunluk ufukla ilk
    kapanislar 2. haftadan once gelmiyor. Backtest daha zayif bir kanittir
    (parametreler bu donemden tamamen bagimsiz degil) ama BUGUN elde var.
    Ikisi birlikte bakiliyor: backtest bir on gosterge, ileri test hakem.

    Ayni karar kodunu kullanir (strat.evaluate); strateji burada yeniden
    yazilmaz — yazilsaydi olculen sey canli motor olmazdi.
    """
    from ..strategies import carry_tsmom as st_mod
    # DEMO VERIDE CALISTIRMA. Sentetik 1d ve 4h serileri birbirinden
    # bagimsiz uretiliyor; fiyatlar tutmadigi icin her islem stop yiyor ve
    # sonuc "-1.00R, %0 isabet" cikiyor. Bu sayiyi panele yazmak, olcum
    # yapmadigin bir seyi olculmus gibi gostermek olur — yoklugundan kotu.
    if binance.is_demo():
        out = {"ok": False, "at": db.now_ms(),
               "error": "Binance demo modda — geçmiş testi anlamsız sonuç üretir, çalıştırılmadı"}
        db.set_setting(SELFTEST_KEY, out)
        return out
    cfg_all = runtime.get()
    cfg, rcfg = cfg_all["tsmom"], cfg_all["risk"]
    scfg = dict(cfg)
    scfg["leverage"] = rcfg["default_leverage"]

    end = db.now_ms() - MS_DAY
    start = end - days * MS_DAY
    warm = (st_mod.MIN_BARS + 10) * MS_DAY

    try:
        tickers, contracts = await asyncio.gather(
            binance.ticker_24h(), binance.perpetual_symbols())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"veri alinamadi: {exc}"}
    now_ms = db.now_ms()
    min_age = int(cfg["min_listing_days"]) * MS_DAY
    ok_syms = {c["symbol"] for c in contracts
               if not (c.get("onboardDate") and now_ms - int(c["onboardDate"]) < min_age)}
    universe = [t["symbol"] for t in sorted(
        [t for t in tickers if t["symbol"] in ok_syms
         and float(t.get("quoteVolume", 0) or 0) >= cfg["min_quote_volume_m"] * 1e6],
        key=lambda t: -float(t.get("quoteVolume", 0) or 0))][:symbols]

    results: List[float] = []
    exits: Dict[str, int] = {}
    horizon_ms = int(cfg["horizon_days"]) * MS_DAY

    for symbol in universe:
        try:
            daily = await binance.klines_range(symbol, "1d", start - warm, end, 1500)
            b4 = await binance.klines_range(symbol, "4h", start, end + 20 * MS_DAY, 1500)
        except Exception:  # noqa: BLE001
            continue
        if len(daily) < st_mod.MIN_BARS + 20 or not b4:
            continue
        busy_until = 0
        for i in range(st_mod.MIN_BARS, len(daily) - 1):
            close_time = int(daily[i][6])
            if close_time < start or close_time <= busy_until:
                continue
            # Giris: SONRAKI gunun acilisi. Gordugun kapanistan islem
            # yapamazsin — bu satir olmadan sonuc iyimser cikar.
            entry = float(daily[i + 1][1])
            entry_ts = int(daily[i + 1][0])
            if entry <= 0:
                continue
            try:
                d = st_mod.evaluate(symbol, daily[:i + 1], None, None, scfg, entry)
            except Exception:  # noqa: BLE001
                continue
            if not d.get("ok"):
                continue
            direction = 1 if d["side"] == "LONG" else -1
            risk = abs(d["entry"] - d["stop"])
            if risk <= 0:
                continue
            hit, exit_px, exit_at = "sure", d["entry"], entry_ts + horizon_ms
            for b in b4:
                bt = int(b[0])
                if bt < entry_ts:
                    continue
                if bt > entry_ts + horizon_ms:
                    break
                high, low, close = float(b[2]), float(b[3]), float(b[4])
                stop_touch = low <= d["stop"] if direction > 0 else high >= d["stop"]
                tgt_touch = high >= d["target"] if direction > 0 else low <= d["target"]
                exit_px, exit_at = close, int(b[6])
                if stop_touch:          # ayni barda ikisi de -> STOP (kotumser)
                    hit, exit_px = "stop", d["stop"]
                    break
                if tgt_touch:
                    hit, exit_px = "target", d["target"]
                    break
            gross = (exit_px - d["entry"]) * direction / risk
            results.append(round(gross - float(d["total_cost_r"]), 4))
            exits[hit] = exits.get(hit, 0) + 1
            busy_until = exit_at
        await asyncio.sleep(0)

    if not results:
        out = {"ok": False, "error": "hic islem uretilmedi", "at": db.now_ms(),
               "days": days, "symbols": len(universe)}
        db.set_setting(SELFTEST_KEY, out)
        return out

    import math as _m
    n = len(results)
    mean = sum(results) / n
    sd = (sum((x - mean) ** 2 for x in results) / (n - 1)) ** .5 if n > 1 else 0.0
    se = sd / _m.sqrt(n) if n else 0.0
    wins = [x for x in results if x > 0]
    losses = [x for x in results if x < 0]
    eq = peak = mdd = 0.0
    for x in results:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    out = {
        "ok": True, "at": db.now_ms(), "days": days, "symbols": len(universe),
        "n": n, "total_r": round(sum(results), 2), "avg_r": round(mean, 4),
        "ci95": [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)],
        # Anlamlilik icin en az 5 gozlem VE sifir olmayan dagilim sart.
        # Hepsi ayni degerde kapanmis bir seri (ornegin 9 islemin 9'u da tam
        # stopta) sd=0 uretir; guven araligi tek noktaya coker ve kod bunu
        # "kanitlandi" sanirdi. Kanit degil, tekrar.
        "significant": bool(n >= 5 and se > 0
                            and ((mean - 1.96 * se) > 0 or (mean + 1.96 * se) < 0)),
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else None,
        "max_drawdown_r": round(mdd, 2), "exits": exits,
        "note": "Gecmis veri. Ileri testten ZAYIF kanit; parametreler bu donemden bagimsiz degil.",
    }
    db.set_setting(SELFTEST_KEY, out)
    log.info("TSMOM oz-backtest: n=%d ort=%+.4fR GA[%+.3f,%+.3f]",
             n, mean, out["ci95"][0], out["ci95"][1])
    return out


# --------------------------------------------------------------------------- #
# Tarama
# --------------------------------------------------------------------------- #
async def scan_once() -> Dict[str, Any]:
    if _scan_lock.locked():
        return {"ok": False, "error": "TSMOM taramasi zaten calisiyor"}
    async with _scan_lock:
        started = time.monotonic()
        _state.update(scanning=True, last_error=None)
        try:
            cfg = runtime.get()["tsmom"]
            if binance.status().get("mode") != "live":
                _state.update(data_paused=True, data_pause_reason="Tam tarama canlı veri bekliyor")
                return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": {}}
            # CIKISLAR ONCE ve KOSULSUZ. Pozisyon kapatmak sinyal uretmek
            # degil risk yonetimidir; motor anahtarina baglanmasi hataydi.
            # Motor kapatildiginda acik pozisyonlar sonsuza kadar donuyordu.
            exits = await evaluate_open()
            # Stop/hedef Binance tarafinda tetiklenmis olabilir veya onceki
            # cikis denemesi gecici ag hatasi almis olabilir. Her gozcu turu
            # borsa ile yerel defteri yeniden uzlastirir.
            await live_trade.reconcile()
            if binance.status().get("mode") != "live":
                return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": exits}
            if not cfg.get("enabled", True) or not enabled():
                return {"ok": True, "skipped": "motor kapali", "exits": exits}

            tickers, contracts = await asyncio.gather(
                binance.ticker_24h(), binance.perpetual_symbols())
            if binance.status().get("mode") != "live":
                _state.update(data_paused=True, data_pause_reason="Evren taraması canlı veriden düştü")
                return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": exits}
            now_ms = db.now_ms()
            # Yas filtresi burada eski motordan COK daha siki: momentum 126
            # gunluk geriye bakis istiyor, ustune 60 gunluk oynaklik penceresi.
            # Yeterli gecmisi olmayan kontrat hesaba bile girmemeli.
            min_age_ms = int(cfg["min_listing_days"]) * MS_DAY
            min_volume = float(cfg["min_quote_volume_m"]) * 1e6
            allowed = {c["symbol"] for c in contracts
                       if not (c.get("onboardDate") and now_ms - int(c["onboardDate"]) < min_age_ms)}
            live_prices = {}
            for t in tickers:
                try:
                    lp = float(t.get("lastPrice", 0) or 0)
                except (TypeError, ValueError):
                    lp = 0.0
                if lp > 0:
                    live_prices[t["symbol"]] = lp
            liquid = [t for t in tickers if t.get("symbol") in allowed
                      and float(t.get("quoteVolume", 0) or 0) >= min_volume]
            liquid.sort(key=lambda t: -float(t.get("quoteVolume", 0) or 0))
            size = int(cfg["universe_size"])
            universe = [t["symbol"] for t in (liquid[:size] if size else liquid)]

            # 30.08 — HANGI FILTRE GERCEKTEN ELIYOR?
            # Evren "esigi gecenler -> hacme gore sirala -> ilk N" diye
            # seciliyor. Esigi gecen sembol sayisi N'den fazlaysa hacim esigi
            # HICBIR SEYI elemez; evreni tamamen universe_size belirler. Esigi
            # asagi cekip "daha cok coin taransin" beklemek bu durumda bos
            # umut. Ikisini ayri ayri sayip disari veriyoruz ki hangi ayarin
            # is yaptigi tahmin degil olcum olsun.
            _yas_elenen = len(tickers) - len([t for t in tickers if t.get("symbol") in allowed])
            _hacim_elenen = len([t for t in tickers if t.get("symbol") in allowed]) - len(liquid)
            _kirpilan = max(0, len(liquid) - len(universe))
            evren_teshis = {
                "borsadaki_kontrat": len(tickers),
                "yas_elenen": _yas_elenen,
                "hacim_esigi_m": round(min_volume / 1e6, 2),
                "hacim_elenen": _hacim_elenen,
                "esigi_gecen": len(liquid),
                "universe_size": size,
                "kirpilan": _kirpilan,
                "taranan": len(universe),
                "esik_baglayici_mi": bool(_hacim_elenen > 0 and _kirpilan == 0),
                "en_dusuk_taranan_hacim_m": (
                    round(float(liquid[len(universe) - 1].get("quoteVolume", 0) or 0) / 1e6, 1)
                    if universe else None),
            }
            _state["evren_teshis"] = evren_teshis
            if _kirpilan > 0 and _hacim_elenen > 0:
                log.info("TSMOM evren: hacim esigi %.1fM ile %d elendi ama %d tanesi zaten "
                         "universe_size=%d kirpmasina takildi — esigi dusurmek evreni "
                         "buyutmez, universe_size'i buyutmek buyutur.",
                         min_volume / 1e6, _hacim_elenen, _kirpilan, size)

            reasons: Dict[str, int] = {}
            passed: List[Dict[str, Any]] = []
            near_miss: Dict[str, Dict[str, Any]] = {}
            evaluated = 0
            sem = asyncio.Semaphore(4)
            cfg_risk = runtime.get()["risk"]
            router_cfg = decision_v4.router_config()

            async def inspect(symbol: str) -> None:
                nonlocal evaluated
                async with sem:
                    try:
                        daily = await _daily(symbol)
                        if binance.status().get("mode") != "live":
                            return
                        if len(daily) < strat.MIN_BARS:
                            reasons["yetersiz gecmis"] = reasons.get("yetersiz gecmis", 0) + 1
                            return
                        rate, hist = await _funding(symbol)
                        if binance.status().get("mode") != "live":
                            return
                        # CANLI fiyat. Gunluk barlar kapanmis barlardan olusuyor
                        # (momentum icin dogru), ama giris fiyati oradan alinamaz:
                        # 24 saate kadar eski olabilir.
                        live = live_prices.get(symbol)
                        # Kaldirac strateji katmanina gecmeli: stop tavani
                        # likidasyon mesafesinden turuyor, o da kaldiractan.
                        scfg = dict(cfg)
                        scfg["leverage"] = cfg_risk["default_leverage"]
                        d = await asyncio.to_thread(
                            strat.evaluate, symbol, daily, rate, hist, scfg, live)
                        if d.get("side"):
                            d["v4"] = await asyncio.to_thread(
                                regime_router.assess_tsmom, d, daily, router_cfg)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("%s degerlendirilemedi: %s", symbol, exc)
                        reasons["hata"] = reasons.get("hata", 0) + 1
                        return
                    evaluated += 1
                    if d.get("ok"):
                        passed.append(d)
                    else:
                        vetos = d.get("veto", [])
                        # SADECE kovalama kapisina takilanlar "az kaldi"
                        # sayiliyor; baska bir vetosu varsa listeye girmez.
                        if len(vetos) == 1 and vetos[0].startswith("fiyat sinyal barindan"):
                            near_miss[symbol] = d
                        for v in vetos:
                            key = v.split("(")[0].strip()
                            reasons[key] = reasons.get(key, 0) + 1

            await asyncio.gather(*(inspect(s) for s in universe))
            if binance.status().get("mode") != "live":
                _state.update(data_paused=True, data_pause_reason="Sembol taraması canlı veriden düştü")
                return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": exits}

            # SIRALAMA: |t| buyukten kucuge. Frekans kontrolu burada — esik
            # degil siralama. Esik yalnizca taban; asil secim goreceli guc.
            passed.sort(key=lambda d: -abs(d["t_stat"]))

            # Gunluk sinyal ayni kalsa da portfoy donup yeni firsatlara yer
            # acabilsin: dort saatlik kontrollu guc karsilastirmasi.
            rotation = await _rotate_for_better(passed, live_prices, cfg)

            saved = 0
            opened = 0
            open_blocks: Dict[str, int] = {}
            open_detail = ""
            max_open = int(cfg["max_open"])
            max_per_side = int(cfg["max_per_side"])
            per_scan = int(cfg["max_new_per_scan"])
            sides = _open_sides()
            # Kapilari GECEN her kurulumun neden acilmadigi kaydediliyor;
            # aday listesi bu sebeplerle birlikte yaziliyor. Onceden bu bilgi
            # yalnizca bir sayacta kaliyor, kullanici panelde bos kutu
            # goruyordu.
            blocked: Dict[str, str] = {}
            for d in passed:
                if _has_open(d["symbol"]):
                    blocked[d["symbol"]] = "araştırma kaydı zaten açık"
                    continue
                cooldown = _cooldown_remaining_hours(d["symbol"], cfg)
                if cooldown > 0:
                    blocked[d["symbol"]] = f"sembol soğuma süresi ({cooldown:.1f}s kaldı)"
                    reasons["sembol soğuma süresi"] = reasons.get("sembol soğuma süresi", 0) + 1
                    continue
                if saved >= per_scan:
                    blocked[d["symbol"]] = "tarama başına yeni işlem sınırı"
                    continue
                if _open_count() >= max_open:
                    blocked[d["symbol"]] = "açık kayıt sınırı dolu"
                    continue
                # AYNI YON SINIRI. Kripto perp'lerinde ortalama korelasyon
                # ~0.48; 10 pozisyon acmak 10 bagimsiz bahis degil ~1.9
                # bahis demek. Ayni yonde yiginmak riski cesitlendirmez,
                # sadece buyutur.
                side_limit = _side_limit_for(d, cfg)
                if sides.get(d["side"], 0) >= side_limit:
                    reasons["ayni yon siniri"] = reasons.get("ayni yon siniri", 0) + 1
                    blocked[d["symbol"]] = "aynı yön sınırı"
                    continue
                rid = _save(d, cfg)
                # Arastirma kaydi HER ZAMAN tutulur (olcum kesintiye ugramasin);
                # kagit pozisyon risk limitleri izin verirse acilir.
                why = await _open_position(d, rid, cfg, cfg_risk)
                if why:
                    # Sebebi sayiyoruz: "tariyorum ama islem acmiyor" sorusunun
                    # cevabi burada, tahminde degil.
                    key = why.split("(")[0].strip()
                    open_blocks[key] = open_blocks.get(key, 0) + 1
                    if not open_detail:
                        open_detail = why
                    blocked[d["symbol"]] = key
                else:
                    opened += 1
                sides[d["side"]] = sides.get(d["side"], 0) + 1
                saved += 1

            candidates = _write_candidates(passed, blocked, cfg, cfg_risk)

            # IZLEME LISTESI — hizli gozcunun her 30 sn'de bakacagi kisa liste.
            # Iki grup giriyor:
            #   1) kapilari GECEN ama bu taramada acilamayan kurulumlar
            #   2) YALNIZCA "fiyat sinyal barindan uzaklasti" diye elenenler
            # Ikinci grup onemli: gunluk momentum ayni kaliyor, degisen tek
            # sey fiyatin sinyal barina uzakligi. Fiyat geri cekilince o
            # kurulum acilabilir hale geliyor — bir sonraki tam taramayi
            # beklemek o firsati kaciriyordu.
            watch: List[str] = [d["symbol"] for d in passed]
            for sym, d in near_miss.items():
                if sym not in watch:
                    watch.append(sym)
            _state["watchlist"] = watch[:int(cfg.get("watchlist_size", 12))]

            _state.update(
                data_paused=False, data_pause_reason="",
                last_scan_at=db.now_ms(),
                last_duration=round(time.monotonic() - started, 2),
                last_universe=len(universe), last_evaluated=evaluated,
                last_passed=len(passed), last_saved=saved, last_opened=opened,
                last_candidates=candidates,
                last_rotation=rotation,
                block_reasons=dict(sorted(reasons.items(), key=lambda x: -x[1])[:10]),
                open_blocks=dict(sorted(open_blocks.items(), key=lambda x: -x[1])[:6]),
                open_detail=open_detail,
                top=[{"symbol": d["symbol"], "side": d["side"], "t": d["t_stat"],
                      "cost_r": d["total_cost_r"], "funding_bp": d["funding"].get("bp"),
                      "stop_pct": d.get("stop_pct"), "atr_pct": d.get("atr_pct")}
                     for d in passed[:10]],
            )
            log.info("TSMOM tarama: evren=%d degerlendirildi=%d gecti=%d kayit=%d islem=%d aday=%d cikis=%s",
                     len(universe), evaluated, len(passed), saved, opened, candidates, exits)
            return {"ok": True, "universe": len(universe), "evaluated": evaluated,
                    "passed": len(passed), "saved": saved, "opened": opened,
                    "candidates": candidates, "exits": exits, "rotation": rotation,
                    "open_blocks": _state["open_blocks"], "open_detail": open_detail,
                    "block_reasons": _state["block_reasons"], "top": _state["top"]}
        except Exception as exc:  # noqa: BLE001
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("TSMOM taramasi basarisiz")
            return {"ok": False, "error": _state["last_error"]}
        finally:
            _state["scanning"] = False


RSI_TARAMA_ARALIGI_MS = 3_600_000     # saatte bir yeter; RSI 1h barla calisiyor
_rsi_son_tarama = {"at": 0}


def _select_rsi_leads(radar: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """RSI sayfasındaki iki uç listeden ana motora izleme adayı çıkar.

    Çıplak RSI yön değildir. Bu yüzden yalnızca rsi_context'in bağlam
    verdiği satırlar alınır: aşırı bölgede beş bileşenli karar (`expect`)
    ya da nötr bölgede yeterince güçlü 1 saatlik trend (`trend`). Bunlar
    yine de emir değildir; watch_once günlük TSMOM kapılarını ayrıca arar.
    """
    limit = int(cfg.get("rsi_leads_limit", 12))
    min_t = float(cfg.get("rsi_leads_min_t", 1.5))
    if limit <= 0 or not cfg.get("rsi_leads_enabled", True):
        return []

    seen = set()
    leads: List[Dict[str, Any]] = []
    for item in list(radar.get("lowest") or []) + list(radar.get("highest") or []):
        symbol = str(item.get("symbol") or "").upper()
        ctx = item.get("ctx") or {}
        if not symbol or symbol in seen or not isinstance(ctx, dict):
            continue
        direction = ctx.get("expect")
        decisive = direction in ("up", "down")
        if not decisive:
            direction = ctx.get("trend")
            try:
                if direction not in ("up", "down") or abs(float(ctx.get("t_stat") or 0)) < min_t:
                    continue
            except (TypeError, ValueError):
                continue
        side = "LONG" if direction == "up" else "SHORT"
        try:
            confidence = abs(float(ctx.get("score") if decisive else ctx.get("t_stat") or 0))
        except (TypeError, ValueError):
            confidence = 0.0
        leads.append({
            "symbol": symbol, "side": side,
            "rsi": float(item.get("rsi") or ctx.get("rsi") or 0),
            "label": str(ctx.get("label") or ctx.get("trend_label") or "RSI adayı"),
            "zone": str(ctx.get("zone") or "neutral"),
            "verdict": str(ctx.get("verdict") or "notr"),
            "score": float(ctx.get("score") or 0),
            "t_stat": ctx.get("t_stat"),
            "confidence": round(confidence, 3),
            "decisive": decisive,
        })
        seen.add(symbol)

    # Beş bileşenli aşırı bölge kararları önce; sonra mutlak güven.
    leads.sort(key=lambda x: (not x["decisive"], -x["confidence"]))
    return leads[:limit]


def _rsi_direction_matches(decision: Dict[str, Any], lead: Dict[str, Any]) -> bool:
    """RSI yönü ile günlük ana motor yönü aynı mı."""
    return str(decision.get("side")) == str(lead.get("side"))


async def _rsi_tara_kaydet() -> int:
    """RSI radarını tara, ölçüm çağrılarını ve motor adaylarını güncelle.

    Sayfanın açılmasını beklemiyoruz. Radarın en yüksek/en düşük RSI
    listeleri ana motorun gözcüsüne aday sağlar; günlük TSMOM doğrulaması
    olmadan araştırma kaydı veya paper pozisyon açılmaz.
    """
    now = db.now_ms()
    if now - _rsi_son_tarama["at"] < RSI_TARAMA_ARALIGI_MS:
        return 0

    cfg = runtime.get()["tsmom"]
    if not cfg.get("rsi_leads_enabled", True):
        _rsi_son_tarama["at"] = now
        _state.update(rsi_leads=[], last_rsi_scan_at=now, last_rsi_new_calls=0)
        return 0

    # Gercekten KAC KAYIT acildigini say. Asiri bolgedeki sembol sayisi bunun
    # yerine gecmez: rsi_context.record() "belirsiz" kararlari yazmiyor ve ayni
    # sembol icin acik cagri varsa tekrar yazmiyor.
    onceki = db.query_one("SELECT COUNT(*) c FROM rsi_calls")["c"]
    from ..api import market as _market
    radar = await _market.rsi_radar(
        interval="1h", limit=int(cfg.get("rsi_leads_universe_size", 80)),
        context_limit=24, _user=None)
    # Yalnızca başarılı taramayı saatlik kotaya yaz. Ağ hatası olursa gözcü
    # bir sonraki turda tekrar dener; bir saat boyunca sessiz kalmaz.
    _rsi_son_tarama["at"] = db.now_ms()
    yeni = db.query_one("SELECT COUNT(*) c FROM rsi_calls")["c"] - onceki
    leads = _select_rsi_leads(radar, cfg)
    _state.update(rsi_leads=leads, last_rsi_scan_at=db.now_ms(),
                  last_rsi_new_calls=yeni)
    db.set_setting("tsmom_rsi_leads", {
        "at": _state["last_rsi_scan_at"], "items": leads, "new_calls": yeni,
    })
    if yeni or leads:
        log.info("RSI otomatik tarama: %d yeni cagri, %d motor adayi", yeni, len(leads))
    return yeni


async def watch_once() -> Dict[str, Any]:
    """HIZLI GOZCU. Tam tarama arasinda surekli calisir.

    Iki isi var:
      1) CIKISLAR. Onceden cikislar yalnizca tam taramada kontrol ediliyordu;
         tarama saatte bir olunca stop veya hedefe deginen bir pozisyon bir
         saate kadar acik kaliyordu. Artik gecikme saniyeler mertebesinde.
      2) IZLEME LISTESI. Gunluk momentum gun icinde DEGISMEZ — degisen tek
         sey canli fiyat. "Fiyat sinyal barindan uzaklasti" diye elenmis bir
         kurulum, fiyat geri cekilince acilabilir hale gelir. Gozcu bunu
         yakaliyor.

    API maliyeti dusuk: gunluk barlar onbellekten geliyor (gun icinde
    degismiyorlar), yalnizca ticker ve fonlama tazeleniyor.
    """
    if _scan_lock.locked() or _watch_lock.locked():
        return {"ok": True, "skipped": "tarama zaten calisiyor"}
    async with _watch_lock:
        started = time.monotonic()
        try:
            cfg = runtime.get()["tsmom"]
            if binance.status().get("mode") != "live":
                _state.update(data_paused=True, data_pause_reason="Gözcü canlı veri bekliyor",
                              last_watch_at=db.now_ms())
                return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": {}}
            exits = await evaluate_open()
            await live_trade.reconcile()
            if binance.status().get("mode") != "live":
                return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": exits}

            # RSI radarinin kendi cagrilarini da burada olcuyoruz: ayri bir
            # dongu acmak yerine zaten 30 saniyede bir donen tura biniyor.
            # Suresi dolmamis cagri yoksa hicbir istek yapmiyor.
            try:
                await rsi_context.evaluate_due()
            except Exception:  # noqa: BLE001
                log.debug("RSI cagri degerlendirmesi atlandi", exc_info=True)

            # 29.08 DUZELTMESI — TARAFLI ORNEKLEM
            # Onceden rsi_context.record() YALNIZCA /api/market/rsi-radar
            # endpoint'inden cagriliyordu, yani karne ancak biri sayfayi
            # actiginda doluyordu. Insan piyasa hareketliyken bakma
            # egiliminde oldugu icin bu, karneyi "tahminlerimiz iyi mi"
            # sorusundan uzaklastirip "bakildigi anlarda iyi mi" sorusuna
            # ceviriyordu. Artik kayit saatlik olarak kendiliginden dusuyor.
            try:
                await _rsi_tara_kaydet()
            except Exception:  # noqa: BLE001
                log.debug("RSI otomatik tarama atlandi", exc_info=True)

            opened = 0
            saved = 0
            checked = 0
            rsi_by = {x["symbol"]: x for x in (_state.get("rsi_leads") or [])
                      if x.get("symbol")}
            # RSI adayları önce değerlendirilir; ana taramanın izleme listesi
            # ardından eklenir. Dict sırası korunur ve sembol tekilleşir.
            watch = list(dict.fromkeys(
                list(rsi_by) + list(_state.get("watchlist") or [])))
            if not (cfg.get("enabled", True) and enabled()) or not watch:
                _state.update(last_watch_at=db.now_ms(), last_watch_exits=exits,
                              last_watch_opened=0, last_watch_checked=0,
                              last_rsi_checked=0, last_rsi_aligned=0, rsi_blocks={},
                              last_watch_ms=int((time.monotonic() - started) * 1000))
                return {"ok": True, "exits": exits, "opened": 0, "checked": 0}

            cfg_risk = runtime.get()["risk"]
            router_cfg = decision_v4.router_config()
            try:
                tick = {t["symbol"]: float(t.get("lastPrice", 0) or 0)
                        for t in await binance.ticker_24h()}
                if binance.status().get("mode") != "live":
                    _state.update(data_paused=True, data_pause_reason="Gözcü ticker verisi canlı moddan düştü")
                    return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": exits}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"ticker alinamadi: {exc}"}

            sides = _open_sides()
            max_open = int(cfg["max_open"])
            max_per_side = int(cfg["max_per_side"])
            sem = asyncio.Semaphore(4)
            hazir: List[Dict[str, Any]] = []
            rsi_checked = 0
            rsi_aligned = 0
            rsi_blocks: Counter = Counter()

            async def bak(symbol: str) -> None:
                nonlocal checked, rsi_checked, rsi_aligned
                if _has_open(symbol):
                    return
                if _cooldown_remaining_hours(symbol, cfg) > 0:
                    if rsi_by.get(symbol):
                        rsi_blocks["sembol soğuma süresi"] += 1
                    return
                lead = rsi_by.get(symbol)
                async with sem:
                    try:
                        daily = await _daily(symbol)      # onbellekten
                        if binance.status().get("mode") != "live":
                            return
                        if len(daily) < strat.MIN_BARS:
                            return
                        rate, hist = await _funding(symbol)
                        if binance.status().get("mode") != "live":
                            return
                        scfg = dict(cfg)
                        scfg["leverage"] = cfg_risk["default_leverage"]
                        d = await asyncio.to_thread(
                            strat.evaluate, symbol, daily, rate, hist, scfg,
                            tick.get(symbol))
                        if d.get("side"):
                            d["v4"] = await asyncio.to_thread(
                                regime_router.assess_tsmom, d, daily, router_cfg)
                    except Exception:  # noqa: BLE001
                        if lead:
                            rsi_blocks["analiz hatası"] += 1
                        return
                    checked += 1
                    if lead:
                        rsi_checked += 1
                    if not d.get("ok"):
                        if lead:
                            vetos = d.get("veto") or ["ana motor filtresi"]
                            rsi_blocks[str(vetos[0]).split("(")[0].strip()] += 1
                        return
                    if lead and not _rsi_direction_matches(d, lead):
                        rsi_blocks["RSI ile günlük yön uyuşmuyor"] += 1
                        return
                    if lead:
                        d["rsi_lead"] = lead
                        rsi_aligned += 1
                    hazir.append(d)

            await asyncio.gather(*(bak(sym) for sym in watch))
            if binance.status().get("mode") != "live":
                _state.update(data_paused=True, data_pause_reason="Gözcü sembol verisi canlı moddan düştü")
                return {"ok": True, "skipped": "canlı veri bekleniyor", "exits": exits}
            hazir.sort(key=lambda d: -abs(d["t_stat"]))

            # Gozcu de tam tarama gibi TURDA sinirli aciyor. Sinir olmasaydi
            # tek bir turda kontenjan doluyordu; asil kisitlayici max_open
            # olsa da davranisin ongorulebilir kalmasi onemli.
            per_cycle = int(cfg["max_new_per_scan"])
            for d in hazir:
                if opened >= per_cycle:
                    break
                if _open_count() >= max_open:
                    break
                if sides.get(d["side"], 0) >= _side_limit_for(d, cfg):
                    continue
                if _has_open(d["symbol"]):
                    continue
                rid = _save(d, cfg)
                saved += 1
                why = await _open_position(d, rid, cfg, cfg_risk)
                if not why:
                    opened += 1
                sides[d["side"]] = sides.get(d["side"], 0) + 1
                # Acilan sembol izleme listesinden dussun
                _state["watchlist"] = [x for x in (_state.get("watchlist") or [])
                                       if x != d["symbol"]]

            _state.update(last_watch_at=db.now_ms(), last_watch_exits=exits,
                          data_paused=False, data_pause_reason="",
                          last_watch_opened=opened, last_watch_checked=checked,
                          last_watch_saved=saved,
                          last_rsi_checked=rsi_checked, last_rsi_aligned=rsi_aligned,
                          rsi_blocks=dict(rsi_blocks.most_common(6)),
                          last_watch_ms=int((time.monotonic() - started) * 1000))
            if saved or any(exits.get(k) for k in ("stop", "target", "flip", "fade", "sure", "durgun")):
                log.info("gozcu: %d kayit acildi (%d parayla aynalandi), cikis=%s, %d sembol bakildi",
                         saved, opened, exits, checked)
            _state["last_watch_saved"] = saved
            return {"ok": True, "exits": exits, "saved": saved,
                    "opened": opened, "checked": checked}
        except Exception as exc:  # noqa: BLE001
            _state["last_watch_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("Gozcu turu hata verdi")
            return {"ok": False, "error": _state["last_watch_error"]}


async def _watch_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            cfg = runtime.get()["tsmom"]
            await watch_once()
            await asyncio.sleep(max(10, int(cfg["watch_interval_seconds"])))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Gozcu dongusu kirildi, 60 sn sonra tekrar")
            await asyncio.sleep(60)


async def _selftest_loop() -> None:
    """Gunde bir kez oz-backtest. Acilista 3 dk bekler.

    Ayri dongu: backtest dakikalar surebiliyor ve tarama dongusunu
    bekletmemeli. Sonuc app_settings'e yazilir, panel oradan okur.
    """
    await asyncio.sleep(180)
    while True:
        try:
            if enabled() and runtime.get()["tsmom"].get("selftest_enabled", True):
                cfgt = runtime.get()["tsmom"]
                await run_selftest(int(cfgt.get("selftest_days", 120)),
                                   int(cfgt.get("selftest_symbols", 12)))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Oz-backtest hata verdi")
        await asyncio.sleep(24 * 3600)


async def _loop() -> None:
    # Acilista hemen taramamak icin kisa bekleme: eski motor ve WS zaten
    # ayaga kalkiyor, ayni anda 150 sembolluk gunluk mum cekmek agirlik
    # limitini gereksiz zorlar.
    await asyncio.sleep(45)
    while True:
        try:
            cfg = runtime.get()["tsmom"]
            if cfg.get("enabled", True) and enabled():
                await scan_once()
            else:
                # Motor kapali olsa bile ACIK pozisyonlar izlenmeye devam
                # eder. Yoksa "motoru kapattim" demek "acik islemlerim
                # kaderine terk edildi" demek oluyordu.
                await evaluate_open()
            await asyncio.sleep(max(300, int(cfg["scan_interval_seconds"])))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("TSMOM dongusu hata verdi, 5 dk sonra tekrar")
            await asyncio.sleep(300)


async def start() -> None:
    global _task, _selftest_task, _watch_task
    if _selftest_task is None or _selftest_task.done():
        _selftest_task = asyncio.create_task(_selftest_loop())
    if _watch_task is None or _watch_task.done():
        _watch_task = asyncio.create_task(_watch_loop())
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        cfg = runtime.get()["tsmom"]
        log.info("TSMOM motoru baslatildi — tam tarama %ds, gozcu %ds",
                 cfg["scan_interval_seconds"], cfg["watch_interval_seconds"])


async def stop() -> None:
    global _task, _selftest_task, _watch_task
    if _watch_task is not None:
        _watch_task.cancel()
        try:
            await _watch_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _watch_task = None
    if _selftest_task is not None:
        _selftest_task.cancel()
        try:
            await _selftest_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _selftest_task = None
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
