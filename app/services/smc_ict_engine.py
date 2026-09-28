"""SMC/ICT market-structure engine.

The engine deliberately treats SMC terms as an ordered state machine, not as
independent indicator votes.  A setup can only advance through:

    HTF draw-on-liquidity bias -> liquidity raid -> displacement/MSS
    -> CISD plus FVG/IFVG confirmation -> executable paper plan

All structure primitives use closed candles and confirmed pivots from
``app.indicators.structure``.  This module never sends an order; execution is
kept behind the existing explicit live-trade safety gate.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from ..indicators import core, structure as st
from . import binance, market_library


HTF_INTERVAL = "4h"
LTF_INTERVAL = "15m"
MIN_RR = 1.5


def _finite(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _last_finite(values: Sequence[float], default: float = 0.0) -> float:
    for value in reversed(values):
        number = _finite(value)
        if number is not None:
            return number
    return default


def _closed_rows(rows: List[list]) -> List[list]:
    """Drop an open last candle without inventing its interval duration."""
    if not rows:
        return []
    now_ms = int(time.time() * 1000)
    result = []
    previous = -1
    for row in rows:
        if len(row) < 7:
            raise ValueError("Mum kapanış zamanı eksik")
        opened, closed = int(row[0]), int(row[6])
        values = [float(x) for x in row[1:6]]
        o, h, l, c, v = values
        if (opened <= previous or closed < opened or
                not all(math.isfinite(x) for x in values) or
                min(o, h, l, c) <= 0 or v < 0 or h < max(o, c) or l > min(o, c)):
            raise ValueError("Geçersiz veya sırasız OHLCV")
        previous = opened
        if closed < now_ms:
            result.append(row)
    return result


@dataclass(frozen=True)
class Displacement:
    index: int
    direction: str
    body_atr: float
    range_multiple: float
    close: float

    def dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "direction": self.direction,
            "body_atr": round(self.body_atr, 3),
            "range_multiple": round(self.range_multiple, 3),
            "close": self.close,
        }


@dataclass(frozen=True)
class Cisd:
    index: int
    direction: str
    level: float
    source_candle: int

    def dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "direction": self.direction,
            "level": self.level,
            "source_candle": self.source_candle,
        }


def find_displacement(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                      close: np.ndarray, atr: np.ndarray, direction: str,
                      start: int, end: Optional[int] = None,
                      min_body_atr: float = 0.8,
                      min_range_multiple: float = 1.35) -> Optional[Displacement]:
    """Return the first directional expansion candle after ``start``.

    The body must be large versus ATR and the full range versus the preceding
    20-bar median.  Requiring both avoids calling a long-wick stop raid itself
    displacement.
    """
    stop = min(len(close), end if end is not None else len(close))
    for i in range(max(20, start), stop):
        atr_i = _finite(atr[i], 0.0) or 0.0
        if atr_i <= 0:
            continue
        body = abs(float(close[i] - open_[i]))
        bar_range = float(high[i] - low[i])
        previous = high[max(0, i - 20):i] - low[max(0, i - 20):i]
        median_range = float(np.median(previous)) if len(previous) else 0.0
        directional = close[i] > open_[i] if direction == "bull" else close[i] < open_[i]
        closes_near_edge = (
            close[i] >= low[i] + bar_range * 0.68 if direction == "bull"
            else close[i] <= high[i] - bar_range * 0.68
        )
        range_multiple = bar_range / median_range if median_range > 0 else 0.0
        if (directional and closes_near_edge and body / atr_i >= min_body_atr
                and range_multiple >= min_range_multiple):
            return Displacement(i, direction, body / atr_i, range_multiple,
                                float(close[i]))
    return None


def find_cisd(open_: np.ndarray, close: np.ndarray, direction: str,
              start: int, end: Optional[int] = None,
              source_lookback: int = 12) -> Optional[Cisd]:
    """Detect Change in State of Delivery with closed-candle confirmation.

    For a bullish shift the reference is the open of the last bearish delivery
    candle; a close above it confirms CISD.  The bearish definition is mirrored.
    """
    if len(close) < 2:
        return None
    source = None
    lower = max(0, start - source_lookback)
    for i in range(min(start, len(close) - 1), lower - 1, -1):
        opposing = close[i] < open_[i] if direction == "bull" else close[i] > open_[i]
        if opposing:
            source = i
            break
    if source is None:
        return None
    level = float(open_[source])
    stop = min(len(close), end if end is not None else len(close))
    for i in range(max(source + 1, start), stop):
        confirmed = close[i] > level if direction == "bull" else close[i] < level
        if confirmed:
            return Cisd(i, direction, level, source)
    return None


def _latest_matching(items: Iterable[Any], direction: str, since: int) -> Any:
    matches = [item for item in items
               if item.direction == direction and item.index >= since]
    return matches[-1] if matches else None


def _htf_context(parsed: Dict[str, np.ndarray]) -> Dict[str, Any]:
    high, low, close = parsed["high"], parsed["low"], parsed["close"]
    events = st.structure_events(high, low, close)
    pivots = st.find_pivots(high, low)
    pd = st.premium_discount(high, low, min(120, len(close)))
    price = float(close[-1])
    bias = events[-1].direction if events else "neutral"

    highs = sorted({float(p.price) for p in pivots if p.kind == "high" and p.price > price})
    lows = sorted({float(p.price) for p in pivots if p.kind == "low" and p.price < price}, reverse=True)
    if bias == "bull":
        target = highs[0] if highs else _finite(pd.get("range_high"), price)
        location_ok = price <= _finite(pd.get("equilibrium"), price)
        draw = "buy_side_liquidity"
    elif bias == "bear":
        target = lows[0] if lows else _finite(pd.get("range_low"), price)
        location_ok = price >= _finite(pd.get("equilibrium"), price)
        draw = "sell_side_liquidity"
    else:
        target, location_ok, draw = None, False, "undetermined"
    return {
        "bias": bias,
        "price": price,
        "last_event": events[-1].dict() if events else None,
        "draw_on_liquidity": draw,
        "target": target,
        "location": "discount" if price < _finite(pd.get("equilibrium"), price)
                    else "premium" if price > _finite(pd.get("equilibrium"), price)
                    else "equilibrium",
        "location_ok": bool(location_ok),
        "dealing_range": {key: round(float(value), 8) for key, value in pd.items()},
    }


def _phase(htf_ok: bool, sweep: Any, displacement: Any, mss: Any,
           cisd: Any, imbalance: Any, rr: float) -> str:
    if not htf_ok:
        return "HTF_BIAS_BEKLE"
    if sweep is None:
        return "LIKIDITE_BEKLE"
    if displacement is None or mss is None:
        return "MSS_BEKLE"
    if cisd is None or imbalance is None:
        return "TEYIT_BEKLE"
    if rr < MIN_RR:
        return "RR_YETERSIZ"
    return "HAZIR"


def analyze_rows(symbol: str, htf_rows: List[list], ltf_rows: List[list],
                 htf_interval: str = HTF_INTERVAL,
                 ltf_interval: str = LTF_INTERVAL,
                 context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    htf_rows, ltf_rows = _closed_rows(htf_rows), _closed_rows(ltf_rows)
    if ltf_rows:
        htf_rows = [row for row in htf_rows if int(row[6]) <= int(ltf_rows[-1][6])]
    if len(htf_rows) < 80 or len(ltf_rows) < 100:
        return {"ok": False, "symbol": symbol.upper(), "error": "yetersiz kapalı mum"}

    hp = binance.parse_klines(htf_rows)
    lp = binance.parse_klines(ltf_rows)
    htf = _htf_context(hp)
    direction = htf["bias"]
    side = "LONG" if direction == "bull" else "SHORT" if direction == "bear" else "WAIT"
    o, h, l, c = lp["open"], lp["high"], lp["low"], lp["close"]
    atr = core.atr(h, l, c, 14)
    events = st.structure_events(h, l, c)
    sweeps = st.liquidity_sweeps(h, l, c, o, min_wick_ratio=0.35)
    gaps = st.fair_value_gaps(h, l, c)
    blocks = st.order_blocks(o, h, l, c)
    recent_start = max(0, len(c) - 64)

    sweep = _latest_matching(
        (item for item in sweeps if item.closed_back), direction, recent_start
    ) if direction in ("bull", "bear") else None
    sequence_start = sweep.index + 1 if sweep else len(c)
    displacement = find_displacement(o, h, l, c, atr, direction, sequence_start) \
        if direction in ("bull", "bear") and sweep else None
    mss = next((event for event in events
                if displacement and event.index >= displacement.index
                and event.direction == direction), None)
    cisd = find_cisd(o, c, direction, sequence_start) \
        if direction in ("bull", "bear") and sweep else None

    confirmation_at = max(
        displacement.index if displacement else -1,
        mss.index if mss else -1,
        cisd.index if cisd else -1,
    )
    same_direction_fvgs = [gap for gap in gaps
                           if gap.direction == direction
                           and displacement and gap.index >= displacement.index
                           and gap.inversed_at is None and gap.filled_at is None]
    standard_fvg = same_direction_fvgs[-1] if same_direction_fvgs else None
    inverse_direction = "bear" if direction == "bull" else "bull"
    ifvgs = [gap for gap in gaps
             if gap.direction == inverse_direction
             and gap.inversed_at is not None
             and displacement and gap.inversed_at >= displacement.index
             and (all(c[j] >= gap.bottom for j in range(gap.inversed_at + 1, len(c)))
                  if direction == "bull" else
                  all(c[j] <= gap.top for j in range(gap.inversed_at + 1, len(c))))]
    ifvg = ifvgs[-1] if ifvgs else None
    imbalance = ifvg or standard_fvg

    current = float(c[-1])
    atr_now = _last_finite(atr, current * 0.005)
    if imbalance is not None:
        entry = (float(imbalance.top) + float(imbalance.bottom)) / 2.0
    else:
        entry = current
    if sweep is not None:
        sweep_bar = sweep.index
        stop = (float(l[sweep_bar]) - atr_now * 0.12 if side == "LONG"
                else float(h[sweep_bar]) + atr_now * 0.12)
    else:
        stop = entry - atr_now * 1.5 if side == "LONG" else entry + atr_now * 1.5
    risk = abs(entry - stop)
    target = _finite(htf.get("target"))
    target_valid = target is not None and (
        target > entry if side == "LONG" else target < entry if side == "SHORT" else False
    )
    rr = abs(target - entry) / risk if target_valid and risk > 0 else 0.0

    funding = _finite(((context or {}).get("funding") or {}).get("rate"))
    crowding_ok = funding is None or (
        funding < 0.0005 if side == "LONG" else funding > -0.0005
    )
    htf_ok = direction in ("bull", "bear")
    steps = [
        {"key": "htf", "label": "HTF likidite yönü", "ok": htf_ok, "weight": 20},
        {"key": "sweep", "label": "Likidite süpürmesi + reclaim", "ok": sweep is not None, "weight": 20},
        {"key": "displacement", "label": "Displacement", "ok": displacement is not None, "weight": 15},
        {"key": "mss", "label": "MSS / CHoCH kapanışı", "ok": mss is not None, "weight": 15},
        {"key": "cisd", "label": "CISD", "ok": cisd is not None, "weight": 10},
        {"key": "imbalance", "label": "IFVG / FVG", "ok": imbalance is not None, "weight": 10},
        {"key": "pd", "label": "Premium / discount konumu", "ok": htf["location_ok"], "weight": 5},
        {"key": "crowding", "label": "Funding kalabalığı yok", "ok": funding is not None and crowding_ok, "weight": 5},
    ]
    score = sum(step["weight"] for step in steps if step["ok"])
    phase = _phase(htf_ok, sweep, displacement, mss, cisd, imbalance, rr)
    ready = phase == "HAZIR" and confirmation_at >= sequence_start
    # Readiness describes a pending research setup, never a historical fill.
    if ready and imbalance is not None:
        zone_available = (imbalance.inversed_at if ifvg is not None else imbalance.index + 1)
        confirmed = max(confirmation_at, zone_available)
        if any(l[i] <= entry <= h[i] for i in range(confirmed + 1, len(c))):
            phase, ready = "GIRIS_GECMIS", False
    if ready and (side == "LONG" and stop >= entry or side == "SHORT" and stop <= entry):
        phase, ready = "GECERSIZ_STOP", False
    if ready and (len(c) - 1 - confirmation_at > 12 or
                  (side == "LONG" and np.min(l[confirmation_at:]) <= stop) or
                  (side == "SHORT" and np.max(h[confirmation_at:]) >= stop)):
        phase, ready = "KURULUM_GECERSIZ", False
    if ready and not crowding_ok:
        phase, ready = "FUNDING_BEKLE", False
    active_block = next((block for block in reversed(blocks)
                         if block.direction == direction and block.mitigated_at is None), None)

    reasons = [step["label"] for step in steps if step["ok"]]
    missing = [step["label"] for step in steps if not step["ok"]]
    model = "IFVG_CISD" if ifvg is not None else "MSS_FVG"
    result = {
        "ok": True,
        "symbol": symbol.upper(),
        "engine": "smc_ict_v5",
        "as_of": int(ltf_rows[-1][6]),
        "mode": "paper",
        "live_execution": False,
        "demo": binance.is_demo(),
        "model": model,
        "htf_interval": htf_interval,
        "ltf_interval": ltf_interval,
        "side": side,
        "phase": phase,
        "ready": ready,
        "score": score,
        "max_score": 100,
        "price": round(current, 8),
        "entry": round(entry, 8),
        "stop": round(stop, 8),
        "target": round(float(target), 8) if target_valid else None,
        "rr": round(rr, 2),
        "htf": htf,
        "sequence": {
            "sweep": sweep.dict() if sweep else None,
            "displacement": displacement.dict() if displacement else None,
            "mss": mss.dict() if mss else None,
            "cisd": cisd.dict() if cisd else None,
            "fvg": standard_fvg.dict() if standard_fvg else None,
            "ifvg": ifvg.dict() if ifvg else None,
            "order_block": active_block.dict() if active_block else None,
        },
        "steps": steps,
        "reasons": reasons,
        "missing": missing,
        "funding_bp": round(funding * 10_000, 2) if funding is not None else None,
        "confirmation_age_bars": len(c) - 1 - confirmation_at if confirmation_at >= 0 else None,
        "note": "Kapalı mumlarla üretilen forward-test adayıdır; işlem emri değildir.",
    }
    from .smc_quality import score, cost_guard
    cost_guard(result)
    result["verification"] = score(result)
    result["sparkline"] = [float(x) for x in c[-32:]]
    from . import smc_rsi
    result["rsi_seed"] = smc_rsi.seed(ltf_rows) if ltf_interval=="15m" else None
    return result


async def analyze_symbol(symbol: str, htf_interval: str = HTF_INTERVAL,
                         ltf_interval: str = LTF_INTERVAL) -> Dict[str, Any]:
    """Fetch both timeframes and derivative context concurrently."""
    from . import analysis  # avoids service import cycles at module load
    htf_rows, ltf_rows, context = await asyncio.gather(
        binance.klines(symbol.upper(), htf_interval, 260),
        binance.klines(symbol.upper(), ltf_interval, 500),
        analysis.market_context(symbol.upper()),
    )
    result = await asyncio.to_thread(
        analyze_rows, symbol, htf_rows, ltf_rows, htf_interval, ltf_interval, context
    )
    closed = _closed_rows(ltf_rows)
    from .demo import INTERVAL_MS
    htf_closed = _closed_rows(htf_rows)
    if (closed and time.time() * 1000 - int(closed[-1][6]) > INTERVAL_MS[ltf_interval] * 2
            or htf_closed and time.time() * 1000 - int(htf_closed[-1][6]) > INTERVAL_MS[htf_interval] * 2):
        result.update(ready=False, phase="VERI_BAYAT")
    return result


_scan_cache = {}
_scan_lock = asyncio.Lock()
scan_progress = {"completed": 0, "total": 0}

async def scan(limit: int = 200, htf_interval: str = HTF_INTERVAL,
               ltf_interval: str = LTF_INTERVAL) -> Dict[str, Any]:
    async with _scan_lock:
        return await _scan_unique(limit, htf_interval, ltf_interval)

async def _scan_unique(limit, htf_interval, ltf_interval):
    from . import smc_market
    from .demo import INTERVAL_MS
    markets, rates = await asyncio.gather(smc_market.universe(min(200, max(1, limit))), smc_market.funding())
    symbols = list(dict.fromkeys(row["symbol"] for row in markets))
    from . import binance_ws
    binance_ws.set_watch(symbols)
    scan_progress.update(completed=0, total=len(symbols))
    bucket = int((time.time() * 1000 - 3000) // INTERVAL_MS[ltf_interval])
    # Keep only this candle generation; repeated callers reuse successful analyses.
    for key in list(_scan_cache):
        if key[1] != bucket:
            del _scan_cache[key]
    semaphore = asyncio.Semaphore(4)
    reused = 0

    async def one(symbol: str) -> Dict[str, Any]:
        nonlocal reused
        key = (symbol, bucket, htf_interval, ltf_interval, smc_market.demo_mode())
        if key in _scan_cache:
            reused += 1
            scan_progress["completed"] += 1
            return _scan_cache[key]
        async with semaphore:
            try:
                htf, ltf = await asyncio.gather(
                    smc_market.candles(symbol, htf_interval, 260),
                    smc_market.candles(symbol, ltf_interval, 500))
                result = await asyncio.to_thread(analyze_rows, symbol, htf, ltf,
                                                htf_interval, ltf_interval,
                                                {"funding": {"rate": rates.get(symbol)}})
                result["demo"] = smc_market.demo_mode()
                if not smc_market.demo_mode() and symbol not in rates:
                    result.update(ready=False, phase="FUNDING_BEKLE")
                closed, higher = _closed_rows(ltf), _closed_rows(htf)
                if (not closed or not higher or
                    closed[-1][6] < bucket * INTERVAL_MS[ltf_interval] - 1 or
                    time.time() * 1000 - higher[-1][6] > INTERVAL_MS[htf_interval] * 2):
                    result.update(ready=False, phase="VERI_BAYAT")
                elif result.get("ok"):
                    _scan_cache[key] = result
                    if not result.get('demo'):
                        from . import smc_rsi
                        smc_rsi.install(symbol,result.get('rsi_seed'))
                return result
            except Exception as exc:  # one symbol must not abort the universe
                return {"ok": False, "symbol": symbol, "error": str(exc)[:160]}
            finally:
                scan_progress["completed"] += 1

    results = await asyncio.gather(*(one(symbol) for symbol in symbols))
    valid = [row for row in results if row.get("ok")]
    candidates = [row for row in valid if row.get("score", 0) >= 55]
    candidates.sort(key=lambda row: (bool(row.get("ready")), row.get("score", 0),
                                     row.get("rr", 0)), reverse=True)
    return {
        "engine": "smc_ict_v5",
        "mode": "paper",
        "live_execution": False,
        "scanned": len(symbols),
        "errors": len(results) - len(valid),
        "candidates": candidates,
        "results": valid,
        "reused": reused,
        "unique_analyzed": len(symbols) - reused,
        "last_scan_at": int(time.time() * 1000),
    }
