"""Production research-only entrypoint; no legacy execution workers."""
from contextlib import asynccontextmanager

import uvicorn
from fastapi.responses import JSONResponse

from app import db
from app.config import settings
from app.main import app
from app.services import binance, binance_ws, premium_engine, terminal_briefing, smc_notifications


@asynccontextmanager
async def lifespan(application):
    db.init()
    await binance.probe()
    await binance.start_recovery()
    await binance_ws.start()
    await premium_engine.start()
    await terminal_briefing.start()
    await smc_notifications.start()
    try:
        yield
    finally:
        await smc_notifications.stop()
        await terminal_briefing.stop()
        await premium_engine.stop()
        await binance_ws.stop()
        await binance.stop_recovery()
        await binance.close()


app.router.lifespan_context = lifespan


@app.middleware("http")
async def research_only(request, call_next):
    # This deployment must not enable the legacy order layer through its API.
    if request.url.path.startswith("/api/trading") and request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse({"detail": "Bu sunucuda gerçek emir yürütme kapalı."}, status_code=403)
    return await call_next(request)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", ws_ping_interval=20)
