"""Rate-limited Telegram research digests from the existing scanner cache."""
import asyncio
import contextlib
from datetime import datetime
from zoneinfo import ZoneInfo
from .. import db
from . import telegram, smc_journal, smc_controls, binance_ws, smc_board

DEFAULTS = {"enabled": False, "instant_enabled": True, "interval_minutes": 60, "symbol_cooldown_hours": 6, "min_score": 65}
_task = None
_lock = asyncio.Lock()
_wake = asyncio.Event()

def wake():
    _wake.set()

def preferences(uid):
    saved = db.get_setting(f"smc_notifications_{uid}", {})
    return {**DEFAULTS, **(saved if isinstance(saved, dict) else {})}

def init():
    db.execute("""CREATE TABLE IF NOT EXISTS smc_notification_log (
      user_id INTEGER NOT NULL, setup_id TEXT NOT NULL, symbol TEXT NOT NULL,
      attempted_at INTEGER NOT NULL, state TEXT NOT NULL,
      PRIMARY KEY(user_id,setup_id))""")

def state(uid):
    return db.get_setting(f"smc_notification_state_{uid}", {})

def pick(uid, scanner, cfg, now, instant=False):
    rows = sorted(scanner.get("candidates", []), key=lambda r:r.get("verification",{}).get("score",0), reverse=True)
    chosen, seen = [], set()
    ticks = binance_ws.snapshot()
    for row in rows:
        if not row.get("ready") or not row.get("selected") or row.get("demo") or not smc_controls.allows(row):
            continue
        if row.get("verification",{}).get("score",0) < cfg["min_score"]:
            continue
        symbol = row["symbol"]
        if symbol in seen:
            continue
        tick = ticks.get(symbol, {})
        if instant:
            record=db.query_one("SELECT * FROM smc_paper WHERE id=?",(smc_journal.setup_id(row),))
            since=db.get_setting(f"smc_instant_since_{uid}",now)
            if not record or record["created_at"]<since or now-record["created_at"]>5*60_000 or smc_board.entry_status(record,tick,now):
                continue
        price = tick.get("price", 0)
        if now - (tick.get("ts") or 0) > 10_000 or not min(row["stop"],row["target"]) < price < max(row["stop"],row["target"]):
            continue
        if db.query_one("SELECT 1 FROM smc_notification_log WHERE user_id=? AND (setup_id=? OR (symbol=? AND attempted_at>?))",
                        (uid,smc_journal.setup_id(row),symbol,now if instant else now-cfg["symbol_cooldown_hours"]*3_600_000)):
            continue
        chosen.append(row); seen.add(symbol)
        if len(chosen) == (10 if instant else 2):
            break
    return chosen

def report(scanner, rows):
    e = telegram.esc
    stamp = datetime.fromtimestamp(scanner["last_scan_at"]/1000, ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M")
    results = scanner.get("results", [])
    longs = sum(r.get("side") == "LONG" for r in results)
    shorts = sum(r.get("side") == "SHORT" for r in results)
    lines = ["<b>VORTEX · Piyasa ve SMC özeti</b>",
             f"{e(stamp)} TR · {scanner.get('scanned',0)} piyasa · 4H/15M",
             f"Yapısal yön: {longs} LONG / {shorts} SHORT / {max(0,len(results)-longs-shorts)} belirsiz",
             f"Seçim modu: <b>{e(smc_controls.direction())}</b>", ""]
    for row in rows:
        q = row.get("verification", {})
        evidence = [c["name"] for c in q.get("components",[]) if c.get("value",0) >= .5][:6]
        lines += [f"<b>{e(row['symbol'])} · {e(row['side'])}</b> — {q.get('score',0):.1f}/100",
                  f"Model: {e(row.get('model','SMC'))}",
                  f"Giriş {row['entry']:.8g} · Stop {row['stop']:.8g} · Hedef {row['target']:.8g}",
                  f"Brüt R:R {row.get('rr',0):.2f} · Öğrenme katkısı {q.get('learning_delta',0):+.1f}",
                  "Teyitler: " + e(", ".join(evidence) or "Yapı kontrolü"),
                  "Stop sınırı aşılırsa kurulum geçersizdir; fiyat değiştikçe yeniden kontrol edin.", ""]
    if not rows:
        lines += ["Yeni, tekrar sınırına takılmayan ve bildirim eşiğini geçen hazır plan yok. Zorla sinyal üretilmedi.", ""]
    lines += ["<i>Araştırma / simülasyon. Skor kazanma olasılığı değildir; gerçek emir gönderilmez. Ücret, kayma ve funding sonucu değiştirebilir.</i>"]
    return "\n".join(lines)

async def send_instant(user,scanner,cfg,now):
    uid=user["id"]
    if not cfg.get("instant_enabled"):
        return
    if db.get_setting(f"smc_instant_since_{uid}") is None:
        db.set_setting(f"smc_instant_since_{uid}",now)
    for row in pick(uid,scanner,cfg,now,instant=True):
        # Recheck immediately before send; the tracker may have advanced.
        record=db.query_one("SELECT * FROM smc_paper WHERE id=?",(smc_journal.setup_id(row),))
        if not record or smc_board.entry_status(record,binance_ws.snapshot().get(row["symbol"]),db.now_ms()):
            continue
        db.execute("INSERT OR IGNORE INTO smc_notification_log VALUES(?,?,?,?,?)",(uid,smc_journal.setup_id(row),row["symbol"],now,"attempted"))
        text=report(scanner,[row]).replace("Piyasa ve SMC özeti","YENİ GİRİŞ PLANI",1)
        text=text.replace("<i>Araştırma", "<b>Giriş seviyesi henüz bekleniyor. Fiyatı kovalamayın; gönderim sonrası durum değişebilir.</b>\n<i>Araştırma",1)
        try:
            result=await telegram.send(text,token=user["telegram_token"],chat_id=user["telegram_chat_id"],silent=False)
        except Exception:
            result={"ok":False,"error":"Anlık bildirim teslimatı doğrulanamadı; tekrar gönderilmeyecek."}
        db.execute("UPDATE smc_notification_log SET state=? WHERE user_id=? AND setup_id=?",("sent" if result.get("ok") else "unconfirmed",uid,smc_journal.setup_id(row)))
        previous=state(uid)
        previous.update(last_instant_attempt=now,instant_error=result.get("error"))
        if result.get("ok"):previous["last_instant_sent"]=db.now_ms()
        db.set_setting(f"smc_notification_state_{uid}",previous)

async def run_due():
    from . import premium_engine
    async with _lock:
        init()
        scanner = premium_engine.status()
        if scanner.get("demo") or scanner.get("stale") or scanner.get("scanning") or not scanner.get("enabled"):
            return
        now = db.now_ms()
        users = db.query("SELECT id,telegram_token,telegram_chat_id FROM users WHERE is_active=1")
        for user in users:
            uid = user["id"]; cfg = preferences(uid); previous = state(uid)
            if not cfg["enabled"]:
                continue
            if not telegram.configured(user["telegram_token"],user["telegram_chat_id"]):
                db.set_setting(f"smc_notification_state_{uid}", {**previous,"error":"Bot token ve Chat ID kaydedilmeli."})
                continue
            await send_instant(user,scanner,cfg,now)
            previous=state(uid)
            if now - previous.get("last_attempt",0) < cfg["interval_minutes"]*60_000:
                continue
            if scanner.get("last_scan_at") == previous.get("scan_at"):
                continue
            rows = [] if cfg.get("instant_enabled") else pick(uid,scanner,cfg,now)
            # Claim before network I/O. Uncertain deliveries are not retried:
            # Telegram sendMessage has no idempotency key.
            for row in rows:
                db.execute("INSERT OR IGNORE INTO smc_notification_log VALUES(?,?,?,?,?)",
                    (uid,smc_journal.setup_id(row),row["symbol"],now,"attempted"))
            current = {**previous,"last_attempt":now,"scan_at":scanner["last_scan_at"],"error":None}
            db.set_setting(f"smc_notification_state_{uid}",current)
            try:
                message=report(scanner,rows)
                if cfg.get("instant_enabled"):
                    message=message.replace("Yeni, tekrar sınırına takılmayan ve bildirim eşiğini geçen hazır plan yok. Zorla sinyal üretilmedi.","Yeni uygun giriş planları ayrı bildirimle iletilir. Bu mesaj periyodik piyasa özetidir.")
                result = await telegram.send(message,token=user["telegram_token"],chat_id=user["telegram_chat_id"],silent=True)
            except Exception:
                result = {"ok":False,"error":"Telegram gönderimi tamamlanamadı; yinelenmeyi önlemek için bu kurulum tekrar gönderilmeyecek."}
            current["error"] = result.get("error") if not result.get("ok") else None
            if result.get("ok"):
                current["last_sent"] = db.now_ms()
            db.set_setting(f"smc_notification_state_{uid}",current)
            for row in rows:
                db.execute("UPDATE smc_notification_log SET state=? WHERE user_id=? AND setup_id=?",
                    ("sent" if result.get("ok") else "unconfirmed",uid,smc_journal.setup_id(row)))

async def _loop():
    while True:
        try:
            await run_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log provider URLs: bot tokens are embedded in them.
            import logging
            logging.getLogger(__name__).warning("SMC bildirim döngüsü başarısız; sonraki kontrolde yeniden denenecek.")
        try:
            await asyncio.wait_for(_wake.wait(),timeout=5)
        except asyncio.TimeoutError:
            pass
        _wake.clear()

async def start():
    global _task
    init()
    if not _task or _task.done():
        _task = asyncio.create_task(_loop())

async def stop():
    global _task
    if _task:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
        _task = None
