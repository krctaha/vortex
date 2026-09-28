"""Basit TTL cache - hem async hem sync kullanim."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, Optional, Tuple


class TTLCache:
    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = (time.time() + ttl, value)

    def stale(self, key: str) -> Optional[Any]:
        """Suresi gecmis olsa bile son bilinen degeri dondurur (failover icin)."""
        item = self._store.get(key)
        return item[1] if item else None

    async def wrap(self, key: str, ttl: float, factory: Callable) -> Any:
        hit = self.get(key)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self.get(key)
            if hit is not None:
                return hit
            value = await factory()
            if value is not None:
                self.set(key, value, ttl)
            return value

    def clear(self) -> None:
        self._store.clear()


cache = TTLCache()
