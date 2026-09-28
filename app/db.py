"""SQLite katmani. Harici ORM yok - sema acik, migrasyon basit."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    telegram_token   TEXT NOT NULL DEFAULT '',
    telegram_chat_id TEXT NOT NULL DEFAULT '',
    binance_key      TEXT NOT NULL DEFAULT '',
    binance_secret   TEXT NOT NULL DEFAULT '',
    avatar        TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    last_login    INTEGER
);

CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,               -- LONG | SHORT
    status       TEXT NOT NULL DEFAULT 'open',-- candidate | open | closed | cancelled
    mode         TEXT NOT NULL DEFAULT 'paper',-- paper | live
    entry        REAL NOT NULL,
    stop         REAL,
    take_profit  REAL,
    margin_usdt  REAL NOT NULL DEFAULT 60,
    leverage     INTEGER NOT NULL DEFAULT 4,
    margin_type  TEXT NOT NULL DEFAULT 'ISOLATED',
    qty          REAL,
    risk_usdt    REAL,
    exit_price   REAL,
    pnl_usdt     REAL,
    r_multiple   REAL,
    opened_at    INTEGER NOT NULL,
    closed_at    INTEGER,
    interval     TEXT NOT NULL DEFAULT '15m',
    note         TEXT NOT NULL DEFAULT '',
    meta         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_trades_user_status ON trades(user_id, status);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oi_history (
    symbol    TEXT NOT NULL,
    ts        INTEGER NOT NULL,
    oi_value  REAL NOT NULL,
    price     REAL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS analysis_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT NOT NULL,
    interval   TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    price      REAL NOT NULL,
    regime     TEXT,
    bias       TEXT,
    findings   TEXT NOT NULL DEFAULT '{}',
    outcome_1h REAL,
    outcome_4h REAL,
    outcome_24h REAL,
    resolved   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_symbol ON analysis_memory(symbol, ts);

CREATE TABLE IF NOT EXISTS mentor_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    symbol      TEXT NOT NULL,
    interval    TEXT NOT NULL,
    direction   TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    price       REAL NOT NULL,
    entry_price REAL,
    stop_price  REAL,
    target_price REAL,
    reward_risk REAL,
    created_at  INTEGER NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_mentor_reviews_user_created
    ON mentor_reviews(user_id, created_at DESC);

-- MENTOR GUNLUGU — kullanicinin KENDI islemleri.
--
-- mentor_reviews ile karistirmayin:
--   mentor_reviews -> mentorun VERDIGI tavsiye (karar oncesi)
--   mentor_trades  -> kullanicinin GERCEKTEN yaptigi islem (karar sonrasi)
-- Ikinci tablo olmadan birincisinin isabetli olup olmadigi hic olculemez.
--
-- initial_stop bilerek ayri kolon ve ASLA guncellenmiyor. Ev yapimi
-- gunluklerin en sik kendini kandirma bicimi, stop tasindiginda R'yi
-- yeniden hesaplamaktir: oyle olunca her stop tasima istatistigi
-- guzellestirir. Giristeki risk donduruluyor.
CREATE TABLE IF NOT EXISTS mentor_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    review_id INTEGER,                 -- varsa ilgili mentor_reviews kaydi
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,
    entry REAL NOT NULL,
    -- NULL OLABILIR: Binance gecmisinden aktarilan islemlerde stop yoktur.
    -- Oraya giris fiyatini yazmak veriyi kirletirdi (0 risk = uydurma R).
    -- Stop yoksa NULL kalir ve result_r de NULL olur; R tabanli hicbir
    -- istatistik o satiri saymaz.
    initial_stop REAL,                 -- DONDURULMUS, elle girilenlerde zorunlu
    initial_target REAL,
    qty REAL,
    leverage INTEGER,
    exit_price REAL,
    exit_reason TEXT,
    gerekce TEXT,
    etiket TEXT,
    kural_uyumu INTEGER,
    kontrol_listesi TEXT,
    baglam TEXT,
    result_r REAL,
    pnl_usdt REAL,
    mfe_r REAL,
    mae_r REAL,
    updraw_pct REAL,
    drawdown_pct REAL,
    exit_efficiency REAL,
    bayraklar TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mentor_trades_user_status
    ON mentor_trades(user_id, status);
CREATE INDEX IF NOT EXISTS idx_mentor_trades_closed
    ON mentor_trades(user_id, closed_at);

CREATE TABLE IF NOT EXISTS signal_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    interval    TEXT NOT NULL,
    side        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'candidate',
    score       INTEGER NOT NULL DEFAULT 0,
    entry       REAL NOT NULL,
    stop        REAL NOT NULL,
    targets     TEXT NOT NULL DEFAULT '[]',
    reasons     TEXT NOT NULL DEFAULT '[]',
    created_at  INTEGER NOT NULL,
    sent_at     INTEGER,
    user_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signal_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signal_events(symbol, interval, created_at DESC);

CREATE TABLE IF NOT EXISTS research_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    interval    TEXT NOT NULL,
    side        TEXT NOT NULL,
    score       INTEGER NOT NULL,
    entry       REAL NOT NULL,
    stop        REAL NOT NULL,
    target      REAL NOT NULL,
    reasons     TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    closed_at   INTEGER,
    exit_price  REAL,
    result_r    REAL,
    gross_result_r REAL,
    cost_r      REAL,
    net_result_r REAL,
    outcome     TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_status ON research_trades(status, created_at DESC);

-- V4: Her kararın rejim yönlendiricisi tarafından o anda nasıl okunduğu.
-- Champion davranışını değiştirmeden challenger sonucunu aynı işlemin
-- gerçekleşen net sonucu üzerinden karşılaştırmaya yarar.
CREATE TABLE IF NOT EXISTS decision_audit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id    INTEGER UNIQUE,
    strategy       TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL,
    regime         TEXT NOT NULL,
    route          TEXT NOT NULL,
    router_allowed INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL,
    details        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_decision_audit_created ON decision_audit(created_at DESC);

-- V4 noktasal özellik deposu. Bir satır yalnız bar kapandıktan sonra
-- yazılır; gelecek getiriler sonradan doldurulur. Böylece model eğitiminde
-- "geleceği görme" hatası veri katmanında engellenir.
CREATE TABLE IF NOT EXISTS market_feature_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    interval         TEXT NOT NULL,
    bar_time         INTEGER NOT NULL,
    captured_at      INTEGER NOT NULL,
    price            REAL NOT NULL,
    quote_volume_24h REAL,
    regime           TEXT,
    features         TEXT NOT NULL DEFAULT '{}',
    forward_1h_pct   REAL,
    forward_4h_pct   REAL,
    forward_12h_pct  REAL,
    labeled_at       INTEGER,
    UNIQUE (symbol, interval, bar_time)
);
CREATE INDEX IF NOT EXISTS idx_features_time ON market_feature_snapshots(bar_time DESC);
CREATE INDEX IF NOT EXISTS idx_features_unlabeled ON market_feature_snapshots(labeled_at, bar_time);

-- RSI radar kararlarinin KENDI KARNESI.
-- Bir siniflandirici kurup "iyi calisiyor" demek kolaydir; kaydini
-- tutmadan bunu kimse bilemez. Her karar buraya yaziliyor ve N bar
-- sonra fiyata bakilip isabet edip etmedigi isaretleniyor. Panel bu
-- tablodan besleniyor: "tukenme dediklerimizin yuzde kaci gercekten
-- dondu" sorusunun cevabi tahmin degil sayim.
CREATE TABLE IF NOT EXISTS rsi_calls (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol       TEXT NOT NULL,
  interval     TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  evaluate_at  INTEGER NOT NULL,
  rsi          REAL,
  zone         TEXT,
  verdict      TEXT,
  score        REAL,
  price        REAL,
  components   TEXT,
  evaluated_at INTEGER,
  price_after  REAL,
  forward_pct  REAL,
  correct      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rsi_calls_open ON rsi_calls(evaluated_at, evaluate_at);
CREATE INDEX IF NOT EXISTS idx_rsi_calls_sym ON rsi_calls(symbol, created_at);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    user_agent  TEXT DEFAULT '',
    created_at  INTEGER NOT NULL,
    last_ok     INTEGER,
    fail_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_id);

-- =====================================================================
-- PIYASA KUTUPHANESI (market_library / market_history)
--
-- NEDEN VAR
-- Onceki tarama mimarisi sunu yapiyordu: hacme gore ilk N sembolu al,
-- her birine PAHALI derin analiz uygula. N kucuk olmak zorundaydi cunku
-- derin analiz sembol basina ~10 agirlik harciyor ve dakikalik butce 2400.
-- Sonuc: ~500 surekli kontratin yalnizca 40-120 tanesi goruluyordu ve
-- gorulenler HEP AYNI en likit coinlerdi. Hareket kucuk bir coinde
-- basladiginda radara hic girmiyordu.
--
-- Bu iki tablo ucuz katmani tutuyor. /fapi/v1/ticker/24hr sembolsuz
-- cagrildiginda TEK istekte (agirlik 40) BUTUN sembolleri getiriyor.
-- Yani tam kapsama zaten neredeyse bedava; eksik olan sey onu SAKLAMAK ve
-- zaman icinde karsilastirabilmekti.
--
-- market_library : her sembolun SON durumu (upsert, ~500 satir)
-- market_history : periyodik anlik goruntuler (sembol basina saatlik)
--
-- Tarih neden gerekli: Binance'in verdigi 24s degisim mutlak bir sayi.
-- "Hacim normalin 4 katina cikti" gibi bir sey soylemek icin sembolun
-- KENDI normaline ihtiyac var. Bu, sabit esiklerin yapamadigi seydir:
-- 5 milyon dolarlik hacim BTC icin olu, yeni bir kontrat icin patlama.
-- =====================================================================
CREATE TABLE IF NOT EXISTS market_library (
    symbol        TEXT PRIMARY KEY,
    updated_at    INTEGER NOT NULL,
    price         REAL,
    change_pct    REAL,
    quote_volume  REAL,
    high          REAL,
    low           REAL,
    open_price    REAL,
    weighted_avg  REAL,
    trade_count   INTEGER,
    band_pos      REAL,
    vol_ratio     REAL,
    mom_1h        REAL,
    mom_4h        REAL,
    sikisma       REAL,
    ilgi          REAL
);
CREATE INDEX IF NOT EXISTS idx_mlib_ilgi ON market_library(ilgi DESC);
CREATE INDEX IF NOT EXISTS idx_mlib_hacim ON market_library(quote_volume DESC);

CREATE TABLE IF NOT EXISTS market_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    price        REAL,
    change_pct   REAL,
    quote_volume REAL
);
CREATE INDEX IF NOT EXISTS idx_mhist_sym ON market_history(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_mhist_ts ON market_history(ts);

CREATE TABLE IF NOT EXISTS watchlist (
    user_id INTEGER NOT NULL,
    symbol  TEXT NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, symbol)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


@contextmanager
def cursor():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# (tablo, kolon, tip) — mevcut kurulumlara sonradan eklenen kolonlar.
# CREATE TABLE IF NOT EXISTS calisan bir veritabanini degistirmez, bu yuzden
# yeni kolonlar burada tek tek kontrol edilip ekleniyor.
log = logging.getLogger("vortex.db")

MIGRATIONS = [
    ("research_trades", "last_checked_at", "INTEGER"),
    ("research_trades", "mfe_r", "REAL"),
    ("research_trades", "mae_r", "REAL"),
    ("research_trades", "exit_reason", "TEXT"),
    # Skorun hangi bilesenlerinden olustugu. Toplam skor tek basina hangi
    # bilesenin para kazandirdigini gizliyor; bunlar kaydedilirse sistem
    # CANLI veriyle de bilesen analizi yapilabilir hale geliyor — backtest
    # gecmise bakar, bu ileri dogru birikir.
    ("research_trades", "components", "TEXT"),
    ("signal_events", "components", "TEXT"),
    # Kaydi hangi motor uretti: 'score' (eski, gosterge skoru) veya 'tsmom'.
    # Ikisi paralel calisiyor ve ayni tabloya yaziyor; kaynak ayrimi olmadan
    # karsilastirilamazlar. Varsayilan 'score' cunku mevcut TUM kayitlar ondan.
    ("research_trades", "source", "TEXT DEFAULT 'score'"),
    # Motora ozgu teshis (TSMOM icin t degeri, fonlama, maliyet kalemleri).
    ("research_trades", "meta", "TEXT"),
    # V4 net muhasebe. result_r geriye uyumluluk için korunur; yeni karne
    # fiyat hareketini, tahmini maliyeti ve net sonucu ayrı sütunlarda tutar.
    ("research_trades", "gross_result_r", "REAL"),
    ("research_trades", "cost_r", "REAL"),
    ("research_trades", "net_result_r", "REAL"),
    # Kullanici pasife alinabilsin. Silmek yerine pasiflestirmek tercih
    # ediliyor: silinen kullanicinin islemleri ve sinyalleri sahipsiz kalir.
    ("users", "is_active", "INTEGER NOT NULL DEFAULT 1"),
]


def _sema_kolonlari() -> Dict[str, set]:
    """SCHEMA metnindeki CREATE TABLE bloklarindan (tablo -> kolon adlari)."""
    out: Dict[str, set] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\s*\)",
                         SCHEMA, re.S):
        tablo, govde = m.group(1), m.group(2)
        kolonlar = set()
        for satir in govde.split("\n"):
            satir = satir.strip().rstrip(",")
            if not satir or satir.startswith("--"):
                continue
            ilk = satir.split()[0]
            if ilk.upper() in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"):
                continue
            kolonlar.add(ilk)
        out[tablo] = kolonlar
    return out


def init() -> None:
    with cursor() as conn:
        conn.executescript(SCHEMA)
        for table, column, coltype in MIGRATIONS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

        # 29.08 — SESSIZ SEMA KAYMASI KORUMASI
        # MIGRATIONS elle tutulan bir liste. SCHEMA'daki CREATE TABLE blokuna
        # kolon eklerken bu listeye eklemeyi unutmak hicbir uyari vermiyordu:
        # yeni kurulumda calisiyor, mevcut sunucuda calisma aninda
        # "no such column" ile patliyordu. Artik acilista yuksek sesle soyluyor.
        eksik = []
        for tablo, kolonlar in _sema_kolonlari().items():
            canli = {r["name"] for r in conn.execute(f"PRAGMA table_info({tablo})")}
            if not canli:
                continue
            eksik += [f"{tablo}.{k}" for k in kolonlar - canli]
        if eksik:
            log.error("SEMA KAYMASI — su kolonlar SCHEMA'da var ama veritabaninda YOK: %s. "
                      "app/db.py icindeki MIGRATIONS listesine eklenmeleri gerekiyor.",
                      ", ".join(sorted(eksik)))


def query(sql: str, params: Iterable = ()) -> List[Dict[str, Any]]:
    with cursor() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable = ()) -> int:
    with cursor() as conn:
        cur = conn.execute(sql, tuple(params))
        return cur.lastrowid


def executemany(sql: str, rows: Iterable[Iterable]) -> int:
    """Tek transaction içinde mekanik toplu yazım."""
    payload = [tuple(row) for row in rows]
    if not payload:
        return 0
    with cursor() as conn:
        conn.executemany(sql, payload)
    return len(payload)


def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    execute("INSERT INTO app_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)))


def now_ms() -> int:
    return int(time.time() * 1000)
