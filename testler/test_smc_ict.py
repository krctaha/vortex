"""Research-engine invariants: time availability and no money authority."""
import time
import numpy as np
import pytest
from app.services import demo, premium_engine, smc_ict_engine as smc


def bar(t, close=100):
    return [t, close, close + 1, close - 1, close, 10, t + 999, 1000, 1, 5, 500, 0]


def test_open_and_future_candles_are_excluded():
    now = int(time.time() * 1000)
    rows = [bar(now - 5000), bar(now), bar(now + 5000)]
    assert smc._closed_rows(rows) == rows[:1]


@pytest.mark.parametrize("change", ["nan", "reversed", "missing_time", "duplicate"])
def test_bad_candles_fail_closed(change):
    rows = [bar(1000), bar(3000)]
    if change == "nan":
        rows[0][4] = float("nan")
    elif change == "reversed":
        rows[0][2] = 50
    elif change == "missing_time":
        rows[0] = rows[0][:6]
    else:
        rows[1][0] = 1000
    with pytest.raises(ValueError):
        smc._closed_rows(rows)


@pytest.mark.parametrize("direction", ["bull", "bear"])
def test_displacement_requires_directional_body_not_only_wick(direction):
    o = np.full(24, 100.0)
    h, l, c, atr = o + 1, o - 1, o.copy(), np.full(24, 2.0)
    if direction == "bull":
        h[22], c[22] = 104, 103.5
    else:
        l[22], c[22] = 96, 96.5
    found = smc.find_displacement(o, h, l, c, atr, direction, 20)
    assert found.index == 22
    c[22] = 100.1
    assert smc.find_displacement(o, h, l, c, atr, direction, 20) is None


def test_cisd_needs_close_beyond_opposing_open():
    o = np.array([100., 99., 98., 99.])
    c = np.array([99., 98., 99., 99.5])
    assert smc.find_cisd(o, c, "bull", 2).index == 3
    c[3] = 99.
    assert smc.find_cisd(o, c, "bull", 2) is None


def test_htf_future_bars_cannot_change_past_ltf_decision():
    htf = demo.klines("BTCUSDT", "4h", 260)
    ltf = demo.klines("BTCUSDT", "15m", 500)[:-40]
    cutoff = ltf[-1][6]
    visible = [r for r in htf if r[6] <= cutoff]
    result = smc.analyze_rows("BTCUSDT", htf, ltf)
    assert result == smc.analyze_rows("BTCUSDT", visible, ltf)


def test_flat_market_does_not_invent_bias_or_target():
    rows = [bar(i * 1000) for i in range(150)]
    result = smc.analyze_rows("FLATUSDT", rows, rows)
    assert result["side"] == "WAIT"
    assert result["ready"] is False
    assert result["target"] is None
    assert "confidence" not in result
    assert result["funding_bp"] is None


@pytest.mark.asyncio
async def test_forged_candidate_cannot_reach_execution():
    result = await premium_engine.open_live(
        {"ready": True, "score": 100, "symbol": "BTCUSDT", "side": "LONG",
         "price": 100, "stop": 99, "target": 110}, 1, "BINANCE")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_disabled_scanner_does_not_fetch(monkeypatch):
    async def forbidden(*args):
        pytest.fail("Disabled engine fetched prices")
    monkeypatch.setattr(smc, "scan", forbidden)
    monkeypatch.setitem(premium_engine._STATE, "enabled", False)
    assert (await premium_engine.scan_once())["enabled"] is False


@pytest.mark.asyncio
async def test_observation_is_persistent_deduplicated_and_demo_separated(monkeypatch, tmp_path):
    from app import db
    monkeypatch.setattr(db.settings, "db_path", tmp_path / "smc.db")
    db.init()
    row = {"symbol": "BTCUSDT", "as_of": 1000, "demo": True, "ready": False}
    async def scan(*args):
        return {"candidates": [dict(row)], "scanned": 1, "errors": 0, "last_scan_at": 1000}
    monkeypatch.setattr(smc, "scan", scan)
    monkeypatch.setattr(premium_engine, "_STATE", {
        "enabled": True, "scanning": False, "last_scan_at": None})
    await premium_engine.scan_once()
    await premium_engine.scan_once()
    assert len(premium_engine.history()["items"]) == 1
    row["demo"] = False
    await premium_engine.scan_once()
    assert len(premium_engine.history()["items"]) == 2
    assert db.get_setting("smc_last_scan")["scanned"] == 1


def test_legacy_entry_disabled_by_default_but_user_preference_retained(monkeypatch):
    from app.services import tsmom_engine
    from app import db
    monkeypatch.setattr(db, "get_setting", lambda key, default=None: default)
    assert tsmom_engine.enabled() is False
    monkeypatch.setattr(db, "get_setting", lambda key, default=None: {
        "primary_engine": "tsmom", "tsmom_engine_enabled": True}.get(key, default))
    assert tsmom_engine.enabled() is True
