from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import runtime
from ..deps import admin_user, check_origin, current_user

router = APIRouter(prefix="/api/runtime-settings", tags=["settings"])


class SettingsBody(BaseModel):
    settings: Dict[str, Any]


@router.get("")
def read_settings(_user=Depends(current_user)):
    return runtime.get()


@router.post("")
def write_settings(body: SettingsBody, _user=Depends(admin_user),
                   _: None = Depends(check_origin)):
    return {"ok": True, "settings": runtime.save(body.settings)}
