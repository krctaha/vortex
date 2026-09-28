from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import db, security
from ..deps import COOKIE_NAME, admin_user, check_origin, current_user, user_count

router = APIRouter(prefix="/api/auth", tags=["auth"])

_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
WINDOW = 300.0


def _rate_limit(key: str) -> None:
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < WINDOW]
    if len(hits) >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Çok fazla deneme. 5 dakika sonra tekrar deneyin.")
    hits.append(now)
    _attempts[key] = hits


class LoginBody(BaseModel):
    username: str = Field(min_length=2, max_length=48)
    password: str = Field(min_length=1, max_length=256)


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=48)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=120)


@router.get("/state")
def state(request: Request):
    from ..deps import current_session
    sess = current_session(request)
    user = None
    if sess:
        row = db.query_one("SELECT id, username, display_name, email, role, avatar FROM users WHERE id = ? AND is_active=1",
                           (sess.get("uid"),))
        user = row
    return {"authenticated": bool(user), "user": user, "needs_setup": user_count() == 0}


@router.post("/register")
def register(body: RegisterBody, request: Request, _: None = Depends(check_origin)):
    """Ilk kullanici otomatik admin olur. Sonrakiler icin admin yetkisi gerekir."""
    # 29.08: login'de hiz siniri vardi, register'da yoktu.
    _rate_limit(f"reg:{request.client.host if request.client else '?'}")
    if user_count() > 0:
        admin_user(current_user(request))
    if db.query_one("SELECT id FROM users WHERE username = ?", (body.username.lower(),)):
        raise HTTPException(status_code=409, detail="Bu kullanıcı adı zaten var")
    role = "admin" if user_count() == 0 else "user"
    uid = db.execute(
        "INSERT INTO users(username, display_name, email, password_hash, role, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (body.username.lower(), body.display_name or body.username, body.email,
         security.hash_password(body.password), role, db.now_ms()))
    return {"ok": True, "id": uid, "role": role}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, _: None = Depends(check_origin)):
    ip = request.client.host if request.client else "?"
    _rate_limit(f"{ip}:{body.username.lower()}")
    user = db.query_one("SELECT * FROM users WHERE username = ?", (body.username.lower(),))
    if not user or not security.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı")
    if "is_active" in user.keys() and not int(user["is_active"] or 0):
        # Sifre dogru ama hesap pasif. Mesaj bilerek ayri: kullanici
        # sifresini yanlis hatirladigini sanip ugrasmasin.
        raise HTTPException(status_code=403, detail="Hesabınız pasif durumda. Yöneticiyle görüşün.")
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (db.now_ms(), user["id"]))
    token = security.make_session({"uid": user["id"], "u": user["username"], "r": user["role"]})
    secure = (request.url.scheme == "https"
              or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
              or security.settings.public_url.startswith("https://"))
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                        secure=secure, max_age=security.SESSION_MAX_AGE, path="/")
    _attempts.pop(f"{ip}:{body.username.lower()}", None)
    return {"ok": True, "user": {"id": user["id"], "username": user["username"],
                                 "display_name": user["display_name"], "role": user["role"]}}


@router.post("/logout")
def logout(response: Response, _: None = Depends(check_origin)):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


class ProfileBody(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    new_password: Optional[str] = None
    current_password: Optional[str] = None


@router.post("/profile")
def update_profile(body: ProfileBody, user=Depends(current_user), _: None = Depends(check_origin)):
    fields, params = [], []
    for col in ("display_name", "email", "telegram_token", "telegram_chat_id"):
        val = getattr(body, col)
        if val is not None:
            fields.append(f"{col} = ?")
            params.append(val.strip())
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(status_code=400, detail="Şifre en az 8 karakter olmalı")
        if not body.current_password or not security.verify_password(
                body.current_password, user["password_hash"]):
            raise HTTPException(status_code=403, detail="Mevcut şifre doğrulanamadı")
        fields.append("password_hash = ?")
        params.append(security.hash_password(body.new_password))
    if not fields:
        return {"ok": True, "changed": 0}
    params.append(user["id"])
    db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)
    return {"ok": True, "changed": len(fields)}
