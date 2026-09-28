"""Forward-only shadow ledger and bounded ranking feedback. Never sends orders."""
import asyncio
import hashlib
import json
import logging
import time
from .. import db
from . import binance, smc_quality, smc_controls

log = logging.getLogger(__name__)
_task = None
_status = {"last_checked": None, "error": None}

def init():
    db.execute("""CREATE TABLE IF NOT EXISTS smc_paper (
        id TEXT PRIMARY KEY, symbol TEXT NOT NULL, model TEXT NOT NULL,
        side TEXT NOT NULL, created_at INTEGER NOT NULL, checked_at INTEGER NOT NULL,
        state TEXT NOT NULL, entry REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL,
        opened_at INTEGER, closed_at INTEGER, result_r REAL, cost_r REAL,
        selected INTEGER NOT NULL, payload TEXT NOT NULL)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS smc_one_active
        ON smc_paper(symbol) WHERE state IN ('pending','open')""")

def setup_id(row):
    # Stable across candle-window shifts, unlike an array index or as_of timestamp.
    values = [row.get(k) for k in ("symbol", "side", "model", "entry", "stop", "target")]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:24]

def feedback(model, side):
    rows = db.query("SELECT cost_r FROM smc_paper WHERE model=? AND side=? AND state IN ('target','stop','timeout') AND cost_r IS NOT NULL AND closed_at<=? AND json_extract(payload,'$.verification.version')=? ORDER BY closed_at DESC LIMIT 200", (model, side,db.now_ms(),smc_quality.VERSION))
    n = len(rows)
    # Neutral prior equivalent to 30 examples, bounded outcomes, bounded score delta.
    mean = sum(max(-2, min(3, r["cost_r"])) for r in rows) / (n+30)
    return round(max(-8, min(8, mean*4)), 2), n

def rank(rows):
    init()
    direction = smc_controls.direction()
    active = {r["symbol"]:r for r in db.query("SELECT id,symbol,state FROM smc_paper WHERE state IN ('pending','open')")}
    eligible = []
    for row in rows:
        smc_quality.cost_guard(row)
        row["verification"] = smc_quality.score(row)
        delta, n = feedback(row.get("model", ""), row.get("side", ""))
        q = row["verification"]
        q.update(score=round(max(0,min(100,q["base"]+delta)),1),learning_delta=delta,samples=n)
        row["selected"] = False
        previous = active.get(row.get("symbol"))
        if previous and (previous["id"] != setup_id(row) or previous["state"] == "open"):
            row.update(ready=False,phase="TAKIPTE")
        if row.get("ready") and not row.get("demo") and smc_controls.allows(row, direction):
            eligible.append(row)
    eligible.sort(key=lambda r:r["verification"]["score"], reverse=True)
    # Keep the hard safety gates fixed; one of ten slots explores another eligible plan.
    chosen = eligible[:9]
    rest = eligible[9:]
    if rest:
        bucket = int(time.time()//900)
        chosen.append(min(rest,key=lambda r:hashlib.sha256(f"{setup_id(r)}:{bucket}".encode()).hexdigest()))
    for row in chosen:
        row["selected"] = True
    return sorted(rows,key=lambda r:(bool(r.get("ready")),bool(r.get("selected")),r["verification"]["score"]),reverse=True)

def register(rows):
    init()
    now = db.now_ms()
    for row in rows:
        row = dict(row)
        smc_quality.cost_guard(row)
        row.setdefault("verification",smc_quality.score(row))
        if not row.get("ready") or row.get("demo"):
            continue
        entry, stop, target = (row.get(k) for k in ("entry", "stop", "target"))
        if not all(isinstance(v,(float,int)) and v>0 for v in (entry,stop,target)):
            continue
        if not (stop < entry < target if row["side"] == "LONG" else target < entry < stop):
            continue
        db.execute("""INSERT OR IGNORE INTO smc_paper
            (id,symbol,model,side,created_at,checked_at,state,entry,stop,target,selected,payload)
            VALUES(?,?,?,?,?,?,'pending',?,?,?,?,?)""",
            (setup_id(row),row["symbol"],row["model"],row["side"],now,now,
             entry,stop,target,int(row.get("selected",False)),json.dumps(row,allow_nan=False)))
        if row.get("selected"):
            db.execute("UPDATE smc_paper SET selected=1 WHERE id=? AND state='pending'",(setup_id(row),))

def advance(trade, candles, now):
    """Only complete 1m candles that START after observation. Ambiguity is excluded."""
    t = dict(trade)
    for bar in candles:
        opened, closed = int(bar[0]), int(bar[6])
        if opened < t["created_at"] or closed <= t["checked_at"] or closed >= now:
            continue
        # Do not infer outcomes across missing bars, outages or partial history.
        expected = ((t["checked_at"]//60000)+1)*60000
        if opened > expected:
            t.update(state="data_gap", closed_at=closed)
            break
        lo, hi = float(bar[3]), float(bar[2])
        is_long = t["side"] == "LONG"
        stop_hit = lo <= t["stop"] if is_long else hi >= t["stop"]
        target_hit = hi >= t["target"] if is_long else lo <= t["target"]
        t["checked_at"] = closed
        if t["state"] == "pending":
            if opened - t["created_at"] > 3*3600_000:
                t.update(state="expired",closed_at=closed)
                break
            if lo <= t["entry"] <= hi:
                t.update(state="open",opened_at=opened)
                if stop_hit or target_hit:
                    t.update(state="ambiguous",closed_at=closed)
                    break
            elif stop_hit:
                t.update(state="invalidated",closed_at=closed)
                break
            continue
        if t["state"] == "open":
            if stop_hit and target_hit:
                t.update(state="ambiguous",closed_at=closed)
                break
            timed_out = closed - t["opened_at"] >= 24*3600_000
            if stop_hit or target_hit or timed_out:
                state = "stop" if stop_hit else "target" if target_hit else "timeout"
                # Gap through stop uses the worse opening price, not an optimistic stop fill.
                exit_price = (min(t["stop"],float(bar[1])) if is_long else max(t["stop"],float(bar[1]))) if stop_hit else t["target"] if target_hit else float(bar[4])
                risk = abs(t["entry"]-t["stop"])
                result = (exit_price-t["entry"])*(1 if is_long else -1)/risk
                costs = (t["entry"]+exit_price)*0.0007/risk
                t.update(state=state,closed_at=closed,result_r=result,cost_r=result-costs)
                break
    return t

async def tick():
    init()
    if binance.settings.data_mode != "live":
        return
    rows = db.query("SELECT * FROM smc_paper WHERE state IN ('pending','open')")
    for trade in rows:
        now = db.now_ms()
        if trade["state"] == "pending" and trade["entry"]*.0014/abs(trade["entry"]-trade["stop"]) >= 1:
            db.execute("UPDATE smc_paper SET state='invalidated',closed_at=? WHERE id=?",(now,trade["id"]))
            continue
        start = ((trade["checked_at"]//60000)+1)*60000
        if start >= now-60_000:
            continue
        try:
            # Request-weight pacing shared with the scanner; no privileged exchange API.
            from .smc_market import request
            candles = await request("/fapi/v1/klines", {"symbol":trade["symbol"],"interval":"1m",
                "startTime":start,"endTime":now-1,"limit":499},2)
            t = advance(trade,candles,now)
            db.execute("""UPDATE smc_paper SET checked_at=?,state=?,opened_at=?,closed_at=?,result_r=?,cost_r=? WHERE id=?""",
                (t["checked_at"],t["state"],t.get("opened_at"),t.get("closed_at"),t.get("result_r"),t.get("cost_r"),t["id"]))
        except Exception as exc:
            _status["error"] = type(exc).__name__
            log.warning("Shadow tracker data unavailable: %s",type(exc).__name__)
    _status["last_checked"] = db.now_ms()

def journal():
    init()
    rows = db.query("SELECT id,symbol,side,created_at,state,entry,stop,target,opened_at,closed_at,result_r,cost_r,selected FROM smc_paper WHERE selected=1 ORDER BY created_at DESC LIMIT 200")
    summary = db.query("SELECT state,COUNT(*) AS count,SUM(cost_r) AS total_r FROM smc_paper WHERE selected=1 GROUP BY state")
    return {"items":rows,"summary":summary,"tracker":dict(_status),"simulation":True,
            "note":"İleriye dönük simülasyon. Maliyet varsayımı: her yönde %0,05 ücret + %0,02 kayma; funding hariç. Gerçek hesap getirisi değildir."}

async def _loop():
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Shadow tracker failed: %s",type(exc).__name__)
        await asyncio.sleep(60)

async def start():
    global _task
    init()
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
