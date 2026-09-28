"""Coin ikonlari — yerel onbellek + garantili yedek.

TASARIM
-------
Ikonlar CALISMA ANINDA disaridan cekilmez. Sebep: CoinGecko gibi kaynaklar
hiz siniri uygular, yavastir ve bir gun kapanabilir; her sembol icin canli
istek atmak arayuzu onlarin ayakta olmasina bagimli kilar.

Bunun yerine iki katman var:
  1. Yerel onbellek — `refresh()` bir kez calisir, PNG'leri diske indirir.
     Arayuz her zaman kendi sunucundan okur.
  2. Yedek monogram — onbellekte olmayan HER sembol icin, sembol adindan
     TURETILMIS renkte bir SVG rozet uretilir. Ag yok, gecikme yok, ve
     hicbir coin "kirik resim" gostermez.

Yedegin renkleri sembolun hash'inden gelir: ayni coin her zaman ayni rengi
alir, farkli coinler birbirinden ayrilir. Rastgele degil, deterministik.
"""
from __future__ import annotations

import asyncio
import colorsys
import hashlib
import json
import logging
import os
import re
from html import escape
from typing import Dict, List, Optional

import httpx

from ..config import BASE_DIR, settings

log = logging.getLogger("vortex.icons")

ICON_DIR = BASE_DIR / "static" / "img" / "coins"
MAP_FILE = ICON_DIR / "_map.json"
SAFE_NAME = re.compile(r"^[A-Z0-9]{1,20}$")

# Binance sembolunden temel varliga inerken kirpilacak ekler.
QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
# Binance bazi dusuk fiyatli coinleri 1000x carpanla listeler (1000PEPEUSDT).
MULTIPLIER = re.compile(r"^(1000+|1M)")

# CoinGecko sembolu ile Binance temel varligi ayrismasi olan bilinen durumlar.
ALIASES = {
    "XBT": "BTC", "MIOTA": "IOTA", "WETH": "ETH", "WBTC": "BTC",
}


def base_asset(symbol: str) -> str:
    """BTCUSDT -> BTC, 1000PEPEUSDT -> PEPE, XAUUSDT -> XAU"""
    s = (symbol or "").upper().strip()
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q):
            s = s[: -len(q)]
            break
    s = MULTIPLIER.sub("", s)
    return ALIASES.get(s, s)


# --------------------------------------------------------------------------- #
# Yedek: monogram rozeti
# --------------------------------------------------------------------------- #
def monogram_svg(symbol: str, size: int = 64) -> str:
    """Sembolden turetilmis renkte SVG rozet. Ag erisimi yok."""
    base = base_asset(symbol) or "?"
    text = base[:4] if len(base) <= 4 else base[:3]
    digest = hashlib.md5(base.encode()).digest()

    # Ton hash'ten; doygunluk ve parlaklik sabit -> her rozet okunabilir kaliyor
    # ve koyu temada bogulmuyor. Rastgele renk secmek bunu garanti etmezdi.
    hue = digest[0] / 255.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.92)
    bg = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
    r2, g2, b2 = colorsys.hsv_to_rgb(hue, 0.75, 0.42)
    ring = f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"

    font = 26 if len(text) <= 2 else 20 if len(text) == 3 else 16
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
        f'width="{size}" height="{size}" role="img" aria-label="{escape(base)}">'
        f'<circle cx="32" cy="32" r="31" fill="{bg}" stroke="{ring}" stroke-width="2"/>'
        f'<text x="32" y="32" fill="#0a0c0f" font-family="Inter,Segoe UI,sans-serif" '
        f'font-size="{font}" font-weight="700" text-anchor="middle" '
        f'dominant-baseline="central" letter-spacing="-0.5">{escape(text)}</text>'
        f"</svg>"
    )


# --------------------------------------------------------------------------- #
# Yerel onbellek
# --------------------------------------------------------------------------- #
def cached_path(symbol: str) -> Optional[str]:
    base = base_asset(symbol)
    if not SAFE_NAME.match(base):          # dizin gezinmesine karsi
        return None
    path = ICON_DIR / f"{base}.png"
    return str(path) if path.exists() else None


def cache_stats() -> Dict[str, object]:
    if not ICON_DIR.exists():
        return {"count": 0, "dir": str(ICON_DIR), "updated": None}
    files = [f for f in os.listdir(ICON_DIR) if f.endswith(".png")]
    updated = None
    if MAP_FILE.exists():
        try:
            updated = json.loads(MAP_FILE.read_text()).get("updated")
        except Exception:  # noqa: BLE001
            pass
    return {"count": len(files), "dir": str(ICON_DIR), "updated": updated}


async def refresh(symbols: Optional[List[str]] = None, pages: int = 3) -> Dict[str, object]:
    """CoinGecko'dan ikonlari indirip diske yazar. Tek seferlik/periyodik.

    pages=3 -> piyasa degerine gore ilk 750 coin. Binance perp evreni bunun
    icinde fazlasiyla kaliyor.
    """
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    wanted = {base_asset(s) for s in (symbols or [])} or None

    headers = {"User-Agent": settings.USER_AGENT, "Accept": "application/json"}
    if settings.coingecko_key:
        headers["x_cg_demo_api_key"] = settings.coingecko_key

    found: Dict[str, str] = {}
    downloaded = failed = 0

    async with httpx.AsyncClient(timeout=25.0, headers=headers, follow_redirects=True) as client:
        for page in range(1, pages + 1):
            try:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={"vs_currency": "usd", "order": "market_cap_desc",
                            "per_page": 250, "page": page, "sparkline": "false"},
                )
                resp.raise_for_status()
                rows = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("CoinGecko sayfa %s alinamadi: %s", page, exc)
                break

            for row in rows:
                sym = str(row.get("symbol", "")).upper()
                sym = ALIASES.get(sym, sym)
                image = row.get("image")
                if not sym or not image or sym in found:
                    continue
                if wanted is not None and sym not in wanted:
                    continue
                if not SAFE_NAME.match(sym):
                    continue
                found[sym] = image
            await asyncio.sleep(1.5)       # hiz limitine nazik davran

        sem = asyncio.Semaphore(6)

        async def grab(sym: str, url: str) -> bool:
            target = ICON_DIR / f"{sym}.png"
            if target.exists():
                return True
            async with sem:
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    if len(r.content) > 400_000:      # bozuk/asiri buyuk dosya
                        return False
                    target.write_bytes(r.content)
                    return True
                except Exception:  # noqa: BLE001
                    return False

        results = await asyncio.gather(*(grab(s, u) for s, u in found.items()))
        downloaded = sum(1 for x in results if x)
        failed = len(results) - downloaded

    MAP_FILE.write_text(json.dumps(
        {"updated": __import__("time").strftime("%Y-%m-%d %H:%M"),
         "symbols": sorted(found)}, ensure_ascii=False))
    log.info("Coin ikonlari: %s indirildi, %s basarisiz", downloaded, failed)
    return {"found": len(found), "downloaded": downloaded, "failed": failed,
            **cache_stats()}
