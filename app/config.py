"""VORTEX yapılandırma — tek kaynak. Her ayar .env'den okunur."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on", "evet")


class Settings:
    # --- çekirdek ---
    secret_key: str = _get("VORTEX_SECRET_KEY") or secrets.token_urlsafe(48)
    host: str = _get("VORTEX_HOST", "0.0.0.0")
    port: int = int(_get("VORTEX_PORT", "8000"))
    data_mode: str = _get("VORTEX_DATA_MODE", "auto").lower()
    db_path: Path = DATA_DIR / "vortex.db"

    # --- telegram ---
    telegram_token: str = _get("VORTEX_TELEGRAM_TOKEN")
    telegram_chat_id: str = _get("VORTEX_TELEGRAM_CHAT_ID")

    # --- binance ---
    binance_key: str = _get("VORTEX_BINANCE_KEY")
    binance_secret: str = _get("VORTEX_BINANCE_SECRET")
    binance_testnet: bool = _get_bool("VORTEX_BINANCE_TESTNET", True)
    coingecko_key: str = _get("VORTEX_COINGECKO_KEY")
    # VAPID "sub" claim'i icin gecerli bir iletisim adresi gerekiyor.
    # Push servisleri (Apple, Google) sorun olursa buraya yaziyor.
    push_contact: str = _get("VORTEX_PUSH_CONTACT", "vortex@localhost")
    # PWA'nin kullandigi genel adres (HTTPS). Bos ise istekten turetilir.
    public_url: str = _get("VORTEX_PUBLIC_URL", "").rstrip("/")

    # --- risk ---
    total_margin: float = _get_float("VORTEX_TOTAL_MARGIN", 380.0)
    max_position_margin: float = _get_float("VORTEX_MAX_POSITION_MARGIN", 60.0)
    max_risk_per_trade: float = _get_float("VORTEX_MAX_RISK_PER_TRADE", 6.0)
    default_leverage: int = int(_get_float("VORTEX_DEFAULT_LEVERAGE", 4))

    # --- sabitler ---
    FAPI = "https://fapi.binance.com"
    # Binance 2026 Futures WebSocket gecisi: eski /stream yolu yerine
    # /public/stream kullaniliyor. Kökü burada tutup stream yolunu istemcide
    # ekliyoruz; böylece URL tek yerde ve açıkça sürümleniyor.
    FSTREAM = "wss://fstream.binance.com/public"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    # Ana panelde sabit izlenen semboller
    HERO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUUSDT"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
