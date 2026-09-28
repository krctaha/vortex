"""Kullanicinin panelden dogrulayabildigi uygulama surumu.

BUILD_ID ARTIK ELLE YAZILMIYOR — ve bunun somut bir sebebi var.

OLCULMUS HATA (08.09):
BUILD_ID sabit bir metindi ("20260905.4") ve her dagitimda elle
artirilmasi gerekiyordu. Sablonlar statik dosyalari
"/static/js/workspace.js?v={{ build_id }}" diye cagiriyor; yani o metin
degismedigi surece TARAYICI ESKI DOSYAYI ONBELLEKTEN SERVIS EDIYOR.

Uc gun boyunca sunucuya yeni JavaScript yuklendi ama kullanicinin
tarayicisina hicbiri ULASMADI: sayfa gecisi yeniden yazildi, kullanici
hala tam sayfa yenilemesi goruyordu ve hakli olarak "hala refresh atiyor"
dedi. Kod dogruydu, dagitim dogruydu, ulasmayan sey dosyanin kendisiydi.

Elle artirilan bir surum numarasi er ya da gec unutulur. Bu yuzden artik
statik dosyalarin ve sablonlarin GERCEK durumundan (yol, boyut, degisim
zamani) hesaplaniyor: bir dosya degistiyse BUILD_ID degisir, degismediyse
ayni kalir. Unutulacak bir adim kalmiyor.

APP_VERSION yalnizca kullaniciya gorunen ozellik degistiginde elle
yukseltilir; o bir surum ETIKETI, onbellek anahtari degil.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger("vortex.version")

APP_VERSION = "5.0.0-rc11"
BUILD_DATE = "28 Eylül 2026 · grafik odaklı terminal düzeni"

# Yedek deger: imza hesaplanamazsa (izin hatasi, beklenmedik dosya sistemi)
# hicbir sey patlamasin, en azindan surum yazsin.
_YEDEK = "20260908.0"

_KOK = Path(__file__).resolve().parent.parent
# Onbellek anahtarini etkileyen her sey: tarayiciya giden dosyalar.
_IZLENEN = (
    ("static", (".js", ".css")),
    ("templates", (".html",)),
)


def _imza() -> str:
    """Tarayiciya giden dosyalarin durumundan kisa bir imza uretir.

    Icerigi degil, (yol, boyut, mtime) uclusunu hasliyoruz: 100+ dosyayi
    her acilista okumak gereksiz, bu ucluler degisiklikleri yakalamak icin
    fazlasiyla yeterli.
    """
    h = hashlib.sha1()
    try:
        for klasor, uzantilar in _IZLENEN:
            kok = _KOK / klasor
            if not kok.is_dir():
                continue
            for yol in sorted(kok.rglob("*")):
                if not yol.is_file() or yol.suffix.lower() not in uzantilar:
                    continue
                try:
                    st = yol.stat()
                except OSError:
                    continue
                h.update(str(yol.relative_to(_KOK)).encode("utf-8"))
                h.update(str(st.st_size).encode("ascii"))
                h.update(str(int(st.st_mtime)).encode("ascii"))
        return h.hexdigest()[:10]
    except Exception as exc:  # noqa: BLE001
        log.warning("BUILD_ID imzasi hesaplanamadi (%s); yedek kullaniliyor", exc)
        return ""


def _build_id() -> str:
    # Ortam degiskeni her seyi ezer: CI ya da elle sabitleme gerekirse.
    zorla = os.getenv("VORTEX_BUILD_ID")
    if zorla:
        return zorla.strip()
    imza = _imza()
    return f"{_YEDEK}-{imza}" if imza else _YEDEK


BUILD_ID = _build_id()
