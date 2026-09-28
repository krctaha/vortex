"""SHORT ANALIZI — kovalarin kendisi dogru mu?

Bu testlerin derdi sonucun ne cikacagi degil, OLCUM ARACININ bozuk
olmamasi. Iki sessiz hata sinifi var ve ikisi de sonucu tamamen
degistirip hicbir uyari vermez:

  1. Bar toplama kaymasi. Turetilen 4h mumu borsanin mumundan kayarsa,
     "4h yukari diyordu" cumlesi baska bir mumu kastetmis olur.
  2. Look-ahead. Islem anindan SONRA kapanan bir mumu okumak backtest'i
     sistematik olarak iyimser yapar — ve rapor kusursuz gorunur.
"""
import pytest

import short_analizi as sa


def _bar(acilis, o, h, l, c, adim, hacim=10.0):
    return [acilis, str(o), str(h), str(l), str(c), str(hacim),
            acilis + adim - 1, str(hacim * c), 5, "0", "0", "0"]


def _seri(n, adim, bas=0):
    return [_bar(bas + i * adim, 100 + i, 101 + i, 99 + i, 100.5 + i, adim)
            for i in range(n)]


# ------------------------------------------------------------------ #
# BAR TOPLAMA
# ------------------------------------------------------------------ #
def test_1h_4h_dogru_sayida():
    b4 = sa.topla(_seri(24, sa.MS["1h"]), "1h", "4h")
    assert len(b4) == 6


def test_5m_15m_dogru_sayida():
    b15 = sa.topla(_seri(12, sa.MS["5m"]), "5m", "15m")
    assert len(b15) == 4


def test_kova_siniri_utc_epoch_hizali():
    """Binance de boyle boluyor; kayarsa olcum sessizce yanlis olur."""
    b4 = sa.topla(_seri(24, sa.MS["1h"]), "1h", "4h")
    assert all(int(b[0]) % sa.MS["4h"] == 0 for b in b4)


def test_ohlc_dogru_toplaniyor():
    barlar = [_bar(0, 100, 110, 95, 105, sa.MS["1h"]),
              _bar(sa.MS["1h"], 105, 120, 90, 100, sa.MS["1h"]),
              _bar(2 * sa.MS["1h"], 100, 108, 99, 107, sa.MS["1h"]),
              _bar(3 * sa.MS["1h"], 107, 115, 101, 112, sa.MS["1h"])]
    b4 = sa.topla(barlar, "1h", "4h")
    assert len(b4) == 1
    k = b4[0]
    assert float(k[1]) == 100        # ilk acilis
    assert float(k[2]) == 120        # en yuksek
    assert float(k[3]) == 90         # en dusuk
    assert float(k[4]) == 112        # son kapanis


def test_eksik_kova_atiliyor():
    """Kapanmamis bir 4h mumuyla karar vermek look-ahead'in tersi bir hata."""
    barlar = _seri(6, sa.MS["1h"])          # 4 tam + 2 eksik
    assert len(sa.topla(barlar, "1h", "4h")) == 1


def test_kapanis_zamani_dogru():
    b4 = sa.topla(_seri(4, sa.MS["1h"]), "1h", "4h")
    assert int(b4[0][6]) == sa.MS["4h"] - 1


def test_uyumsuz_adim_reddediliyor():
    with pytest.raises(ValueError):
        sa.topla(_seri(4, sa.MS["1h"]), "1h", "15m")


def test_bozuk_bar_akisi_kesmiyor():
    barlar = _seri(4, sa.MS["1h"])
    barlar.append(["cop"] * 12)
    assert len(sa.topla(barlar, "1h", "4h")) == 1


# ------------------------------------------------------------------ #
# REJIM ARAMASI
# ------------------------------------------------------------------ #
def test_rejim_at_gecmise_bakiyor():
    seri = [(1000, "yukari"), (2000, "yatay"), (3000, "asagi")]
    assert sa.rejim_at(seri, 2500) == "yatay"
    assert sa.rejim_at(seri, 2000) == "yatay"
    assert sa.rejim_at(seri, 999) is None      # henuz veri yok
    assert sa.rejim_at(seri, 99999) == "asagi"


def test_rejim_at_bos_seri():
    assert sa.rejim_at([], 1000) is None


@pytest.mark.parametrize("kod,beklenen", [
    ("guclu_yukari", "BTC yukari (aleyhte)"),
    ("yukari", "BTC yukari (aleyhte)"),
    ("yatay", "BTC yatay"),
    ("asagi", "BTC asagi (lehte)"),
    ("guclu_asagi", "BTC asagi (lehte)"),
    (None, "bilinmiyor"),
])
def test_btc_kovalari(kod, beklenen):
    assert sa.btc_kova(kod) == beklenen


# ------------------------------------------------------------------ #
# MTF KOVASI
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("net,beklenen", [
    (6, "MTF hizali"), (3, "MTF hizali"), (2, "MTF zayif"),
    (0, "MTF yonsuz"), (-4, "MTF catisiyor"), (None, "bilinmiyor"),
])
def test_mtf_kovalari(net, beklenen):
    assert sa.mtf_kova(net) == beklenen


# ------------------------------------------------------------------ #
# LOOK-AHEAD — en sinsi hata
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_mtf_islem_anindan_sonraki_mumu_okumuyor(monkeypatch):
    """4h mumun ortasindayken o mumun kapanisini gormek, backtest'i
    sistematik olarak iyimser yapar ve rapor kusursuz gorunur."""
    gorulen = {}

    async def sahte(symbol, interval, rows):
        gorulen[interval] = [int(r[6]) for r in rows]
        return "bear"

    monkeypatch.setattr(sa, "_yon_kodu", sahte)
    b4 = sa.topla(_seri(400, sa.MS["1h"]), "1h", "4h")
    b15 = sa.topla(_seri(400, sa.MS["5m"]), "5m", "15m")
    kesme = int(b4[80][6])                    # 81. 4h mumun kapanisi (>=60 bar var)
    await sa.mtf_net("X", "SHORT", "bear", b4, b15, kesme)
    assert gorulen["4h"], "4h kesiti bos"
    assert max(gorulen["4h"]) <= kesme, "islem anindan sonra kapanan mum okundu"


@pytest.mark.asyncio
async def test_mtf_net_isareti_short_icin_ters(monkeypatch):
    """net, PLAN YONUNDEN bakiyor: short'ta 'bear' lehtedir."""
    async def hep_bear(symbol, interval, rows):
        return "bear"

    monkeypatch.setattr(sa, "_yon_kodu", hep_bear)
    b4 = sa.topla(_seri(400, sa.MS["1h"]), "1h", "4h")
    b15 = sa.topla(_seri(400, sa.MS["5m"]), "5m", "15m")
    ms = int(b4[-1][6])
    net = await sa.mtf_net("X", "SHORT", "bear", b4, b15, ms)
    assert net == 6                            # 4h(3) + 1h(2) + 15m(1), hepsi lehte

    net_long = await sa.mtf_net("X", "LONG", "bull", b4, b15, ms)
    assert net_long == -6 + 4                  # 1h bull(+2), 4h/15m bear(-4)


@pytest.mark.asyncio
async def test_mtf_veri_yoksa_none(monkeypatch):
    async def hic(symbol, interval, rows):
        return None

    monkeypatch.setattr(sa, "_yon_kodu", hic)
    net = await sa.mtf_net("X", "SHORT", "", [], [], 1000)
    assert net is None
