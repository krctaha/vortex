"""Kullanici yonetimi — YALNIZCA yonetici.

GUVENLIK NOTU
-------------
Bu dosyadaki HER uc nokta `admin_user` bagimliligindan geciyor. Arayuzde
menuyu gizlemek yeterli degil: menu gizlense bile uc noktaya dogrudan
istek atilabilir. Yetki kontrolu sunucuda, tek tek her uc noktada.

KILITLER
--------
Yonetici kendini kilitleyebilecegi hicbir islemi yapamaz:
  - kendini silemez
  - kendini pasife alamaz
  - kendi yoneticiligini birakamaz
  - son yoneticiyi silemez / pasife alamaz / dusuremez
Bunlar olmadan tek bir yanlis tikla sisteme girisin tamamen kapanabilir
ve geri almanin arayuzden yolu kalmaz.

SILME vs PASIFLESTIRME
----------------------
Varsayilan PASIFLESTIRME. Silinen kullanicinin islemleri, sinyalleri ve
arastirma kayitlari sahipsiz kalir ve gecmis bozulur. Silme yine mumkun
ama neyin gidecegini onceden sayip soyluyoruz.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, security
from ..deps import admin_user, check_origin

router = APIRouter(prefix="/api/users", tags=["users"])

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,48}$")
PUBLIC_COLUMNS = ("id", "username", "display_name", "email", "role",
                  "avatar", "created_at", "last_login")
USER_TABLES = {"trades":"trades", "signals":"signal_events", "push":"push_subscriptions",
               "cross_manual":"cross_manual", "mentor_trades":"mentor_trades",
               "mentor_reviews":"mentor_reviews", "watchlist":"watchlist",
               "notification_log":"smc_notification_log"}


def _admin_count(exclude_id: Optional[int] = None) -> int:
    """Aktif yonetici sayisi. Pasif yonetici sisteme giremez, sayilmaz."""
    sql = "SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1"
    params: tuple = ()
    if exclude_id is not None:
        sql += " AND id != ?"
        params = (exclude_id,)
    row = db.query_one(sql, params)
    return int(row["c"]) if row else 0


def _get(user_id: int) -> dict:
    row = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    return row


def _public(row) -> dict:
    d = {k: row[k] for k in PUBLIC_COLUMNS if k in row.keys()}
    d["is_active"] = bool(row["is_active"]) if "is_active" in row.keys() else True
    d["telegram_configured"] = bool((row["telegram_token"] or "").strip()
                                    and (row["telegram_chat_id"] or "").strip())
    return d


def _usage(user_id: int) -> dict:
    """Bu kullaniciya bagli kayitlar — silmeden once gosterilir."""
    def count(table: str) -> int:
        try:
            row = db.query_one(f"SELECT COUNT(*) c FROM {table} WHERE user_id = ?", (user_id,))
            return int(row["c"]) if row else 0
        except Exception:  # noqa: BLE001 — tablo yoksa sorun degil
            return 0
    return {key:count(table) for key,table in USER_TABLES.items()}


# --------------------------------------------------------------------------- #
@router.get("")
def list_users(admin=Depends(admin_user)):
    rows = db.query("SELECT * FROM users ORDER BY role='admin' DESC, username")
    items = []
    for r in rows:
        d = _public(r)
        d["is_self"] = r["id"] == admin["id"]
        d["usage"] = _usage(r["id"])
        items.append(d)
    return {"items": items, "admin_count": _admin_count(), "me": admin["id"]}


class CreateBody(BaseModel):
    username: str = Field(min_length=3, max_length=48)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=120)
    role: str = Field(default="user")


@router.post("")
def create_user(body: CreateBody, _admin=Depends(admin_user),
                _: None = Depends(check_origin)):
    username = body.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400,
                            detail="Kullanıcı adı 3-48 karakter olmalı; sadece harf, rakam, nokta, tire ve alt çizgi")
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise HTTPException(status_code=409, detail="Bu kullanıcı adı zaten var")
    role = "admin" if body.role == "admin" else "user"
    uid = db.execute(
        "INSERT INTO users(username, display_name, email, password_hash, role, created_at, is_active) "
        "VALUES(?,?,?,?,?,?,1)",
        (username, (body.display_name or username).strip(), body.email.strip(),
         security.hash_password(body.password), role, db.now_ms()))
    return {"ok": True, "user": _public(_get(uid))}


class RoleBody(BaseModel):
    role: str


@router.post("/{user_id}/role")
def set_role(user_id: int, body: RoleBody, admin=Depends(admin_user),
             _: None = Depends(check_origin)):
    role = "admin" if body.role == "admin" else "user"
    target = _get(user_id)
    if target["id"] == admin["id"] and role != "admin":
        raise HTTPException(status_code=400,
                            detail="Kendi yöneticiliğini bırakamazsın")
    if target["role"] == "admin" and role != "admin" and _admin_count(exclude_id=user_id) == 0:
        raise HTTPException(status_code=400,
                            detail="Sistemde en az bir aktif yönetici kalmalı")
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    return {"ok": True, "user": _public(_get(user_id))}


class ActiveBody(BaseModel):
    is_active: bool


@router.post("/{user_id}/active")
def set_active(user_id: int, body: ActiveBody, admin=Depends(admin_user),
               _: None = Depends(check_origin)):
    target = _get(user_id)
    if target["id"] == admin["id"] and not body.is_active:
        raise HTTPException(status_code=400, detail="Kendi hesabını pasife alamazsın")
    if (not body.is_active and target["role"] == "admin"
            and _admin_count(exclude_id=user_id) == 0):
        raise HTTPException(status_code=400,
                            detail="Sistemde en az bir aktif yönetici kalmalı")
    db.execute("UPDATE users SET is_active = ? WHERE id = ?",
               (1 if body.is_active else 0, user_id))
    return {"ok": True, "user": _public(_get(user_id))}


class PasswordBody(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


@router.post("/{user_id}/password")
def reset_password(user_id: int, body: PasswordBody, _admin=Depends(admin_user),
                   _: None = Depends(check_origin)):
    """Yonetici sifirlamasi — mevcut sifre SORULMAZ.

    Kullanicinin kendi sifresini degistirmesi /api/auth/profile'da ve orada
    mevcut sifre zorunlu. Buradaki fark bilincli: yonetici zaten kullanicinin
    eski sifresini bilmiyor, bilmesi de gerekmiyor.
    """
    _get(user_id)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (security.hash_password(body.new_password), user_id))
    return {"ok": True}


@router.delete("/{user_id}")
def delete_user(user_id: int, purge: bool = False, admin=Depends(admin_user),
                _: None = Depends(check_origin)):
    target = _get(user_id)
    if target["id"] == admin["id"]:
        raise HTTPException(status_code=400, detail="Kendi hesabını silemezsin")
    if target["role"] == "admin" and _admin_count(exclude_id=user_id) == 0:
        raise HTTPException(status_code=400,
                            detail="Sistemde en az bir aktif yönetici kalmalı")
    usage = _usage(user_id)
    total = sum(usage.values())
    if total and not purge:
        # Kayitlari olan kullaniciyi kazara silmeyi engelle: cagiran tarafin
        # neyin gidecegini gorup acikca onaylamasi gerekiyor.
        raise HTTPException(
            status_code=409,
            detail=(f"Bu kullanıcının {usage['trades']} işlemi, {usage['signals']} sinyali ve "
                    f"{usage['push']} bildirim aboneliği ve toplam {total} kişisel kaydı var. Silmek için onay gerekiyor."))
    # One transaction: do not leave a half-deleted account or hidden credentials.
    with db.cursor() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current=conn.execute("SELECT role,is_active FROM users WHERE id=?",(admin["id"],)).fetchone()
        if not current or current["role"]!="admin" or not current["is_active"]:
            raise HTTPException(status_code=403,detail="Yönetici yetkisi gerekli")
        target=conn.execute("SELECT role FROM users WHERE id=?",(user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404,detail="Kullanıcı bulunamadı")
        if target["role"]=="admin" and not conn.execute("SELECT 1 FROM users WHERE role='admin' AND is_active=1 AND id!=?",(user_id,)).fetchone():
            raise HTTPException(status_code=400,detail="Sistemde en az bir aktif yönetici kalmalı")
        tables={r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        actual={key:int(conn.execute(f"SELECT count(*) FROM {table} WHERE user_id=?",(user_id,)).fetchone()[0]) if table in tables else 0 for key,table in USER_TABLES.items()}
        if sum(actual.values()) and not purge:
            raise HTTPException(status_code=409,detail="Kişisel kayıtlar bulundu. Kalıcı silme onayı gerekli.")
        for table in USER_TABLES.values():
            if table in tables:
                conn.execute(f"DELETE FROM {table} WHERE user_id=?",(user_id,))
        for key in (f"smc_notifications_{user_id}",f"smc_notification_state_{user_id}",f"smc_instant_since_{user_id}"):
            conn.execute("DELETE FROM app_settings WHERE key=?",(key,))
        conn.execute("DELETE FROM users WHERE id=?",(user_id,))
        usage=actual
    return {"ok": True, "deleted": usage}
