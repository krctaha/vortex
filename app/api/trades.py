from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, runtime
from ..config import settings
from ..deps import check_origin, current_user
from ..services import binance, binance_ws, telegram, tsmom_engine

router = APIRouter(prefix="/api/trades", tags=["trades"])


class TradeBody(BaseModel):
    symbol: str
    side: str = Field(pattern="^(LONG|SHORT)$")
    entry: float
    stop: Optional[float] = None
    take_profit: Optional[float] = None
    margin_usdt: float = Field(default=0, ge=0)
    leverage: int = Field(default=0, ge=1, le=125)
    margin_type: str = Field(default="ISOLATED", pattern="^(ISOLATED|CROSSED)$")
    interval: str = "15m"
    status: str = Field(default="open", pattern="^(candidate|open)$")
    note: str = ""
    meta: Dict[str, Any] = Field(default_factory=dict)


def _risk_check(body: TradeBody) -> Dict[str, Any]:
    """Kullanicinin risk cercevesi: toplam 380$, islem basina max 60$ margin,
    islem basina max 6$ risk."""
    rcfg = runtime.get()["risk"]
    margin = body.margin_usdt or rcfg["max_position_margin"]
    lev = body.leverage or rcfg["default_leverage"]
    warnings: List[str] = []
    if margin > rcfg["max_position_margin"]:
        warnings.append(f"Margin {margin:.0f}$ — sınırın ({rcfg['max_position_margin']:.0f}$) üzerinde")
    notional = margin * lev
    qty = notional / body.entry if body.entry else 0.0
    risk = None
    if body.stop:
        per_unit = abs(body.entry - body.stop)
        risk = per_unit * qty
        if risk > rcfg["max_risk_per_trade"] * 1.02:
            warnings.append(
                f"Stop mesafesi bu margin/kaldıraçta {risk:.2f}$ risk demek — "
                f"sınır {rcfg['max_risk_per_trade']:.2f}$")
        if body.side == "LONG" and body.stop >= body.entry:
            warnings.append("LONG işlemde stop girişin üzerinde")
        if body.side == "SHORT" and body.stop <= body.entry:
            warnings.append("SHORT işlemde stop girişin altında")
    return {"margin": margin, "leverage": lev, "notional": notional,
            "qty": qty, "risk": risk, "warnings": warnings}


def _enrich(row: Dict[str, Any], price: Optional[float]) -> Dict[str, Any]:
    """Canli degerleri ekler.

    KRITIK: KAPALI bir islem canli fiyattan ASLA yeniden hesaplanmaz.
    MIHENK'te elle kapatilan karli bir islem sonradan negatif gorunuyordu;
    sebebi tam olarak buydu. Kapali islemde referans fiyat, kapanista
    kilitlenen exit_price'tir ve pnl/r dogrudan veritabanindan gelir.
    """
    out = dict(row)
    try:
        out["meta"] = json.loads(row.get("meta") or "{}")
    except json.JSONDecodeError:
        out["meta"] = {}

    is_closed = row.get("status") == "closed"
    if is_closed:
        out["live_price"] = row.get("exit_price") or row["entry"]
        out["pnl_live"] = row.get("pnl_usdt")
        out["r_live"] = row.get("r_multiple")
        out["locked"] = True
        if row.get("margin_usdt") and out["pnl_live"] is not None:
            out["pnl_pct_margin"] = round(out["pnl_live"] / row["margin_usdt"] * 100, 2)
        return out

    live = price or row["entry"]
    out["live_price"] = live
    out["locked"] = False
    direction = 1 if row["side"] == "LONG" else -1
    qty = row.get("qty") or 0.0
    out["pnl_live"] = round((live - row["entry"]) * direction * qty, 4)
    if row.get("stop"):
        per_unit_risk = abs(row["entry"] - row["stop"])
        out["r_live"] = round((live - row["entry"]) * direction / per_unit_risk, 3) \
            if per_unit_risk else None
    else:
        out["r_live"] = None
    if row.get("margin_usdt"):
        out["pnl_pct_margin"] = round(out["pnl_live"] / row["margin_usdt"] * 100, 2)
    return out


@router.get("")
def list_trades(status: str = "open,candidate", user=Depends(current_user)):
    rcfg = runtime.get()["risk"]
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    placeholders = ",".join("?" for _ in statuses)
    rows = db.query(
        f"SELECT * FROM trades WHERE user_id = ? AND status IN ({placeholders}) "
        "ORDER BY opened_at DESC", [user["id"], *statuses])
    ticks = binance_ws.snapshot()
    out = [_enrich(r, (ticks.get(r["symbol"]) or {}).get("price")) for r in rows]
    binance_ws.watch([r["symbol"] for r in rows])
    return {
        "trades": out,
        "open_count": sum(1 for r in out if r["status"] == "open"),
        "candidate_count": sum(1 for r in out if r["status"] == "candidate"),
        "total_margin_used": round(sum(r["margin_usdt"] for r in out if r["status"] == "open"), 2),
        "risk_frame": {
            "total_margin": settings.total_margin,
            "max_position_margin": rcfg["max_position_margin"],
            "max_risk_per_trade": rcfg["max_risk_per_trade"],
            "daily_loss_limit": rcfg["daily_loss_limit"],
            "default_leverage": rcfg["default_leverage"],
        },
    }


@router.post("")
def create_trade(body: TradeBody, user=Depends(current_user), _: None = Depends(check_origin)):
    calc = _risk_check(body)
    rcfg = runtime.get()["risk"]
    if body.status == "open":
        row = db.query_one("SELECT COALESCE(SUM(CASE WHEN pnl_usdt<0 THEN -pnl_usdt ELSE 0 END),0) loss FROM trades WHERE user_id=? AND status='closed' AND closed_at>=?", (user["id"], db.now_ms() - 86_400_000))
        used = float((row or {}).get("loss") or 0)
        if used >= rcfg["daily_loss_limit"] or (calc["risk"] is not None and used + calc["risk"] > rcfg["daily_loss_limit"]):
            raise HTTPException(status_code=409, detail=f"Günlük kayıp bütçesi dolu: kullanılan {used:.2f}$ / limit {rcfg['daily_loss_limit']:.2f}$")
    tid = db.execute(
        "INSERT INTO trades(user_id, symbol, side, status, mode, entry, stop, take_profit,"
        " margin_usdt, leverage, margin_type, qty, risk_usdt, opened_at, interval, note, meta)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user["id"], body.symbol.upper(), body.side, body.status, "paper", body.entry,
         body.stop, body.take_profit, calc["margin"], calc["leverage"], body.margin_type,
         calc["qty"], calc["risk"], db.now_ms(), body.interval, body.note,
         json.dumps(body.meta, ensure_ascii=False)))
    binance_ws.watch([body.symbol.upper()])
    return {"ok": True, "id": tid, "calc": calc, "warnings": calc["warnings"]}


class CloseBody(BaseModel):
    exit_price: Optional[float] = None
    note: str = ""


@router.post("/{trade_id}/close")
async def close_trade(trade_id: int, body: CloseBody, user=Depends(current_user),
                      _: None = Depends(check_origin)):
    row = db.query_one("SELECT * FROM trades WHERE id = ? AND user_id = ?", (trade_id, user["id"]))
    if not row:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı")
    if row["status"] == "closed":
        raise HTTPException(status_code=409, detail="İşlem zaten kapalı")
    # TSMOM paper/canli satiri bagimsiz kapatilamaz. Arastirma kaydini acik
    # birakmak sembol ve yon kontenjanini hayalet pozisyonla dolduruyordu.
    try:
        meta = json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    research_id = int(meta.get("research_id") or 0)
    if meta.get("engine") == "tsmom" and research_id:
        result = await tsmom_engine.close_manual(
            research_id, reason=body.note.strip() or "elle")
        if not result.get("ok"):
            raise HTTPException(status_code=409,
                                detail=result.get("error") or "Motor işlemi kapatılamadı")
        return result
    # MIHENK hatasi: kapanis fiyati KILITLENMELI, sonradan canli fiyattan
    # yeniden hesaplanmamali. Bu yuzden exit_price/pnl/r kolonlari yaziliyor.
    tick = binance_ws.snapshot().get(row["symbol"], {})
    exit_price = body.exit_price or tick.get("price") or row["entry"]
    direction = 1 if row["side"] == "LONG" else -1
    qty = row["qty"] or 0.0
    pnl = (exit_price - row["entry"]) * direction * qty
    r_mult = None
    if row["stop"]:
        per_unit = abs(row["entry"] - row["stop"])
        r_mult = (exit_price - row["entry"]) * direction / per_unit if per_unit else None
    db.execute(
        "UPDATE trades SET status='closed', exit_price=?, pnl_usdt=?, r_multiple=?,"
        " closed_at=?, note = CASE WHEN ?='' THEN note ELSE ? END WHERE id = ?",
        (exit_price, round(pnl, 6), round(r_mult, 4) if r_mult is not None else None,
         db.now_ms(), body.note, body.note, trade_id))
    return {"ok": True, "exit_price": exit_price, "pnl_usdt": round(pnl, 4),
            "r_multiple": round(r_mult, 3) if r_mult is not None else None}


@router.post("/{trade_id}/promote")
def promote_trade(trade_id: int, user=Depends(current_user), _: None = Depends(check_origin)):
    """Aday isleme donustur. Giris fiyati ANLIK fiyata guncellenir ve
    miktar/risk yeniden hesaplanir — aday fiyatiyla acilmis gibi davranmak
    karneyi yaniltir."""
    row = db.query_one("SELECT * FROM trades WHERE id = ? AND user_id = ?", (trade_id, user["id"]))
    if not row:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı")
    if row["status"] != "candidate":
        raise HTTPException(status_code=409, detail="Sadece aday işlem açılabilir")
    tick = binance_ws.snapshot().get(row["symbol"], {})
    entry = tick.get("price") or row["entry"]
    qty = (row["margin_usdt"] * row["leverage"]) / entry if entry else 0.0
    risk = abs(entry - row["stop"]) * qty if row["stop"] else None
    rcfg = runtime.get()["risk"]
    loss_row = db.query_one("SELECT COALESCE(SUM(CASE WHEN pnl_usdt<0 THEN -pnl_usdt ELSE 0 END),0) loss FROM trades WHERE user_id=? AND status='closed' AND closed_at>=?", (user["id"], db.now_ms() - 86_400_000))
    used = float((loss_row or {}).get("loss") or 0)
    if used >= rcfg["daily_loss_limit"] or (risk is not None and used + risk > rcfg["daily_loss_limit"]):
        raise HTTPException(status_code=409, detail=f"Günlük kayıp bütçesi dolu: kullanılan {used:.2f}$ / limit {rcfg['daily_loss_limit']:.2f}$")
    warnings = []
    if risk is not None and risk > rcfg["max_risk_per_trade"] * 1.02:
        warnings.append(f"Bu girişte stop riski {risk:.2f}$ — sınır {rcfg['max_risk_per_trade']:.2f}$")
    db.execute("UPDATE trades SET status='open', entry=?, qty=?, risk_usdt=?, opened_at=? WHERE id=?",
               (entry, qty, risk, db.now_ms(), trade_id))
    return {"ok": True, "entry": entry, "qty": qty, "risk_usdt": risk, "warnings": warnings}


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, user=Depends(current_user), _: None = Depends(check_origin)):
    row = db.query_one("SELECT id,status,meta FROM trades WHERE id = ? AND user_id = ?",
                       (trade_id, user["id"]))
    if not row:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı")
    try:
        meta = json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    if row["status"] == "open" and meta.get("engine") == "tsmom" and meta.get("research_id"):
        raise HTTPException(status_code=409,
                            detail="Motor işlemi silinemez; önce Kapat düğmesini kullan")
    db.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    return {"ok": True}


@router.post("/{trade_id}/send")
async def send_to_group(trade_id: int, user=Depends(current_user), _: None = Depends(check_origin)):
    row = db.query_one("SELECT * FROM trades WHERE id = ? AND user_id = ?", (trade_id, user["id"]))
    if not row:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı")
    tick = binance_ws.snapshot().get(row["symbol"], {})
    live = tick.get("price") or row["entry"]
    e = telegram.esc
    arrow = "🟢 LONG" if row["side"] == "LONG" else "🔴 SHORT"
    lines = [
        f"<b>VORTEX · {e(row['symbol'])}</b>  {arrow}",
        f"Giriş: <code>{e(row['entry'])}</code>",
    ]
    if row["stop"]:
        lines.append(f"Stop: <code>{e(row['stop'])}</code>")
    if row["take_profit"]:
        lines.append(f"Hedef: <code>{e(row['take_profit'])}</code>")
    lines += [
        f"Anlık: <code>{e(round(live, 8))}</code>",
        f"Kaldıraç: <code>{e(row['leverage'])}x {e(row['margin_type'])}</code> · "
        f"Margin: <code>{e(row['margin_usdt'])}$</code>",
        f"Zaman dilimi: <code>{e(row['interval'])}</code>",
    ]
    if row["note"]:
        lines.append(f"\n{e(row['note'])}")
    if binance.is_demo():
        lines.append("\n⚠️ <i>DEMO veri — gerçek fiyat değildir</i>")
    result = await telegram.send("\n".join(lines),
                                 token=user["telegram_token"], chat_id=user["telegram_chat_id"])
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result.get("error") or "Telegram gönderimi başarısız")
    return result
