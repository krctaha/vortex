"""Separate fresh entry opportunities from existing paper positions."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from .. import db
from . import smc_journal, smc_controls, binance_ws

def entry_status(record, tick, now):
    if record.get("state") != "pending":
        return "Giriş gerçekleşmiş veya kayıt kapanmış"
    if now-record["created_at"] > 3*3600_000:
        return "Giriş süresi doldu"
    if not tick or now-(tick.get("ts") or 0)>10_000 or tick.get("demo"):
        return "Canlı fiyat doğrulanamıyor"
    p=tick.get("price") or 0
    if not min(record["stop"],record["target"])<p<max(record["stop"],record["target"]):
        return "Fiyat plan sınırlarının dışında"
    if (record["side"]=="LONG" and p<=record["entry"]) or (record["side"]=="SHORT" and p>=record["entry"]):
        return "Giriş seviyesi görülmüş; takip teyidi bekleniyor"
    return None

def snapshot(scanner):
    smc_journal.init()
    now=db.now_ms();ticks=binance_ws.snapshot()
    midnight=datetime.fromtimestamp(now/1000,ZoneInfo("Europe/Istanbul")).replace(hour=0,minute=0,second=0,microsecond=0)
    start=int(midnight.timestamp()*1000)
    rows=db.query("SELECT * FROM smc_paper WHERE selected=1 AND (state IN ('pending','open') OR closed_at>=?) ORDER BY created_at DESC",(start,))
    candidates={smc_journal.setup_id(r):r for r in scanner.get("candidates",[]) if r.get("ready") and r.get("selected") and not r.get("demo")}
    pending,opened,closed=[],[],[]
    for record in rows:
        payload=json.loads(record.pop("payload"));record["verification"]=payload.get("verification",{})
        record["model"]=payload.get("model",record.get("model"));record["rr"]=payload.get("rr")
        tick=ticks.get(record["symbol"],{})
        record["live_price"]=tick.get("price") if now-(tick.get("ts") or 0)<=10_000 else None
        if record["state"]=="pending":
            reason=entry_status(record,tick,now)
            if not reason and (scanner.get("stale") or scanner.get("demo") or not scanner.get("enabled")):
                reason="Motor veya güncel teyit bekleniyor"
            if not reason and (record["id"] not in candidates or not smc_controls.allows(record)):
                reason="Güncel yön / kurulum teyidi bekleniyor"
            record.update(actionable=reason is None,entry_note=reason or "Giriş seviyesi bekleniyor; piyasa emri çağrısı değildir")
            pending.append(record)
        elif record["state"]=="open":
            record["entry_note"]="Giriş simülasyonda gerçekleşti — yeni giriş fırsatı değil"
            if record["live_price"] is not None and not min(record["stop"],record["target"])<record["live_price"]<max(record["stop"],record["target"]):
                record["entry_note"]="Çıkış sınırı görüldü; kapanış teyidi bekleniyor — yeni giriş değil"
            opened.append(record)
        else:
            closed.append(record)
    counts={s:sum(r["state"]==s for r in closed) for s in ("target","stop","timeout","expired","invalidated","ambiguous","data_gap")}
    return {"pending":pending,"open":opened,"closed_today":closed,"today":counts,
            "today_date":midnight.strftime("%d.%m.%Y"),"updated_at":now,
            "new_today":db.query_one("SELECT count(*) n FROM smc_paper WHERE selected=1 AND created_at>=?",(start,))["n"],
            "simulation":True,"tracker":smc_journal.journal()["tracker"]}
