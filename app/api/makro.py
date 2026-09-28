"""MAKRO VARLIKLAR — Nasdaq, altın, gümüş, dolar endeksi, petrol.

Salt okunur. Emir, plan ya da sinyal üretmiyor: kullanıcının planı
"buna ayrı bir motor yazacağız" ve o motor bu veri katmanının üzerine
oturacak. Şimdiden karar mantığı karıştırmak, motoru yazarken hangi
kararın nereden geldiğini bulunamaz hale getirirdi.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import current_user
from ..services import makro

router = APIRouter(prefix="/api/makro", tags=["makro"])


@router.get("/ozet")
async def makro_ozet(taze: bool = False, _user=Depends(current_user)):
    return await makro.ozet(taze=taze)


@router.get("/teshis")
async def makro_teshis(_user=Depends(current_user)):
    """Hangi kaynak cevap veriyor, hangisi neden vermiyor."""
    return await makro.teshis()
