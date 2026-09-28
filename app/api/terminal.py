from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from .. import db
from ..deps import current_user, check_origin, admin_user
from ..services import smc_journal, terminal_briefing, binance_ws, smc_controls, smc_notifications, premium_engine, telegram

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

@router.get("/preferences")
def preferences(user=Depends(current_user)):
    return {"direction":smc_controls.direction(),"notifications":smc_notifications.preferences(user["id"]),
            "notification_state":smc_notifications.state(user["id"]),
            "telegram":{"configured":telegram.configured(user.get("telegram_token",""),user.get("telegram_chat_id","")),
                        "token_saved":bool(user.get("telegram_token")),"chat_id":user.get("telegram_chat_id","")}}

class DirectionBody(BaseModel):
    direction: Literal["AUTO","LONG","SHORT"]

@router.post("/direction")
def direction(body:DirectionBody,user=Depends(admin_user),_:None=Depends(check_origin)):
    premium_engine.set_direction(body.direction)
    return {"ok":True,"direction":smc_controls.direction()}

class NotificationBody(BaseModel):
    enabled: bool
    instant_enabled: bool = True
    interval_minutes: int = Field(default=60,ge=15,le=1440)
    symbol_cooldown_hours: int = Field(default=6,ge=1,le=168)
    min_score: int = Field(default=65,ge=0,le=100)

@router.post("/notifications")
def notifications(body:NotificationBody,user=Depends(current_user),_:None=Depends(check_origin)):
    previous=smc_notifications.preferences(user["id"])
    if body.enabled and body.instant_enabled and (not previous["enabled"] or not previous.get("instant_enabled")):
        db.set_setting(f"smc_instant_since_{user['id']}",db.now_ms())
    db.set_setting(f"smc_notifications_{user['id']}",body.model_dump())
    return {"ok":True,"notifications":smc_notifications.preferences(user["id"])}

@router.get("/journal")
def journal(_user=Depends(current_user)):
    return smc_journal.journal()

@router.get("/briefing")
def briefing(_user=Depends(current_user)):
    return terminal_briefing.snapshot

def cross_init():
    db.execute("""CREATE TABLE IF NOT EXISTS cross_manual (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, asset TEXT NOT NULL,
        side TEXT NOT NULL, entry REAL NOT NULL, quantity REAL NOT NULL,
        created_at INTEGER NOT NULL, exit REAL, closed_at INTEGER)""")

class CrossTrade(BaseModel):
    asset: Literal["NASDAQ","EURUSD","GBPUSD","USDJPY","XAUUSD","XAGUSD"]
    side: Literal["LONG","SHORT"]
    entry: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    quantity: float = Field(gt=0, le=1e9, allow_inf_nan=False)

class CloseTrade(BaseModel):
    exit: float = Field(gt=0, le=1e9, allow_inf_nan=False)

@router.get("/cross")
def cross(user=Depends(current_user)):
    cross_init()
    return {"provider_connected":False,"mode":"manual",
        "instruments":["NASDAQ","EURUSD","GBPUSD","USDJPY","XAUUSD","XAGUSD"],
        "items":db.query("SELECT * FROM cross_manual WHERE user_id=? ORDER BY created_at DESC LIMIT 200",(user["id"],))}

@router.post("/cross")
def create_cross(body:CrossTrade,user=Depends(current_user),_:None=Depends(check_origin)):
    cross_init()
    uid=db.execute("INSERT INTO cross_manual(user_id,asset,side,entry,quantity,created_at) VALUES(?,?,?,?,?,?)",
        (user["id"],body.asset,body.side,body.entry,body.quantity,db.now_ms()))
    return {"ok":True,"id":uid,"manual":True}

@router.post("/cross/{trade_id}/close")
def close_cross(trade_id:int,body:CloseTrade,user=Depends(current_user),_:None=Depends(check_origin)):
    cross_init()
    row=db.query_one("SELECT id FROM cross_manual WHERE id=? AND user_id=? AND closed_at IS NULL",(trade_id,user["id"]))
    if not row:
        raise HTTPException(404,"Açık kayıt bulunamadı")
    db.execute("UPDATE cross_manual SET exit=?,closed_at=? WHERE id=? AND user_id=?",(body.exit,db.now_ms(),trade_id,user["id"]))
    return {"ok":True}
