"""Intraday Avcı stratejisinin geriye-dönük bilgi kullanmadığını doğrular."""
from app.strategies import breakout_hunter


def _bar(i, o, h, l, c, v):
    # Binance kline biçimi; strateji ilk altı alanı kullanır.
    return [i * 900_000, str(o), str(h), str(l), str(c), str(v)]


def _base():
    rows = []
    for i in range(60):
        mid = 100 + ((i % 5) - 2) * .05
        rows.append(_bar(i, mid, mid + .25, mid - .25, mid + .03, 100))
    return rows


def test_ilk_roket_mumu_kovalanmaz():
    rows = _base()
    rows.append(_bar(60, 100.0, 107.0, 99.8, 106.0, 1_000))
    result = breakout_hunter.evaluate("TESTUSDT", rows)
    assert not result["ok"]
    assert "kırılım" in result["veto"][0]


def test_hacimli_kirilim_sonrasi_reclaim_long_uretir():
    rows = _base()
    rows.extend([
        _bar(60, 100.0, 107.0, 99.8, 106.0, 1_000),  # hacimli kırılım
        _bar(61, 106.0, 106.5, 98.0, 99.0, 850),      # sweep: ilk giriş stop olurdu
        _bar(62, 99.0, 102.0, 98.7, 101.2, 700),      # seviyeyi geri alır
    ])
    result = breakout_hunter.evaluate("TESTUSDT", rows)
    assert result["ok"]
    assert result["side"] == "LONG"
    assert result["phase"] == "reclaim"
    assert result["stop"] < result["entry"] < result["target"]
    assert result["signal_bar_time"] == rows[-1][0]


def test_reclaim_yokken_bekler():
    rows = _base()
    rows.extend([
        _bar(60, 100.0, 107.0, 99.8, 106.0, 1_000),
        _bar(61, 106.0, 106.3, 101.5, 103.0, 300),
    ])
    result = breakout_hunter.evaluate("TESTUSDT", rows)
    assert not result["ok"]
    assert result["phase"] == "breakout_watch"


def test_asiri_genis_stop_reddedilir():
    rows = _base()
    rows.extend([
        _bar(60, 100.0, 107.0, 99.8, 106.0, 1_000),
        _bar(61, 106.0, 106.5, 65.0, 99.0, 850),
        _bar(62, 99.0, 102.0, 70.0, 101.2, 700),
    ])
    result = breakout_hunter.evaluate("TESTUSDT", rows)
    assert not result["ok"]
    assert "stopu çok geniş" in result["veto"][0]
