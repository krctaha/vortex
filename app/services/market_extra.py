"""Binance disi piyasa verileri: Fear&Greed, dominance, altin, takvim, haber.

Her kaynak icin: birincil + yedek + bayat cache failover.
Dominance icin TEK kaynak sabitlenmistir (CoinPaprika) — kaynak degistirmek
grafikte yapay sicrama uretir; yedege dusuldugunde 'source' alani degisir ve
arayuzde etiketlenir.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from . import binance, demo
from .cache import cache

log = logging.getLogger("vortex.market_extra")

HEADERS = {"User-Agent": settings.USER_AGENT,
           "Accept": "application/json, text/xml, application/rss+xml, */*"}

NEWS_FEEDS = [
    {"name": "Federal Reserve · Para Politikası", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "lang": "en", "important": True},
    {"name": "BLS · Enflasyon", "url": "https://www.bls.gov/feed/cpi.rss", "lang": "en", "important": True},
    {"name": "BLS · İstihdam", "url": "https://www.bls.gov/feed/empsit.rss", "lang": "en", "important": True},
    {"name": "Uzmancoin", "url": "https://uzmancoin.com/feed/", "lang": "tr"},
    {"name": "Coin Mühendisi", "url": "https://coinmuhendisi.com/feed/", "lang": "tr"},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "lang": "en"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "lang": "en"},
    {"name": "Investing TR", "url": "https://tr.investing.com/rss/news_301.rss", "lang": "tr"},
]

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
IMPORTANT_KEYWORDS = (
    "fomc", "federal funds", "rate", "faiz", "cpi", "ppi", "pce", "gdp",
    "non-farm", "nonfarm", "payroll", "unemployment", "powell", "warsh",
    "jackson hole", "fed chair", "interest",
)


async def _get_json(url: str, timeout: float = 12.0, params: Optional[dict] = None) -> Any:
    async with httpx.AsyncClient(timeout=timeout, headers=HEADERS, follow_redirects=True) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def _get_text(url: str, timeout: float = 12.0) -> str:
    async with httpx.AsyncClient(timeout=timeout, headers=HEADERS, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


# --------------------------------------------------------------------------- #
# Fear & Greed  (gunde 1 kez guncellenir - 30 dk cache fazlasiyla yeterli)
# --------------------------------------------------------------------------- #
async def fear_greed() -> Dict[str, Any]:
    if binance.is_demo():
        return demo.fear_greed()

    async def factory():
        try:
            data = await _get_json("https://api.alternative.me/fng/", params={"limit": 8})
            rows = data.get("data", [])
            if not rows:
                raise ValueError("bos yanit")
            cur = rows[0]
            return {
                "value": int(cur["value"]),
                "label": cur.get("value_classification", ""),
                "label_tr": _fng_tr(int(cur["value"])),
                "previous": int(rows[1]["value"]) if len(rows) > 1 else None,
                "history": [{"t": int(r["timestamp"]) * 1000, "v": int(r["value"])}
                            for r in reversed(rows)],
                "updated": int(cur["timestamp"]) * 1000,
                "source": "alternative.me",
                "ok": True,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("fear_greed hata: %s", exc)
            stale = cache.stale("fng")
            return stale or {"ok": False, "error": str(exc)[:120]}

    return await cache.wrap("fng", 1800.0, factory)


def _fng_tr(v: int) -> str:
    if v <= 24:
        return "Aşırı Korku"
    if v <= 44:
        return "Korku"
    if v <= 55:
        return "Nötr"
    if v <= 74:
        return "Açgözlülük"
    return "Aşırı Açgözlülük"


# --------------------------------------------------------------------------- #
# Dominance / toplam market cap
# --------------------------------------------------------------------------- #
async def global_market() -> Dict[str, Any]:
    if binance.is_demo():
        return demo.global_market()

    async def factory():
        # Birincil: CoinPaprika (anahtarsiz, temiz sayisal tipler)
        try:
            g = await _get_json("https://api.coinpaprika.com/v1/global")
            total = float(g["market_cap_usd"])
            btc_d = float(g["bitcoin_dominance_percentage"])
            return {
                "btc_dominance": round(btc_d, 2),
                "eth_dominance": None,
                "total_market_cap": total,
                "total2": round(total * (1 - btc_d / 100.0), 2),
                "volume_24h": float(g.get("volume_24h_usd") or 0),
                "market_cap_change_24h": float(g.get("market_cap_change_24h") or 0),
                "source": "CoinPaprika",
                "ok": True,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("coinpaprika hata: %s", exc)
        # Yedek: CoinLore (ETH dominance de verir)
        try:
            arr = await _get_json("https://api.coinlore.net/api/global/")
            g = arr[0]
            total = float(g["total_mcap"])
            btc_d, eth_d = float(g["btc_d"]), float(g["eth_d"])
            return {
                "btc_dominance": round(btc_d, 2),
                "eth_dominance": round(eth_d, 2),
                "total_market_cap": total,
                "total2": round(total * (1 - btc_d / 100.0), 2),
                "total3": round(total * (1 - (btc_d + eth_d) / 100.0), 2),
                "volume_24h": float(g.get("total_volume") or 0),
                "market_cap_change_24h": float(g.get("mcap_change") or 0),
                "source": "CoinLore (yedek)",
                "ok": True,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("coinlore hata: %s", exc)
        stale = cache.stale("global")
        return stale or {"ok": False, "error": "dominance kaynaklarina ulasilamadi"}

    return await cache.wrap("global", 300.0, factory)


# --------------------------------------------------------------------------- #
# Altin (XAU spot) — Binance XAUUSDT'nin capraz kontrolu
# --------------------------------------------------------------------------- #
async def gold_spot() -> Dict[str, Any]:
    if binance.is_demo():
        return demo.gold_spot()

    async def factory():
        try:
            d = await _get_json("https://api.gold-api.com/price/XAU")
            return {"price": float(d["price"]), "updated": d.get("updatedAt"),
                    "source": "gold-api.com", "ok": True}
        except Exception as exc:  # noqa: BLE001
            return cache.stale("gold") or {"ok": False, "error": str(exc)[:120]}

    return await cache.wrap("gold", 120.0, factory)


# --------------------------------------------------------------------------- #
# Ekonomik takvim
# --------------------------------------------------------------------------- #
async def economic_calendar(only_important: bool = False) -> Dict[str, Any]:
    if binance.is_demo():
        data = demo.economic_calendar()
        if only_important:
            data = {**data, "events": [e for e in data["events"] if e["impact"] == "High"]}
        return data

    async def factory():
        try:
            rows = await _get_json(CALENDAR_URL, timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("takvim hata: %s", exc)
            return cache.stale("calendar") or {"ok": False, "error": str(exc)[:140], "events": []}
        events = []
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["date"]).astimezone(timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            title = r.get("title", "")
            impact = r.get("impact", "")
            critical = (impact == "High"
                        and any(k in title.lower() for k in IMPORTANT_KEYWORDS))
            events.append({
                "title": title,
                "currency": r.get("country", ""),
                "impact": impact,
                "impact_tr": {"High": "Yüksek", "Medium": "Orta",
                              "Low": "Düşük", "Holiday": "Tatil"}.get(impact, impact),
                "time": int(dt.timestamp() * 1000),
                "forecast": r.get("forecast") or None,
                "previous": r.get("previous") or None,
                "critical": critical,
            })
        events.sort(key=lambda e: e["time"])
        return {"ok": True, "events": events, "source": "ForexFactory (faireconomy)",
                "fetched": int(time.time() * 1000)}

    data = await cache.wrap("calendar", 1800.0, factory)
    if only_important and data.get("ok"):
        data = dict(data)
        data["events"] = [e for e in data["events"] if e["impact"] == "High"]
    return data


# --------------------------------------------------------------------------- #
# Haberler
# --------------------------------------------------------------------------- #
def _parse_feed(text: str, source: str, lang: str) -> List[dict]:
    import feedparser
    parsed = feedparser.parse(text)
    out = []
    for e in parsed.entries[:25]:
        ts = None
        for key in ("published_parsed", "updated_parsed"):
            if getattr(e, key, None):
                from calendar import timegm
                ts = int(timegm(getattr(e, key)) * 1000)
                break
        out.append({
            "important": any(k in (getattr(e,"title","") or "").lower() for k in IMPORTANT_KEYWORDS),
            "importance_basis": "Başlıktaki ekonomik anahtar kelimeler; editoryal teyit değil",
            "title": (getattr(e, "title", "") or "").strip(),
            "link": getattr(e, "link", ""),
            "guid": getattr(e, "id", None) or getattr(e, "link", ""),
            "published": ts,
            "source": source,
            "lang": lang,
        })
    return out


async def news(limit: int = 30, max_age_hours: int = 72) -> Dict[str, Any]:
    if binance.is_demo():
        return demo.news(limit)

    async def factory():
        async def one(feed):
            try:
                text = await _get_text(feed["url"], timeout=14.0)
                parsed = _parse_feed(text, feed["name"], feed["lang"])
                if feed.get("important"):
                    for row in parsed:
                        row.update(important=True,importance_basis="Resmî ekonomik veri / para politikası yayını")
                return parsed, None
            except Exception as exc:  # noqa: BLE001
                return [], f'{feed["name"]}: {type(exc).__name__}'

        results = await asyncio.gather(*(one(f) for f in NEWS_FEEDS))
        items, errors = [], []
        for got, err in results:
            items.extend(got)
            if err:
                errors.append(err)
        seen, deduped = set(), []
        cutoff = (time.time() - max_age_hours * 3600) * 1000
        for it in items:
            if not it["title"] or it["guid"] in seen:
                continue
            if it["published"] and it["published"] < cutoff:
                continue
            seen.add(it["guid"])
            deduped.append(it)
        deduped.sort(key=lambda x: x["published"] or 0, reverse=True)
        payload = {"ok": bool(deduped), "items": deduped[:60], "errors": errors,
                   "fetched": int(time.time() * 1000)}
        if not deduped:
            return cache.stale("news") or payload
        return payload

    data = await cache.wrap("news", 60.0, factory)
    return {**data, "items": data.get("items", [])[:limit]}
