"""Telegram gonderimi.

- parse_mode HTML (MarkdownV2'de 18 karakter kacirmak gerekir, fiyat metninde
  her mesaj patlar).
- Grup limiti 20 msg/dk: token-bucket kuyrugu.
- 429 -> parameters.retry_after kadar bekle (blind retry sureyi uzatir).
- 4096 karakter limiti -> 3900'de guvenli bolme.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings

log = logging.getLogger("vortex.telegram")

MAX_LEN = 3900
GROUP_PER_MIN = 20

_sent_times: List[float] = []
_lock = asyncio.Lock()


def esc(text: Any) -> str:
    """HTML parse_mode icin guvenli kacirma."""
    return html.escape(str(text), quote=False)


def configured(token: str = "", chat_id: str = "") -> bool:
    return bool((token or settings.telegram_token) and (chat_id or settings.telegram_chat_id))


async def _throttle() -> None:
    now = time.time()
    while _sent_times and now - _sent_times[0] > 60:
        _sent_times.pop(0)
    if len(_sent_times) >= GROUP_PER_MIN:
        wait = 60 - (now - _sent_times[0]) + 0.5
        log.info("Telegram kuyrugu dolu, %.1fs bekleniyor", wait)
        await asyncio.sleep(max(wait, 0))
    _sent_times.append(time.time())


def _split(text: str) -> List[str]:
    """Once satir siniri, olmazsa sert kesim. Hicbir parca MAX_LEN'i asmaz."""
    if len(text) <= MAX_LEN:
        return [text]
    parts: List[str] = []
    buf = ""
    for line in text.split("\n"):
        # Tek basina cok uzun satir: sert kes
        while len(line) > MAX_LEN:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:MAX_LEN])
            line = line[MAX_LEN:]
        if not buf:
            buf = line
        elif len(buf) + 1 + len(line) > MAX_LEN:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}"
    if buf:
        parts.append(buf)
    return [p for p in parts if p]


async def send(text: str, token: str = "", chat_id: str = "",
               disable_preview: bool = True, silent: bool = False) -> Dict[str, Any]:
    token = token or settings.telegram_token
    chat_id = chat_id or settings.telegram_chat_id
    if not token or not chat_id:
        return {"ok": False, "error": "Telegram token veya chat_id tanımlı değil (Ayarlar)"}

    chunks = _split(text)
    results = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for chunk in chunks:
            async with _lock:
                await _throttle()
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": disable_preview},
                "disable_notification": silent,
            }
            for attempt in range(3):
                try:
                    r = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
                    data = r.json()
                except Exception as exc:  # noqa: BLE001
                    results.append({"ok": False, "error": f"Telegram bağlantı hatası ({type(exc).__name__}). Yeniden deneyin."})
                    break
                if data.get("ok"):
                    results.append({"ok": True, "message_id": data["result"]["message_id"]})
                    break
                if data.get("error_code") == 429:
                    wait = int(data.get("parameters", {}).get("retry_after", 5))
                    log.warning("Telegram 429, %ss bekleniyor", wait)
                    await asyncio.sleep(min(wait, 60))
                    continue
                results.append({"ok": False, "error": data.get("description", "bilinmeyen hata")})
                break
            else:
                results.append({"ok": False, "error": "3 denemede gönderilemedi (429)"})

    ok = all(r.get("ok") for r in results)
    return {"ok": ok, "parts": len(chunks), "results": results,
            "error": next((r["error"] for r in results if not r.get("ok")), None)}


async def test_connection(token: str = "", chat_id: str = "") -> Dict[str, Any]:
    token = token or settings.telegram_token
    if not token:
        return {"ok": False, "error": "Token yok"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Telegram bağlantı hatası ({type(exc).__name__})."}
    if not data.get("ok"):
        return {"ok": False, "error": data.get("description", "getMe basarisiz")}
    bot = data["result"]
    return {"ok": True, "bot": bot.get("username"), "name": bot.get("first_name"),
            "chat_id_set": bool(chat_id or settings.telegram_chat_id)}
