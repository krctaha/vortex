"""Piyasa yapisi: swing pivotlar, BOS/CHoCH, liquidity sweep, FVG/IFVG,
order block / breaker, CRT, RSI divergence.

TASARIM KURALI - REPAINT YOK:
  Bir swing pivot ancak sagindaki `right` bar kapandiktan sonra kesinlesir.
  Her nesnenin `confirmed_at` alani, o bilginin ilk kez kullanilabildigi bar
  indeksidir. Backtest'te sinyali `confirmed_at`ten ONCE kullanmak look-ahead'dir.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import List, Optional

import numpy as np

Array = np.ndarray


# --------------------------------------------------------------------------- #
# Swing pivotlar
# --------------------------------------------------------------------------- #
@dataclass
class Pivot:
    index: int
    price: float
    kind: str            # "high" | "low"
    confirmed_at: int    # bu pivotun kesinlestigi bar

    def dict(self):
        return asdict(self)


def find_pivots(high: Array, low: Array, left: int = 3, right: int = 3) -> List[Pivot]:
    """Fractal pivot. Bar i, solundaki `left` ve sagindaki `right` bardan
    kesin olarak daha yuksek/alcaksa pivottur."""
    pivots: List[Pivot] = []
    n = len(high)
    for i in range(left, n - right):
        win_h = high[i - left: i + right + 1]
        win_l = low[i - left: i + right + 1]
        if high[i] == float(np.max(win_h)) and np.sum(win_h == high[i]) == 1:
            pivots.append(Pivot(i, float(high[i]), "high", i + right))
        elif low[i] == float(np.min(win_l)) and np.sum(win_l == low[i]) == 1:
            pivots.append(Pivot(i, float(low[i]), "low", i + right))
    return pivots


# --------------------------------------------------------------------------- #
# BOS / CHoCH
# --------------------------------------------------------------------------- #
@dataclass
class StructureEvent:
    index: int
    price: float
    kind: str        # "BOS" | "CHoCH"
    direction: str   # "bull" | "bear"
    broken_pivot: int

    def dict(self):
        return asdict(self)


def structure_events(high: Array, low: Array, close: Array,
                     left: int = 3, right: int = 3) -> List[StructureEvent]:
    """Kapanis bazli kirilim. Ayni yonde devam = BOS, yon degistirdi = CHoCH."""
    pivots = find_pivots(high, low, left, right)
    events: List[StructureEvent] = []
    bias: Optional[str] = None
    last_high: Optional[Pivot] = None
    last_low: Optional[Pivot] = None
    pi = 0
    n = len(close)
    for i in range(n):
        while pi < len(pivots) and pivots[pi].confirmed_at <= i:
            p = pivots[pi]
            if p.kind == "high":
                last_high = p
            else:
                last_low = p
            pi += 1
        if last_high is not None and close[i] > last_high.price:
            kind = "BOS" if bias == "bull" else "CHoCH"
            events.append(StructureEvent(i, float(close[i]), kind, "bull", last_high.index))
            bias = "bull"
            last_high = None
        elif last_low is not None and close[i] < last_low.price:
            kind = "BOS" if bias == "bear" else "CHoCH"
            events.append(StructureEvent(i, float(close[i]), kind, "bear", last_low.index))
            bias = "bear"
            last_low = None
    return events


def market_bias(high: Array, low: Array, close: Array, left: int = 3, right: int = 3) -> str:
    ev = structure_events(high, low, close, left, right)
    return ev[-1].direction if ev else "neutral"


# --------------------------------------------------------------------------- #
# Liquidity sweep / stop hunt  (== "Turtle Soup" / TBS ailesinin cekirdegi)
# --------------------------------------------------------------------------- #
@dataclass
class Sweep:
    index: int
    level: float
    direction: str    # "bull" (dip supuruldu) | "bear" (tepe supuruldu)
    wick_ratio: float
    swept_pivot: int
    closed_back: bool

    def dict(self):
        return asdict(self)


def liquidity_sweeps(high: Array, low: Array, close: Array, open_: Array,
                     left: int = 3, right: int = 3,
                     lookback: int = 60,
                     min_wick_ratio: float = 0.5,
                     tolerance: float = 0.0005) -> List[Sweep]:
    """Bir pivot seviyesinin fitille asilip GOVDENIN geri kapanmasi.

    Kosullar:
      1. high[i] > pivot_high  (veya low[i] < pivot_low) -> likidite alindi
      2. close[i] < pivot_high (veya > pivot_low)        -> geri kapandi
      3. fitil / toplam bar boyu >= min_wick_ratio       -> reddedilme var
    tolerance: esit tepe/dip toleransi (fiyata oranla).
    """
    n = len(close)
    pivots = find_pivots(high, low, left, right)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    out: List[Sweep] = []
    for i in range(n):
        bar_range = float(high[i] - low[i])
        if bar_range <= 0:
            continue
        # tepe supurme (bearish sweep)
        for p in reversed(highs):
            if p.confirmed_at >= i or i - p.index > lookback:
                if i - p.index > lookback:
                    break
                continue
            lvl = p.price * (1.0 + tolerance)
            if high[i] > lvl:
                upper_wick = float(high[i] - max(open_[i], close[i]))
                ratio = upper_wick / bar_range
                if ratio >= min_wick_ratio:
                    out.append(Sweep(i, p.price, "bear", round(ratio, 3), p.index,
                                     bool(close[i] < p.price)))
                break
        # dip supurme (bullish sweep)
        for p in reversed(lows):
            if p.confirmed_at >= i or i - p.index > lookback:
                if i - p.index > lookback:
                    break
                continue
            lvl = p.price * (1.0 - tolerance)
            if low[i] < lvl:
                lower_wick = float(min(open_[i], close[i]) - low[i])
                ratio = lower_wick / bar_range
                if ratio >= min_wick_ratio:
                    out.append(Sweep(i, p.price, "bull", round(ratio, 3), p.index,
                                     bool(close[i] > p.price)))
                break
    return out


def equal_levels(high: Array, low: Array, left: int = 3, right: int = 3,
                 tolerance: float = 0.0008) -> dict:
    """Esit tepe/dipler (EQH/EQL) - likiditenin biriktigi yerler."""
    pivots = find_pivots(high, low, left, right)
    eqh, eql = [], []
    for kind, bucket in (("high", eqh), ("low", eql)):
        pts = [p for p in pivots if p.kind == kind]
        used = set()
        for a in range(len(pts)):
            if a in used:
                continue
            group = [pts[a]]
            for b in range(a + 1, len(pts)):
                if abs(pts[b].price - pts[a].price) / pts[a].price <= tolerance:
                    group.append(pts[b])
                    used.add(b)
            if len(group) >= 2:
                bucket.append({
                    "price": round(float(np.mean([g.price for g in group])), 8),
                    "touches": len(group),
                    "indexes": [g.index for g in group],
                })
    return {"eqh": eqh, "eql": eql}


# --------------------------------------------------------------------------- #
# FVG / IFVG
# --------------------------------------------------------------------------- #
@dataclass
class FVG:
    index: int          # ortadaki bar
    top: float
    bottom: float
    direction: str      # "bull" | "bear"
    filled_at: Optional[int] = None
    inversed_at: Optional[int] = None   # IFVG'ye donustugu bar

    def dict(self):
        return asdict(self)


def fair_value_gaps(high: Array, low: Array, close: Array,
                    min_size_pct: float = 0.0005,
                    track_inversion: bool = True) -> List[FVG]:
    """3 mum kurali:
       bullish FVG : low[i+1] > high[i-1]  -> bosluk [high[i-1], low[i+1]]
       bearish FVG : high[i+1] < low[i-1]  -> bosluk [high[i+1], low[i-1]]
    IFVG: bir bullish FVG'nin GOVDE ile asagi kirilmasi -> direnc olur (bear IFVG).
    """
    n = len(close)
    gaps: List[FVG] = []
    for i in range(1, n - 1):
        if low[i + 1] > high[i - 1]:
            size = float(low[i + 1] - high[i - 1])
            if size / max(float(close[i]), 1e-12) >= min_size_pct:
                gaps.append(FVG(i, float(low[i + 1]), float(high[i - 1]), "bull"))
        elif high[i + 1] < low[i - 1]:
            size = float(low[i - 1] - high[i + 1])
            if size / max(float(close[i]), 1e-12) >= min_size_pct:
                gaps.append(FVG(i, float(low[i - 1]), float(high[i + 1]), "bear"))
    if not track_inversion:
        return gaps
    for g in gaps:
        start = g.index + 2
        for j in range(start, n):
            if g.direction == "bull":
                if g.filled_at is None and low[j] <= g.top:
                    g.filled_at = j
                if close[j] < g.bottom:
                    g.inversed_at = j
                    break
            else:
                if g.filled_at is None and high[j] >= g.bottom:
                    g.filled_at = j
                if close[j] > g.top:
                    g.inversed_at = j
                    break
    return gaps


def active_fvgs(gaps: List[FVG], upto: Optional[int] = None) -> List[FVG]:
    """Henuz kapanmamis (mitige edilmemis) bosluklar."""
    return [g for g in gaps if g.inversed_at is None and g.filled_at is None
            and (upto is None or g.index <= upto)]


def inversed_fvgs(gaps: List[FVG]) -> List[FVG]:
    return [g for g in gaps if g.inversed_at is not None]


# --------------------------------------------------------------------------- #
# Order Block / Breaker
# --------------------------------------------------------------------------- #
@dataclass
class OrderBlock:
    index: int
    top: float
    bottom: float
    direction: str        # "bull" | "bear"
    is_breaker: bool = False
    mitigated_at: Optional[int] = None
    confirmed_at: Optional[int] = None

    def dict(self):
        return asdict(self)


def order_blocks(open_: Array, high: Array, low: Array, close: Array,
                 left: int = 3, right: int = 3, max_blocks: int = 12) -> List[OrderBlock]:
    """Bir yapi kirilimindan (BOS/CHoCH) once gelen SON ters renkli mum.
    Kirilan blok mitigate olup ters yonde calisirsa 'breaker' olur."""
    n = len(close)
    events = structure_events(high, low, close, left, right)
    blocks: List[OrderBlock] = []
    for ev in events:
        i = ev.index
        want_bear_candle = ev.direction == "bull"
        for j in range(i - 1, max(i - 30, -1), -1):
            is_bear = close[j] < open_[j]
            if is_bear == want_bear_candle:
                blocks.append(OrderBlock(
                    index=j,
                    top=float(max(open_[j], close[j])) if not want_bear_candle else float(high[j]),
                    bottom=float(low[j]) if want_bear_candle else float(min(open_[j], close[j])),
                    direction=ev.direction,
                    confirmed_at=i,
                ))
                break
    for b in blocks:
        for j in range((b.confirmed_at if b.confirmed_at is not None else b.index) + 1, n):
            if b.direction == "bull" and low[j] <= b.bottom:
                b.mitigated_at = j
                break
            if b.direction == "bear" and high[j] >= b.top:
                b.mitigated_at = j
                break
        if b.mitigated_at is not None:
            j = b.mitigated_at
            if b.direction == "bull" and close[j] < b.bottom:
                b.is_breaker = True
            elif b.direction == "bear" and close[j] > b.top:
                b.is_breaker = True
    return blocks[-max_blocks:]


# --------------------------------------------------------------------------- #
# CRT - Candle Range Theory (3 mum: Accumulation / Manipulation / Distribution)
# --------------------------------------------------------------------------- #
@dataclass
class CRT:
    index: int            # dagitim (3.) mumunun indeksi
    range_high: float
    range_low: float
    direction: str        # "bull" | "bear"
    sweep_side: str       # supurulen taraf

    def dict(self):
        return asdict(self)


def crt_setups(open_: Array, high: Array, low: Array, close: Array,
               min_range_pct: float = 0.001) -> List[CRT]:
    """C1 = range mumu.
       C2 = C1'in bir tarafini fitille supurup C1 araligina GERI KAPANAN mum.
       C3 = C2'nin yonunu teyit eden mum -> setup burada gecerlidir.
    Not: bu klasik false-breakout orgusunun yeniden adlandirilmasidir;
    olculmus bagimsiz bir edge kaniti yoktur, baglam olarak kullanilmalidir."""
    n = len(close)
    out: List[CRT] = []
    for i in range(2, n):
        c1h, c1l = float(high[i - 2]), float(low[i - 2])
        rng = c1h - c1l
        if rng <= 0 or rng / max(float(close[i - 2]), 1e-12) < min_range_pct:
            continue
        c2h, c2l, c2c = float(high[i - 1]), float(low[i - 1]), float(close[i - 1])
        # bullish CRT: C2 C1'in dibini supurdu ama iceri kapandi, C3 yukari
        if c2l < c1l and c1l <= c2c <= c1h and close[i] > close[i - 1] and close[i] > c1l:
            out.append(CRT(i, c1h, c1l, "bull", "low"))
        # bearish CRT
        elif c2h > c1h and c1l <= c2c <= c1h and close[i] < close[i - 1] and close[i] < c1h:
            out.append(CRT(i, c1h, c1l, "bear", "high"))
    return out


# --------------------------------------------------------------------------- #
# Premium / Discount + OTE
# --------------------------------------------------------------------------- #
def premium_discount(high: Array, low: Array, lookback: int = 60) -> dict:
    seg_h = float(np.max(high[-lookback:]))
    seg_l = float(np.min(low[-lookback:]))
    rng = seg_h - seg_l
    if rng <= 0:
        return {}
    return {
        "range_high": seg_h,
        "range_low": seg_l,
        "equilibrium": seg_l + rng * 0.5,
        "premium_start": seg_l + rng * 0.5,
        "discount_end": seg_l + rng * 0.5,
        "ote_bull_start": seg_l + rng * 0.205,   # 0.79 retracement
        "ote_bull_end": seg_l + rng * 0.382,
        "ote_bear_start": seg_l + rng * 0.618,
        "ote_bear_end": seg_l + rng * 0.795,
    }


# --------------------------------------------------------------------------- #
# RSI divergence  (repaint'siz: sadece KESINLESMIS pivotlar)
# --------------------------------------------------------------------------- #
@dataclass
class Divergence:
    index: int
    kind: str        # "regular" | "hidden"
    direction: str   # "bull" | "bear"
    price_a: float
    price_b: float
    rsi_a: float
    rsi_b: float
    pivot_a: int
    pivot_b: int

    def dict(self):
        return asdict(self)


def rsi_divergences(high: Array, low: Array, rsi_values: Array,
                    left: int = 5, right: int = 5,
                    max_gap: int = 60, min_gap: int = 5) -> List[Divergence]:
    """SADECE ardisik iki kesinlesmis pivot karsilastirilir (cherry-pick yok).
    regular bear : fiyat HH, RSI LH
    regular bull : fiyat LL, RSI HL
    hidden  bear : fiyat LH, RSI HH
    hidden  bull : fiyat HL, RSI LL"""
    pivots = find_pivots(high, low, left, right)
    out: List[Divergence] = []
    for kind_filter, arr in (("high", high), ("low", low)):
        pts = [p for p in pivots if p.kind == kind_filter]
        for a, b in zip(pts, pts[1:]):
            gap = b.index - a.index
            if gap < min_gap or gap > max_gap:
                continue
            ra, rb = float(rsi_values[a.index]), float(rsi_values[b.index])
            if np.isnan(ra) or np.isnan(rb):
                continue
            pa, pb = a.price, b.price
            if kind_filter == "high":
                if pb > pa and rb < ra:
                    out.append(Divergence(b.confirmed_at, "regular", "bear", pa, pb, ra, rb, a.index, b.index))
                elif pb < pa and rb > ra:
                    out.append(Divergence(b.confirmed_at, "hidden", "bear", pa, pb, ra, rb, a.index, b.index))
            else:
                if pb < pa and rb > ra:
                    out.append(Divergence(b.confirmed_at, "regular", "bull", pa, pb, ra, rb, a.index, b.index))
                elif pb > pa and rb < ra:
                    out.append(Divergence(b.confirmed_at, "hidden", "bull", pa, pb, ra, rb, a.index, b.index))
    out.sort(key=lambda d: d.index)
    return out
