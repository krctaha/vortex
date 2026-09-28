"""Kullanicinin belirledigi araliklarla Telegram ozetleri gonderir."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
from html import escape
from typing import Any, Dict, List, Optional

from .. import db, runtime
from ..indicators.core import rsi
from . import analysis, binance, market_extra, telegram

log = logging.getLogger("vortex.notifications")

POLL_SECONDS = 30.0   # dongu araligi — test edilebilsin diye sabit
_task: Optional[asyncio.Task] = None


def _admin() -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1")


def _due(key: str, period_ms: int, now: int) -> bool:
    last = db.get_setting(key)
    if not isinstance(last, (int, float)):
        db.set_setting(key, now)
        return False
    return period_ms > 0 and now - int(last) >= period_ms


async def _send(admin: Dict[str, Any], text: str) -> bool:
    result = await telegram.send(text, token=admin["telegram_token"],
                                 chat_id=admin["telegram_chat_id"], silent=True)
    if not result.get("ok"):
        log.warning("Telegram zamanlayici: %s", result.get("error"))
    return bool(result.get("ok"))


async def _news(admin: Dict[str, Any]) -> bool:
    data = await market_extra.news(limit=8)
    items = data.get("items") or []
    if not items:
        return False
    lines = ["<b>VORTEX · GÜNDEM ÖZETİ</b>", ""]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. <a href=\"{escape(item.get('link', ''), quote=True)}\">"
                     f"{escape(item.get('title', ''), quote=False)}</a> "
                     f"<i>({escape(item.get('source', ''), quote=False)})</i>")
    return await _send(admin, "\n".join(lines))


async def _rsi_report(admin: Dict[str, Any], interval: str, universe_size: int) -> bool:
    tickers, contracts = await asyncio.gather(binance.ticker_24h(), binance.perpetual_symbols())
    allowed = {x["symbol"] for x in contracts}
    liquid = [x for x in tickers if x.get("symbol") in allowed and
              float(x.get("quoteVolume", 0) or 0) >= 5_000_000]
    liquid.sort(key=lambda x: float(x.get("quoteVolume", 0) or 0), reverse=True)
    sem = asyncio.Semaphore(6)

    async def one(symbol: str):
        async with sem:
            try:
                parsed = binance.parse_klines(await binance.klines(symbol, interval, 120))
                value = float(rsi(parsed["close"], 14)[-1])
                return None if value != value else {"symbol": symbol, "rsi": round(value, 2)}
            except Exception:  # noqa: BLE001
                return None

    rows = [x for x in await asyncio.gather(*(one(x["symbol"]) for x in liquid[:universe_size])) if x]
    if not rows:
        return False
    rows.sort(key=lambda x: x["rsi"])
    values = [x["rsi"] for x in rows]
    low, high = rows[:5], rows[-5:][::-1]
    lines = [
        "<b>VORTEX · RSI RADAR</b>",
        f"<code>{escape(interval)}</code> · {len(rows)} likit perpetual kontrat", 
        f"Ortalama <b>{sum(values) / len(values):.2f}</b> · Medyan <b>{statistics.median(values):.2f}</b>",
        f"Aşırı satım ≤30: <b>{sum(1 for x in values if x <= 30)}</b> · Aşırı alım ≥70: <b>{sum(1 for x in values if x >= 70)}</b>", "",
        "<b>En düşükler</b> · " + " · ".join(f"{escape(x['symbol'])} {x['rsi']:.1f}" for x in low),
        "<b>En yüksekler</b> · " + " · ".join(f"{escape(x['symbol'])} {x['rsi']:.1f}" for x in high),
    ]
    return await _send(admin, "\n".join(lines))


async def _analysis_reports(admin: Dict[str, Any], symbols: List[str], interval: str) -> bool:
    sent = False
    for symbol in symbols:
        try:
            snap = await analysis.snapshot(symbol, interval, 400, include_series=False)
            if not snap.get("ok"):
                continue
            snap["context"] = await analysis.market_context(symbol)
            sent = await _send(admin, analysis.telegram_report(snap)) or sent
            await asyncio.sleep(.4)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s otomatik analiz: %s", symbol, exc)
    return sent


async def run_due() -> None:
    admin = _admin()
    if not admin or not telegram.configured(admin.get("telegram_token", ""), admin.get("telegram_chat_id", "")):
        return
    cfg = runtime.get(); ecfg, ncfg = cfg["engine"], cfg["notifications"]
    now = db.now_ms()
    jobs = [
        ("notify_last_news", ncfg["news_minutes"] * 60_000, lambda: _news(admin)),
        ("notify_last_rsi", ncfg["rsi_minutes"] * 60_000,
         lambda: _rsi_report(admin, ecfg["signal_interval"], ecfg["universe_size"])),
        ("notify_last_analysis", ncfg["analysis_hours"] * 3_600_000,
         lambda: _analysis_reports(admin, ncfg["analysis_symbols"].split(","), ecfg["signal_interval"])),
    ]
    for key, period, factory in jobs:
        if not _due(key, period, now):
            continue
        try:
            if await factory():
                db.set_setting(key, db.now_ms())
        except Exception as exc:  # noqa: BLE001
            log.exception("%s calismadi: %s", key, exc)


async def _loop() -> None:
    # 29.08 DUZELTMESI — SESSIZ KALICI OLUM
    # Onceden govde korumasizdi. run_due() icindeki try yalnizca is govdesini
    # sariyor; ondan once calisan _admin() (DB sorgusu), runtime.get() ve _due()
    # korumasizdi. WAL + 4 eszamanli yazici dongu varken "database is locked"
    # gercekci bir ihtimal — ve o hata disari sizarsa gorev KALICI olarak
    # oluyordu. Log'a hicbir sey dusmuyordu; bildirimler bir gun gelmeyi
    # birakiyor, sebebi gorunmuyordu.
    while True:
        try:
            await run_due()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("bildirim dongusu hatasi (dongu devam ediyor): %s", exc)
        await asyncio.sleep(POLL_SECONDS)


async def start() -> None:
    global _task
    if not _task or _task.done():
        _task = asyncio.create_task(_loop(), name="vortex-notification-scheduler")


async def stop() -> None:
    global _task
    if not _task:
        return
    _task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _task
    _task = None
