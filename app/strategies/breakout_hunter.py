"""15 dakikalık hacimli kırılım + geri-alım (reclaim) avcısı.

Bu çekirdek ilk roket mumunu kovalamaz. Önce hacimli kırılımı bulur, ardından
fiyatın kırılan seviyeyi test edip yeniden üstüne/altına kapanmasını bekler.
Amaç sonradan güzel görünen her dik mumu işlem sanmak değil; ölçülebilir ve
stop konulabilir ikinci girişleri araştırma katmanına taşımaktır.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

import numpy as np


DEFAULTS = {
    "breakout_lookback": 40,
    "setup_window_bars": 8,
    "breakout_buffer_pct": 0.30,
    "min_breakout_move_pct": 4.0,
    "min_breakout_rel_volume": 3.0,
    "min_reclaim_rel_volume": 1.5,
    "max_stop_pct": 18.0,
    "stop_atr_mult": 0.25,
    "target_r": 2.0,
}


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(values, dtype=float)
    alpha = 2.0 / (period + 1.0)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _atr(rows: Sequence[Sequence], period: int = 14) -> float:
    h = np.asarray([float(x[2]) for x in rows], dtype=float)
    l = np.asarray([float(x[3]) for x in rows], dtype=float)
    c = np.asarray([float(x[4]) for x in rows], dtype=float)
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return float(np.mean(tr[-period:])) if len(tr) >= period else 0.0


def _rel_volume(volumes: np.ndarray, index: int, period: int = 20) -> float:
    start = max(0, index - period)
    base = volumes[start:index]
    med = float(np.median(base)) if len(base) else 0.0
    return float(volumes[index] / med) if med > 0 else 0.0


def evaluate(symbol: str, rows: Sequence[Sequence],
             cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = {**DEFAULTS, **(cfg or {})}
    need = int(c["breakout_lookback"]) + int(c["setup_window_bars"]) + 6
    if len(rows) < need:
        return {"ok": False, "symbol": symbol,
                "veto": [f"yetersiz 15m geçmiş ({len(rows)} < {need})"]}

    o = np.asarray([float(x[1]) for x in rows], dtype=float)
    h = np.asarray([float(x[2]) for x in rows], dtype=float)
    l = np.asarray([float(x[3]) for x in rows], dtype=float)
    close = np.asarray([float(x[4]) for x in rows], dtype=float)
    volume = np.asarray([float(x[5]) for x in rows], dtype=float)
    n = len(rows)
    now_i = n - 1
    lookback = int(c["breakout_lookback"])
    window = int(c["setup_window_bars"])
    buffer = float(c["breakout_buffer_pct"]) / 100.0
    atr = _atr(rows)
    if atr <= 0 or close[-1] <= 0:
        return {"ok": False, "symbol": symbol, "veto": ["15m ATR hesaplanamadı"]}

    ema9 = _ema(close, 9)
    ema25 = _ema(close, 25)
    candidates = []
    # Mevcut mum giriş teyididir; kırılım daha önce oluşmuş olmalı.
    for j in range(max(lookback, n - window - 1), n - 1):
        prior_hi = float(np.max(h[j - lookback:j]))
        prior_lo = float(np.min(l[j - lookback:j]))
        rel = _rel_volume(volume, j)
        anchor = close[max(0, j - 4)]
        move = (close[j] / anchor - 1.0) * 100.0 if anchor > 0 else 0.0
        if (close[j] > prior_hi * (1.0 + buffer)
                and move >= float(c["min_breakout_move_pct"])
                and rel >= float(c["min_breakout_rel_volume"])):
            candidates.append((j, "LONG", prior_hi, rel, move))
        if (close[j] < prior_lo * (1.0 - buffer)
                and move <= -float(c["min_breakout_move_pct"])
                and rel >= float(c["min_breakout_rel_volume"])):
            candidates.append((j, "SHORT", prior_lo, rel, move))

    if not candidates:
        return {"ok": False, "symbol": symbol, "veto": ["hacimli kırılım adayı yok"]}

    # İlk kırılan ana seviye tercih edilir; sonraki roket mumları yeni kurulum
    # sayılmaz. Böylece AKE benzeri hareketlerde tepe mumu kovalanmaz.
    for j, side, level, breakout_rel, move in candidates:
        after_low = float(np.min(l[j + 1:now_i + 1]))
        after_high = float(np.max(h[j + 1:now_i + 1]))
        reclaim_rel = _rel_volume(volume, now_i)
        if side == "LONG":
            tested = after_low <= level * (1.0 + buffer)
            reclaimed = close[-1] > level * (1.0 + buffer) and close[-1] > o[-1]
            trend_ok = ema9[-1] > ema25[-1]
            if not (tested and reclaimed and trend_ok
                    and reclaim_rel >= float(c["min_reclaim_rel_volume"])):
                continue
            # Onceki panik fitilinin dibini stop yapmak riski gereksiz yere
            # sisirir. Giris tezi "bu teyit mumu seviyeyi geri aldi" oldugu
            # icin gecersizlik noktasi teyit mumunun dibi + kirilan seviyedir.
            # Gecmisteki sweep yalnizca test kosuludur; stop mesafesi degildir.
            signal_low = float(l[now_i])
            stop = min(level - atr * float(c["stop_atr_mult"]),
                       signal_low - atr * .10)
            direction = 1
        else:
            tested = after_high >= level * (1.0 - buffer)
            reclaimed = close[-1] < level * (1.0 - buffer) and close[-1] < o[-1]
            trend_ok = ema9[-1] < ema25[-1]
            if not (tested and reclaimed and trend_ok
                    and reclaim_rel >= float(c["min_reclaim_rel_volume"])):
                continue
            signal_high = float(h[now_i])
            stop = max(level + atr * float(c["stop_atr_mult"]),
                       signal_high + atr * .10)
            direction = -1

        entry = float(close[-1])
        risk = abs(entry - stop)
        stop_pct = risk / entry * 100.0
        if risk <= 0 or stop_pct > float(c["max_stop_pct"]):
            return {"ok": False, "symbol": symbol,
                    "veto": [f"reclaim stopu çok geniş (%{stop_pct:.1f})"],
                    "phase": "reclaim", "side": side}
        target = entry + direction * risk * float(c["target_r"])
        score = min(9.9, 1.0 + breakout_rel / 3.0 + reclaim_rel / 2.0 + abs(move) / 8.0)
        return {
            "ok": True, "symbol": symbol, "side": side, "phase": "reclaim",
            "entry": round(entry, 8), "stop": round(float(stop), 8),
            "target": round(float(target), 8), "risk_distance": round(risk, 8),
            "stop_pct": round(stop_pct, 2), "target_r": float(c["target_r"]),
            "score": round(score, 3), "breakout_level": round(level, 8),
            "breakout_rel_volume": round(breakout_rel, 2),
            "reclaim_rel_volume": round(reclaim_rel, 2),
            "breakout_move_pct": round(move, 2), "atr_15m": round(atr, 8),
            "setup_bar_time": int(rows[j][0]), "signal_bar_time": int(rows[-1][0]),
            "veto": [],
        }

    return {"ok": False, "symbol": symbol,
            "phase": "breakout_watch", "veto": ["kırılım var; geri-alım teyidi bekleniyor"]}
