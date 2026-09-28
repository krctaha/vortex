"""VORTEX — Binance Futures kripto terminali."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, runtime
from .api import auth as auth_api, dashboard as dashboard_api, market as market_api, \
    misc as misc_api, trades as trades_api, engine as engine_api, runtime_settings as runtime_api, \
    push as push_api, users as users_api, trading as trading_api, mentor as mentor_api, \
    makro as makro_api
from .config import BASE_DIR, settings
from .deps import current_session, user_count
from .version import APP_VERSION, BUILD_DATE, BUILD_ID
from .services import premium_engine, terminal_briefing
from .api import terminal as terminal_api
from .services import mentor_scan, binance, binance_ws, coin_icons, notification_scheduler, \
    tsmom_engine, test_engine, hunter, research_metrics, market_library, mentor_sync, mentor_plan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    # 29.08: TARIH EKLENDI. Once "%H:%M:%S" idi; journalctl disinda okunan
    # her log satiri hangi gune ait belli olmuyordu.
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 29.08 — LOG GURULTUSU
# httpx her istegi INFO seviyesinde yaziyordu. Akis anketi 3 sembolu birkac
# saniyede bir cektigi icin log saniyede birkac satirla doluyor, gercek
# hatalar aralarinda kayboluyordu. Ayrica journald varsayilan olarak gecici
# modda calisabiliyor — gurultu, tutulan gercek gecmisi kisaltiyor.
# Istekleri gormek gerekirse: logging.getLogger("httpx").setLevel(logging.INFO)
for _gurultulu in ("httpx", "httpcore", "websockets.client", "asyncio"):
    logging.getLogger(_gurultulu).setLevel(logging.WARNING)

log = logging.getLogger("vortex")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


async def _warm_icons() -> None:
    """Ikon onbellegi bossa bir kez doldurur.

    Acilisi bloklamamasi icin arka plan gorevi. Onbellek doluysa hicbir
    disari istegi atmaz — yani her yeniden baslatmada CoinGecko'yu
    dovmuyoruz. Basarisiz olursa arayuz monogram rozetlerle calismaya
    devam eder, o yuzden hata olumcul degil.
    """
    try:
        if int(coin_icons.cache_stats().get("count", 0)) > 0:
            return
        await asyncio.sleep(5)          # acilis trafigi otursun
        result = await coin_icons.refresh()
        log.info("Coin ikonlari hazirlandi: %s", result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Coin ikonlari indirilemedi (monogram yedegi devrede): %s", exc)


def _retire_legacy_score_engine() -> int:
    """Eski 5-gosterge motorunu kalici olarak pasiflestir.

    Gecmis sinyal ve arastirma kayitlari denetim izi olarak korunur. Yalnizca
    halen bekleyen eski adaylar iptal edilir; acik/canli pozisyonlara dokunulmaz.
    """
    db.set_setting("signal_engine_enabled", False)
    cancelled = 0
    for row in db.query("SELECT id,meta,note FROM trades WHERE status='candidate'"):
        try:
            meta = json.loads(row["meta"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        legacy = bool(meta.get("signal_id")) and meta.get("engine") != "tsmom"
        legacy = legacy or str(row["note"] or "").startswith("Otomatik motor")
        if not legacy:
            continue
        db.execute("UPDATE trades SET status='cancelled',note=? WHERE id=?",
                   ("Arşivlendi — eski skor motoru V2.4.0'da kaldırıldı", row["id"]))
        cancelled += 1
    return cancelled


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    cost_backfilled = research_metrics.backfill()
    if cost_backfilled:
        log.info("V4 net maliyet muhasebesi: %s geçmiş kayıt tamamlandı", cost_backfilled)
    st = await binance.probe()
    log.info("Veri modu: %s %s", st["mode"].upper(),
             f'({st["reason"]})' if st.get("reason") else "")
    gecis = runtime.migrate()
    if gecis:
        log.info("Ayar gecisi uygulandi: %s", gecis)
    legacy_cancelled = _retire_legacy_score_engine()
    log.info("Eski skor motoru devre disi; %s bekleyen aday arsivlendi", legacy_cancelled)
    await binance.start_recovery()
    await binance_ws.start()
    await tsmom_engine.start()
    await hunter.start()
    await test_engine.start()
    await notification_scheduler.start()
    # Kutuphane taramadan ONCE baslar: mentor_scan artik aday listesini
    # kutuphaneden aliyor, bos kutuphane bos tarama demek olurdu.
    await market_library.start()
    await premium_engine.start()
    await terminal_briefing.start()
    await mentor_scan.start()
    await mentor_sync.start()
    # Plan onbellegi: arayuz artik sayfa acilisinda tarama
    # tetiklemiyor, bu gorevin yazdigi onbellegi okuyor.
    await mentor_plan.start()
    _icons_task = asyncio.create_task(_warm_icons())
    log.info("VORTEX hazır — http://%s:%s", settings.host, settings.port)
    yield
    await terminal_briefing.stop()
    await premium_engine.stop()
    await mentor_plan.stop()
    await mentor_sync.stop()
    await mentor_scan.stop()
    await market_library.stop()
    await notification_scheduler.stop()
    await test_engine.stop()
    await hunter.stop()
    await tsmom_engine.stop()
    await binance_ws.stop()
    await binance.stop_recovery()
    await binance.close()


app = FastAPI(title="VORTEX", docs_url=None, redoc_url=None, lifespan=lifespan)

app.include_router(auth_api.router)
app.include_router(dashboard_api.router)
app.include_router(market_api.router)
app.include_router(trades_api.router)
app.include_router(misc_api.router)
app.include_router(engine_api.router)
app.include_router(runtime_api.router)
app.include_router(push_api.router)
app.include_router(users_api.router)
app.include_router(trading_api.router)
app.include_router(mentor_api.router)
app.include_router(makro_api.router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Servis calisanini kok kapsamda sunar.

    /static/sw.js olarak kaydedilirse kapsami /static/ ile sinirlanir ve
    ana sayfayi kontrol edemez. Service-Worker-Allowed basligi ile kok
    kapsam veriliyor; push.js zaten scope:'/' istiyor.
    """
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/icon/{symbol}", include_in_schema=False)
def coin_icon(symbol: str):
    """Coin ikonu — onbellekte PNG varsa o, yoksa monogram SVG.

    Bu uc noktanin HICBIR kosulda 404 dondurmemesi gerekiyor; listelerde
    kirik resim gorunmesindense harften uretilmis rozet gosteriyoruz.
    Ikonlar nadiren degistigi icin uzun cache; SVG yedegi de deterministik
    oldugu icin ayni sekilde cache'lenebilir.
    """
    cached = coin_icons.cached_path(symbol)
    if cached:
        return FileResponse(cached, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=604800"})
    return Response(coin_icons.monogram_svg(symbol),
                    media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {
        "settings": settings,
        "app_version": APP_VERSION,
        "build_id": BUILD_ID,
        "build_date": BUILD_DATE,
        **ctx,
    })


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if user_count() == 0:
        return RedirectResponse("/kurulum", status_code=302)
    if not current_session(request):
        return RedirectResponse("/giris", status_code=302)
    # is_admin BURADA DA HESAPLANIYOR.
    # Onceden sabit False'ti: yan menudeki Mentor baglantisi "/" adresine
    # gidiyor, dogrudan /mentor yazinca ayni sayfa yonetici icerigiyle
    # aciliyordu. Ayni sayfanin iki adresten farkli gorunmesi, hangisinin
    # dogru oldugunu bilinemez yapiyordu.
    sess = current_session(request)
    row = db.query_one("SELECT role FROM users WHERE id = ? AND is_active=1", (sess.get("uid"),)) if sess else None
    return _page(request, "dashboard.html", page="dashboard", page_title="Dashboard",
                 is_admin=bool(row and row["role"] == "admin"))


@app.get("/terminal", response_class=HTMLResponse)
async def legacy_terminal(request: Request):
    """Eski özet korunur; ana ürün artık emir vermeyen Mentor'dur."""
    if user_count() == 0:
        return RedirectResponse("/kurulum", status_code=302)
    if not current_session(request):
        return RedirectResponse("/giris", status_code=302)
    return _page(request, "dashboard.html", page="dashboard")


WORKSPACE_PAGES = {
    "/mentor": ("mentor", "VORTEX Mentor"),
    "/portfoy": ("portfoy", "Portföy"),
    "/makro": ("makro", "Makro Panel"),
    "/piyasalar": ("markets", "Piyasa Tarayıcı"),
    "/analiz": ("analiz", "Grafik Analizi"),
    "/rsi-radar": ("rsi", "Kripto Screener"),
    "/haberler": ("haber", "Piyasa Gündemi"),
    "/takvim": ("takvim", "Makro Takvim"),
    "/karne": ("karne", "Motor Karnesi"),
    "/motor": ("motor", "Motor Kontrolü"),
    "/sistem-testi": ("test", "Ölçüm Laboratuvarı"),
    "/ayarlar": ("ayarlar", "Ayarlar"),
}


@app.get("/piyasalar", response_class=HTMLResponse)
@app.get("/mentor", response_class=HTMLResponse)
@app.get("/portfoy", response_class=HTMLResponse)
@app.get("/makro", response_class=HTMLResponse)
@app.get("/analiz", response_class=HTMLResponse)
@app.get("/rsi-radar", response_class=HTMLResponse)
@app.get("/haberler", response_class=HTMLResponse)
@app.get("/takvim", response_class=HTMLResponse)
@app.get("/karne", response_class=HTMLResponse)
@app.get("/motor", response_class=HTMLResponse)
@app.get("/sistem-testi", response_class=HTMLResponse)
@app.get("/ayarlar", response_class=HTMLResponse)
async def workspace_page(request: Request):
    if user_count() == 0:
        return RedirectResponse("/kurulum", status_code=302)
    if not current_session(request):
        return RedirectResponse("/giris", status_code=302)
    page, title = WORKSPACE_PAGES[request.url.path]
    # Yonetici panelini SUNUCUDA kapatiyoruz. Sadece JS ile gizlemek, yonetici
    # olmayan kullaniciya paneli yine de gondermek demek — kaynagi acan gorur.
    # (Uc noktalar zaten ayrica admin_user ile korunuyor; bu ikinci katman.)
    sess = current_session(request)
    row = db.query_one("SELECT role FROM users WHERE id = ? AND is_active=1", (sess.get("uid"),)) if sess else None
    if not row:
        return RedirectResponse('/giris',status_code=302)
    is_admin = bool(row and row["role"] == "admin")
    if request.url.path in ("/portfoy","/makro","/motor","/sistem-testi"):
        return RedirectResponse("/", status_code=302)
    if request.url.path == "/karne":
        return RedirectResponse("/islem-karnesi",status_code=302)
    if request.url.path == "/ayarlar":
        return _page(request,"terminal-pages.html",page="settings",page_title="Ayarlar",is_admin=is_admin)
    return _page(request, "workspace.html", page=page, page_title=title, is_admin=is_admin)


@app.get("/islem-karnesi",response_class=HTMLResponse)
@app.get("/global-piyasalar",response_class=HTMLResponse)
async def terminal_page(request:Request):
    if not current_session(request):
        return RedirectResponse("/giris",status_code=302)
    page = "journal" if request.url.path == "/islem-karnesi" else "cross"
    return _page(request,"terminal-pages.html",page=page,page_title="İşlem Karnesi" if page=="journal" else "Global Piyasalar")


app.include_router(terminal_api.router)


@app.websocket("/ws/briefing")
async def ws_briefing(websocket:WebSocket):
    from .security import read_session
    from urllib.parse import urlsplit
    origin = websocket.headers.get("origin")
    if origin and urlsplit(origin).netloc != websocket.headers.get("host"):
        await websocket.close(code=4403)
        return
    session = read_session(websocket.cookies.get("vortex_session"))
    user = db.query_one("SELECT is_active FROM users WHERE id=?",(session.get("uid"),)) if session else None
    if not user or not user["is_active"]:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    queue = asyncio.Queue(maxsize=2)
    terminal_briefing.subscribers.add(queue)
    try:
        await websocket.send_json(dict(terminal_briefing.snapshot))
        last_scan_state = None
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(),timeout=1)
            except asyncio.TimeoutError:
                payload = None
            if payload:
                await websocket.send_json(payload)
            scanner=premium_engine.status()
            signature=(scanner.get("last_scan_at"),scanner.get("scanning"),scanner.get("progress",{}).get("completed"),scanner.get("enabled"),scanner.get("last_error"))
            if signature != last_scan_state:
                scanner["results"]=[{k:r.get(k) for k in ("symbol","phase","side","ready","sparkline")} for r in scanner.get("results",[])]
                await websocket.send_json({"type":"scanner","scanner":scanner})
                last_scan_state=signature
    except (WebSocketDisconnect,RuntimeError):
        pass
    finally:
        terminal_briefing.subscribers.discard(queue)


@app.get("/copy-trade", include_in_schema=False)
async def retired_positions_page(request: Request):
    """Eski pozisyon sayfası kaldırıldı; para ayarları artık Ayarlar'da."""
    if user_count() == 0:
        return RedirectResponse("/kurulum", status_code=302)
    if not current_session(request):
        return RedirectResponse("/giris", status_code=302)
    return RedirectResponse("/ayarlar#trading-settings", status_code=302)


@app.get("/giris", response_class=HTMLResponse)
async def login_page(request: Request):
    if user_count() == 0:
        return RedirectResponse("/kurulum", status_code=302)
    if current_session(request):
        return RedirectResponse("/", status_code=302)
    return _page(request, "login.html", page="login", mode="login")


@app.get("/kurulum", response_class=HTMLResponse)
async def setup_page(request: Request):
    if user_count() > 0:
        return RedirectResponse("/giris", status_code=302)
    return _page(request, "login.html", page="login", mode="setup")


@app.get("/saglik")
async def health():
    now_s = time.time()
    now_ms = db.now_ms()
    data = binance.status()
    stream = binance_ws.status()
    pulse = tsmom_engine.pulse()
    reconcile = db.get_setting("live_reconcile_last", {}) or {}
    mentor_mode = bool(db.get_setting("mentor_mode", False))
    engine_active = bool(pulse.get("enabled") and runtime.get()["tsmom"].get("enabled", True))
    live_rows = db.query(
        "SELECT meta FROM trades WHERE mode='live' AND status='open'")
    stored_protected = 0
    live_states = []
    for row in live_rows:
        try:
            meta = json.loads(row.get("meta") or "{}")
        except (TypeError, ValueError):
            meta = {}
        stored_protected += int(bool(meta.get("stop_algo_id")))
        live_states.append(str(meta.get("state", "")))

    def age_seconds(value, milliseconds: bool = False):
        try:
            ts = float(value or 0)
            if not ts:
                return None
            return round(max(0.0, (now_ms - ts) / 1000.0) if milliseconds
                         else max(0.0, now_s - ts), 1)
        except (TypeError, ValueError):
            return None

    data_age = age_seconds(data.get("last_ok"))
    ws_age = age_seconds(stream.get("last_message"))
    scan_age = age_seconds(pulse.get("last_scan_at"), True)
    watch_age = age_seconds(pulse.get("last_watch_at"), True)
    reconcile_age = age_seconds(reconcile.get("at"), True)
    degraded = []
    if data.get("mode") != "live" or data_age is None or data_age > 120:
        degraded.append("piyasa verisi taze değil")
    if stream.get("mode") != "live" or ws_age is None or ws_age > 60:
        degraded.append("websocket akışı taze değil")
    max_scan_age = max(900, int(pulse.get("scan_interval_seconds") or 300) * 3)
    if engine_active and not mentor_mode and (scan_age is None or scan_age > max_scan_age):
        degraded.append("motor taraması gecikmiş")
    if live_rows and (reconcile_age is None or reconcile_age > 120):
        degraded.append("canlı koruma denetimi gecikmiş")
    if pulse.get("exit_errors"):
        degraded.append("pozisyon çıkış hatası var")
    if "UNPROTECTED_EMERGENCY" in live_states:
        degraded.append("korumasız canlı pozisyon alarmı")

    return {
        "ok": not degraded, "ready": not degraded, "degraded": degraded,
        "product_mode": "mentor" if mentor_mode else "autonomous",
        "primary_engine": db.get_setting("primary_engine", "smc_ict"),
        "smc": {key: premium_engine.status().get(key) for key in
                ("enabled", "last_scan_at", "scanning", "scanned", "errors", "last_error")},
        "data_mode": data.get("mode"), "data_age_seconds": data_age,
        "ws": stream.get("mode"), "ws_age_seconds": ws_age,
        "motor": {"enabled": engine_active, "scan_age_seconds": scan_age,
                  "watch_age_seconds": watch_age, "research_open": pulse.get("open")},
        "live": {"open": len(live_rows), "stored_protected": stored_protected,
                 "reconcile_age_seconds": reconcile_age,
                 "last_reconcile": reconcile},
        "version": APP_VERSION, "build": BUILD_ID,
    }


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    from .services import smc_orderbook
    from .security import read_session
    from urllib.parse import urlsplit
    origin = websocket.headers.get("origin")
    if origin and urlsplit(origin).netloc != websocket.headers.get("host"):
        await websocket.close(code=4403)
        return
    token = websocket.cookies.get("vortex_session")
    session = read_session(token)
    user = db.query_one("SELECT is_active FROM users WHERE id=?",(session.get("uid"),)) if session else None
    if not user or not user["is_active"]:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    queue = binance_ws.subscribe()
    try:
        await websocket.send_text(json.dumps({
            "type": "snapshot", "ticks": binance_ws.snapshot(),
            "orderbook": smc_orderbook.snapshot(),
            "mode": binance.status()["mode"],
            "server_time_ms": int(time.time() * 1000),
        }))

        async def reader():
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "watch" and isinstance(msg.get("symbols"), list):
                    binance_ws.watch(msg["symbols"])

        reader_task = asyncio.create_task(reader())
        try:
            while True:
                tick = await queue.get()
                latest = {(tick.get('type','tick'),tick["symbol"]): tick}
                await asyncio.sleep(.05)
                while not queue.empty():
                    tick = queue.get_nowait()
                    latest[(tick.get('type','tick'),tick["symbol"])] = tick
                await websocket.send_json({"type":"batch","ticks":[r for r in latest.values() if r.get('type')!='depth'],
                                           "orderbook":next((r for r in latest.values() if r.get('type')=='depth'),None),
                                           "server_time_ms":int(time.time()*1000)})
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("WS istemci hatasi: %s", exc)
    finally:
        binance_ws.unsubscribe(queue)


# =================================================================== #
# BEKLENMEYEN HATALAR — sessizce "Internal Server Error" demek yetmez.
#
# Kullanici arayuzde yalnizca o cumleyi goruyordu ve o cumle hicbir sey
# soylemiyor: hangi uc, hangi istisna, hangi satir. Sunucu logunda
# duruyordu ama oraya bakmak icin SSH gerekiyordu. Artik hata hem tam
# haliyle loglaniyor hem de teshis panelinden okunabiliyor.
# =================================================================== #
@app.exception_handler(Exception)
async def _beklenmeyen_hata(request: Request, exc: Exception):
    from . import hata_kaydi
    yol = str(getattr(request, "url", "?"))
    hata_kaydi.kaydet(yol, exc)
    log.exception("Beklenmeyen hata: %s", yol)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"[:300],
                 "ipucu": "Ayrıntı için Mentor › Araçlar › Bağlantı teşhisi "
                          "panelindeki 'son hatalar' bölümüne bak."},
    )


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Bulunamadı"}, status_code=404)
    return RedirectResponse("/", status_code=302)
