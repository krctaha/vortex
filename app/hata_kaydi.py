"""SUNUCU HATA KAYDI — "Internal Server Error" cumlesini bir sebebe cevirir.

NEDEN VAR
---------
Kullanici arayuzde yalnizca "Internal Server Error" goruyordu. O cumle
hicbir sey soylemiyor: hangi uc nokta, hangi istisna, hangi satir.
Sunucu logunda duruyor ama kullanicinin SSH ile journalctl okumasi
gerekiyor — bu, bir arayuz hatasini bildirmenin makul yolu degil.

Daha kotusu gelistirme tarafinda: iki kez kullanicinin sunucusundaki bir
arizayi UZAKTAN TAHMIN etmeye calistim (once onbellek surumu, sonra bu).
Tahmin etmek yerine sistemin kendi arizasini bildirmesi gerekiyordu.

Bu modul son N istisnayi bellekte tutuyor ve teshis panelinde
gosteriyor. Diske yazmiyor: hata kaydi kalici olursa bir sure sonra
kimsenin bakmadigi ikinci bir log dosyasina donusur, ustelik icinde
istem parametreleri tasiyabilir.

GIZLILIK: yalnizca istisna tipi, mesaji, yol ve traceback'in son
satirlari tutuluyor. Istek govdesi, basliklar ve sorgu parametreleri
KAYDEDILMIYOR — orada API anahtari ya da oturum bilgisi olabilir.
"""
from __future__ import annotations

import logging
import time
import traceback
from collections import deque
from typing import Any, Deque, Dict, List

log = logging.getLogger("vortex.hata")

# Bellekte tutulan hata sayisi. Kucuk bilincli: amac bir log arsivi
# degil, "az once ne kirildi" sorusunu cevaplamak.
KAPASITE = 25
_kayitlar: Deque[Dict[str, Any]] = deque(maxlen=KAPASITE)


def kaydet(yol: str, exc: BaseException) -> None:
    """Bir istisnayi kaydet. Cagrilan yerin patlamasina asla izin verme."""
    try:
        iz = traceback.format_exception(type(exc), exc, exc.__traceback__)
        # Traceback'in SONU en bilgilendirici kismi: hatanin atildigi yer.
        kuyruk = "".join(iz)[-1200:]
        _kayitlar.append({
            "ts": int(time.time() * 1000),
            "yol": (yol or "?")[:120],
            "tip": type(exc).__name__,
            "mesaj": str(exc)[:300],
            "iz": kuyruk,
        })
    except Exception:  # noqa: BLE001
        pass


def son(n: int = 10) -> List[Dict[str, Any]]:
    return list(_kayitlar)[-n:][::-1]


def temizle() -> None:
    _kayitlar.clear()
