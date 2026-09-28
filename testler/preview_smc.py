"""Isolated localhost UI QA; no live credentials, schedulers or production DB."""
import os
import sys
import tempfile
from contextlib import nullcontext, asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["VORTEX_DATA_MODE"] = "live" if os.environ.get("VORTEX_LOCAL_ACCOUNT_MODE") == "1" else "demo"
from app.config import settings
from app import db, security
import uvicorn

if __name__ == "__main__":
    persistent = os.environ.get("VORTEX_LOCAL_ACCOUNT_MODE") == "1"
    context = nullcontext(None) if persistent else tempfile.TemporaryDirectory(prefix="vortex-ui-")
    with context as temp:
        settings.db_path = (Path(__file__).resolve().parents[1] / "data" / "local-demo.db"
                            if persistent else Path(temp) / "qa.db")
        preview_port = int(os.environ.get("VORTEX_PREVIEW_PORT", "18765"))
        settings.public_url = f"http://127.0.0.1:{preview_port}"
        db.init()
        if not persistent:
            uid = db.execute(
                "INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("preview", "Yerel Önizleme", security.hash_password("preview-local-only"), "admin", db.now_ms()))
        from app.main import app
        from fastapi.responses import RedirectResponse
        from app.services import binance, premium_engine, binance_ws, terminal_briefing

        @asynccontextmanager
        async def local_lifespan(app):
            # Only public data + research scanner. Never start execution workers.
            await binance.probe()
            await binance_ws.start()
            await premium_engine.start()
            await terminal_briefing.start()
            try:
                yield
            finally:
                await terminal_briefing.stop()
                await premium_engine.stop()
                await binance_ws.stop()
                await binance.close()

        app.router.lifespan_context = local_lifespan

        if not persistent:
            @app.get("/preview-session")
            def preview_session():
                response = RedirectResponse("/")
                response.set_cookie("vortex_session", security.make_session({"uid": uid}), httponly=True)
                return response

        uvicorn.run(app, host="127.0.0.1", port=preview_port, lifespan="on", log_level="warning")
