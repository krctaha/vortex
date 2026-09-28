"""Shared RSS/calendar fetch and server push. Not a low-latency licensed newswire."""
import asyncio
import time
from . import market_extra

snapshot = {"items":[],"events":[],"source_mode":"RSS / takvim", "news_poll_seconds":60,"calendar_poll_seconds":1800}
subscribers = set()
_task = None

async def _loop():
    while True:
        try:
            news, calendar = await asyncio.gather(market_extra.news(40),market_extra.economic_calendar())
            snapshot.update(items=news.get("items",[]),events=calendar.get("events",[]),
                news_fetched=news.get("fetched"),calendar_fetched=calendar.get("fetched"),
                news_ok=bool(news.get("ok")),calendar_ok=bool(calendar.get("ok")),
                published_at=int(time.time()*1000))
            for queue in list(subscribers):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(dict(snapshot))
        except asyncio.CancelledError:
            raise
        except Exception:
            snapshot["news_ok"] = False
        await asyncio.sleep(60)

async def start():
    global _task
    if not _task or _task.done():
        _task = asyncio.create_task(_loop())

async def stop():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
