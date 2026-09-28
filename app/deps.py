"""Ortak bagimliliklar: oturum, kullanici, CSRF."""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from . import db, security

COOKIE_NAME = "vortex_session"


def current_session(request: Request) -> Optional[Dict[str, Any]]:
    return security.read_session(request.cookies.get(COOKIE_NAME))


def current_user(request: Request) -> Dict[str, Any]:
    sess = current_session(request)
    if not sess:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Oturum yok")
    user = db.query_one("SELECT * FROM users WHERE id = ?", (sess.get("uid"),))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı bulunamadı")
    # Pasiflestirme oturumu ANINDA kesmeli. Yalnizca girise bakip burada
    # kontrol etmezsek, elinde gecerli cerezi olan pasif kullanici cerez
    # suresi boyunca (varsayilan gunlerce) sistemi kullanmaya devam eder.
    if "is_active" in user.keys() and not int(user["is_active"] or 0):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Hesap pasif durumda")
    return user


def admin_user(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Yönetici yetkisi gerekli")
    return user


def check_origin(request: Request) -> None:
    """Basit CSRF korumasi: durum degistiren isteklerde Origin/Referer ayni host olmali."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not origin:
        return
    host = request.headers.get("host", "")
    parsed=urlsplit(origin)
    if host and (parsed.scheme not in ('http','https') or parsed.netloc.lower()!=host.lower()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin doğrulaması başarısız")


def user_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS c FROM users")
    return int(row["c"]) if row else 0
