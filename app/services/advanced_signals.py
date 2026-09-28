from __future__ import annotations

from typing import Any, Dict, List


class AdvancedSignalEngine:
    def __init__(self):
        self.name = "advanced_signals"

    def evaluate(self, market: Dict[str, Any]) -> Dict[str, Any]:
        price = float(market.get("price", 0.0) or 0.0)
        momentum = float(market.get("momentum", 0.0) or 0.0)
        trend = float(market.get("trend", 0.0) or 0.0)
        volume = float(market.get("volume", 0.0) or 0.0)

        score = (momentum * 0.5) + (trend * 0.3) + (min(volume, 1000) / 1000.0) * 0.2
        direction = "BUY" if score >= 0.5 else "SELL" if score <= -0.5 else "HOLD"

        return {
            "symbol": market.get("symbol", "UNKNOWN"),
            "price": price,
            "score": round(score, 4),
            "direction": direction,
            "confidence": min(max(abs(score) * 100, 0), 100),
        }

    def batch(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.evaluate(item) for item in markets]
