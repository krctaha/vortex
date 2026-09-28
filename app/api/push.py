"""Web Push abonelik uclari."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..deps import admin_user, check_origin, current_session
from ..services import webpush

router = APIRouter(prefix="/api/push", tags=["push"])


class SubscribeBody(BaseModel):
    subscription: Dict[str, Any]


class UnsubscribeBody(BaseModel):
    endpoint: str


class TestBody(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None


@router.get("/status")
def push_status():
    """Genel anahtar ve durum — giris yapmadan da okunabilir olmali,
    servis calisani abonelik kurarken buna ihtiyac duyuyor."""
    return webpush.status()


@router.post("/subscribe")
def subscribe(body: SubscribeBody, request: Request,
              _: None = Depends(check_origin)):
    sess = current_session(request)
    user_id = sess.get("uid") if sess else None
    ok = webpush.save_subscription(user_id, body.subscription,
                                   request.headers.get("user-agent", ""))
    return {"ok": ok, "subscriptions": webpush.status()["subscriptions"]}


@router.post("/unsubscribe")
def unsubscribe(body: UnsubscribeBody, _: None = Depends(check_origin)):
    webpush.remove_subscription(body.endpoint)
    return {"ok": True}


@router.post("/test")
async def test(body: TestBody, _user=Depends(admin_user),
               _: None = Depends(check_origin)):
    return await webpush.send(
        body.title or "VORTEX bildirim testi",
        body.body or "Bildirimler çalışıyor. Sinyaller buradan gelecek.",
        url="/", tag="test")
