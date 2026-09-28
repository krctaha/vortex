"""V4 noktasal 15dk özellik deposu ve gecikmeli getiri etiketleyicisi."""
from __future__ import annotations

import json
import math
from typing import Any, Dict, Iterable, Sequence

import numpy as np

from .. import db, runtime
from ..indicators import regime as regime_mod, structure as structure_mod

MS_HOUR = 3_600_000
MS_DAY = 86_400_000


def _ema(values: np.ndarray, period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    value = float(values[0])
    for x in values[1:]:
        value = alpha * float(x) + (1 - alpha) * value
    return value


def build(symbol: str, rows: Sequence[Sequence], ticker: Dict[str, Any],
          hunter: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    """Yalnız son KAPANMIŞ barın o anda bilinen özelliklerini üretir."""
    if len(rows) < 60:
        return None
    o = np.asarray([float(x[1]) for x in rows], dtype=float)
    h = np.asarray([float(x[2]) for x in rows], dtype=float)
    l = np.asarray([float(x[3]) for x in rows], dtype=float)
    c = np.asarray([float(x[4]) for x in rows], dtype=float)
    v = np.asarray([float(x[5]) for x in rows], dtype=float)
    if not np.all(np.isfinite(c[-60:])) or c[-1] <= 0:
        return None
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    atr = float(np.mean(tr[-14:]))
    logret = np.diff(np.log(c[-41:]))
    rv = float(np.std(logret, ddof=1) * math.sqrt(96) * 100) if len(logret) > 1 else 0.0
    vol_base = float(np.median(v[-21:-1])) if len(v) >= 21 else 0.0
    vol_rel = float(v[-1] / vol_base) if vol_base > 0 else 0.0
    quote = float(rows[-1][7]) if len(rows[-1]) > 7 else 0.0
    taker_buy_quote = float(rows[-1][10]) if len(rows[-1]) > 10 else 0.0
    reg = regime_mod.classify(h, l, c)
    # ICT/SMC etiketlerini "edge" diye varsaymıyoruz. Repaint korumalı
    # deterministik yapılarını özellik olarak kaydedip ileri getiride gerçekten
    # fark üretip üretmediklerini ölçülebilir hale getiriyoruz.
    events = structure_mod.structure_events(h, l, c)
    sweeps = structure_mod.liquidity_sweeps(h, l, c, o)
    gaps = structure_mod.active_fvgs(structure_mod.fair_value_gaps(h, l, c))
    equal = structure_mod.equal_levels(h, l)
    pd = structure_mod.premium_discount(h, l, min(120, len(c)))
    last_event = events[-1] if events else None
    last_sweep = sweeps[-1] if sweeps else None
    recent_sweeps = [x for x in sweeps if x.index >= len(c) - 24]
    bull_gaps = [x for x in gaps if x.direction == "bull"]
    bear_gaps = [x for x in gaps if x.direction == "bear"]

    nearest_gap = min(
        gaps,
        key=lambda x: min(abs(c[-1] - x.top), abs(c[-1] - x.bottom)),
        default=None,
    )
    pd_range = float(pd.get("range_high", 0)) - float(pd.get("range_low", 0))

    def ret(bars: int) -> float | None:
        return round((c[-1] / c[-1-bars] - 1) * 100, 5) if len(c) > bars else None

    features = {
        "ret_1h_pct": ret(4), "ret_4h_pct": ret(16), "ret_12h_pct": ret(48),
        "atr_pct": round(atr / c[-1] * 100, 5),
        "realized_vol_daily_pct": round(rv, 5),
        "relative_volume": round(vol_rel, 4),
        "taker_buy_ratio": round(taker_buy_quote / quote, 5) if quote > 0 else None,
        "candle_body_pct": round((c[-1] - o[-1]) / o[-1] * 100, 5) if o[-1] else None,
        "close_location": round((c[-1] - l[-1]) / (h[-1] - l[-1]), 5) if h[-1] > l[-1] else .5,
        "ema9_gap_pct": round((_ema(c[-60:], 9) / c[-1] - 1) * 100, 5),
        "ema25_gap_pct": round((_ema(c[-60:], 25) / c[-1] - 1) * 100, 5),
        "price_change_24h_pct": round(float(ticker.get("priceChangePercent", 0) or 0), 5),
        "regime_confidence": reg.get("confidence"), "adx": reg.get("adx"),
        "choppiness": reg.get("choppiness"), "efficiency_ratio": reg.get("efficiency_ratio"),
        "volatility_rank": reg.get("volatility_rank"),
        "hunter_phase": (hunter or {}).get("phase"),
        "hunter_ready": bool((hunter or {}).get("ok")),
        "hunter_side": (hunter or {}).get("side"),
        "hunter_score": (hunter or {}).get("score"),
        "structure_bias": last_event.direction if last_event else "neutral",
        "structure_event": last_event.kind if last_event else None,
        "structure_event_age_bars": len(c) - 1 - last_event.index if last_event else None,
        "liquidity_sweep_direction": last_sweep.direction if last_sweep else None,
        "liquidity_sweep_age_bars": len(c) - 1 - last_sweep.index if last_sweep else None,
        "liquidity_sweep_closed_back": bool(last_sweep.closed_back) if last_sweep else None,
        "liquidity_sweeps_6h": len(recent_sweeps),
        "active_fvg_bull": len(bull_gaps), "active_fvg_bear": len(bear_gaps),
        "nearest_fvg_direction": nearest_gap.direction if nearest_gap else None,
        "nearest_fvg_distance_pct": (
            round(min(abs(c[-1] - nearest_gap.top), abs(c[-1] - nearest_gap.bottom))
                  / c[-1] * 100, 5) if nearest_gap else None),
        "equal_high_groups": len(equal.get("eqh") or []),
        "equal_low_groups": len(equal.get("eql") or []),
        "dealing_range_location": (
            round((c[-1] - float(pd["range_low"])) / pd_range, 5)
            if pd_range > 0 else None),
    }
    return {
        "symbol": symbol, "interval": "15m", "bar_time": int(rows[-1][6]),
        "captured_at": db.now_ms(), "price": float(c[-1]),
        "quote_volume_24h": float(ticker.get("quoteVolume", 0) or 0),
        "regime": str(reg.get("regime") or "bilinmiyor"), "features": features,
    }


def capture_cycle(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Bir Avcı döngüsünün özelliklerini atomik kaydeder ve eski satırları etiketler."""
    cfg = runtime.get()["decision_v4"]
    if not cfg.get("feature_store_enabled", True):
        return {"captured": 0, "labeled": 0}
    sample_ms = int(cfg.get("feature_sample_minutes", 60)) * 60_000
    universe_size = int(cfg.get("feature_universe_size", 120))
    ordered = sorted(items, key=lambda x: -float(
        (x.get("ticker") or {}).get("quoteVolume", 0) or 0))[:universe_size]
    prepared = []
    histories: Dict[str, Sequence[Sequence]] = {}
    for item in ordered:
        rows = item.get("rows") or []
        if not rows or (int(rows[-1][6]) + 1) % sample_ms != 0:
            continue
        snap = build(item["symbol"], rows, item.get("ticker") or {}, item.get("hunter"))
        if not snap:
            continue
        histories[item["symbol"]] = rows
        prepared.append((snap["symbol"], snap["interval"], snap["bar_time"], snap["captured_at"],
                         snap["price"], snap["quote_volume_24h"], snap["regime"],
                         json.dumps(snap["features"], ensure_ascii=False)))
    before = int((db.query_one("SELECT COUNT(*) c FROM market_feature_snapshots") or {"c": 0})["c"])
    db.executemany(
        "INSERT OR IGNORE INTO market_feature_snapshots(symbol,interval,bar_time,captured_at,"
        "price,quote_volume_24h,regime,features) VALUES(?,?,?,?,?,?,?,?)", prepared)
    after = int((db.query_one("SELECT COUNT(*) c FROM market_feature_snapshots") or {"c": 0})["c"])

    cutoff = db.now_ms() - 12 * MS_HOUR
    pending = db.query(
        "SELECT id,symbol,bar_time,price FROM market_feature_snapshots "
        "WHERE labeled_at IS NULL AND bar_time<=? ORDER BY bar_time LIMIT 5000", (cutoff,))
    updates = []
    for row in pending:
        bars = histories.get(row["symbol"])
        if not bars:
            continue
        future = {}
        for hours in (1, 4, 12):
            target = int(row["bar_time"]) + hours * MS_HOUR
            hit = next((b for b in bars if int(b[6]) >= target), None)
            if hit is not None:
                future[hours] = (float(hit[4]) / float(row["price"]) - 1) * 100
        if 12 not in future:
            continue
        updates.append((round(future.get(1, 0), 6), round(future.get(4, 0), 6),
                        round(future[12], 6), db.now_ms(), row["id"]))
    db.executemany(
        "UPDATE market_feature_snapshots SET forward_1h_pct=?,forward_4h_pct=?,"
        "forward_12h_pct=?,labeled_at=? WHERE id=?", updates)
    retention = db.now_ms() - int(cfg.get("feature_retention_days", 730)) * MS_DAY
    db.execute("DELETE FROM market_feature_snapshots WHERE bar_time<?", (retention,))
    out = {"captured": max(0, after - before), "labeled": len(updates)}
    db.set_setting("feature_store_last", {**out, "at": db.now_ms(), "universe": len(prepared)})
    return out


def status() -> Dict[str, Any]:
    total = dict(db.query_one(
        "SELECT COUNT(*) total,SUM(CASE WHEN labeled_at IS NOT NULL THEN 1 ELSE 0 END) labeled,"
        "MIN(bar_time) oldest,MAX(bar_time) newest,COUNT(DISTINCT symbol) symbols "
        "FROM market_feature_snapshots") or {})
    return {
        "enabled": bool(runtime.get()["decision_v4"].get("feature_store_enabled", True)),
        "total": int(total.get("total") or 0), "labeled": int(total.get("labeled") or 0),
        "symbols": int(total.get("symbols") or 0), "oldest": total.get("oldest"),
        "newest": total.get("newest"), "last": db.get_setting("feature_store_last", {}) or {},
        "sample_minutes": int(runtime.get()["decision_v4"].get("feature_sample_minutes", 60)),
        "universe_size": int(runtime.get()["decision_v4"].get("feature_universe_size", 120)),
    }
