"""VORTEX — kritik yol testleri.

Calistir:   python3 -m pytest testler/ -q
Bagimlilik: pip install pytest pytest-asyncio

Buradaki testler "strateji kar ediyor mu" sorusunu OLCMEZ. Sadece parayla
ilgili aritmetigin ve sistemin kendini toparlama davranisinin dogru oldugunu
dogrular — yani 29.08 denetiminde bulunan hatalarin geri gelmedigini.
"""
import asyncio
import pytest

from app.services import binance
from app.config import settings


# ===================================================================== #
# 1) DEMO'DAN KURTARMA — tek yonlu kapi hatasi geri gelmesin
# ===================================================================== #
@pytest.mark.asyncio
async def test_demo_moddan_kurtarma_calisir(monkeypatch):
    """Ag geri geldiginde mod kendiliginden canliya donmeli."""
    monkeypatch.setattr(settings, "data_mode", "auto", raising=False)
    binance._state["mode"] = "demo"
    binance._state["recover_tries"] = 0
    monkeypatch.setattr(binance, "RECOVER_INTERVAL", 0.01)

    async def sahte_request(path, params=None, weight=1):
        binance._state["mode"] = "live"      # gercek _request'in yaptigi sey
        return {}
    monkeypatch.setattr(binance, "_request", sahte_request)

    await binance.start_recovery()
    await asyncio.sleep(0.08)
    await binance.stop_recovery()

    assert not binance.is_demo(), "kurtarma calismadi, demo'da kaldi"
    assert binance._state["recovered_at"] is not None


@pytest.mark.asyncio
async def test_kurtarma_basarisiz_denemede_olmez(monkeypatch):
    """Ag hala kapaliysa dongu OLMEMELI — tekrar denemeye devam etmeli."""
    monkeypatch.setattr(settings, "data_mode", "auto", raising=False)
    binance._state["mode"] = "demo"
    binance._state["recover_tries"] = 0
    monkeypatch.setattr(binance, "RECOVER_INTERVAL", 0.01)

    async def hep_patlayan(path, params=None, weight=1):
        raise binance.BinanceError("HTTP 451")
    monkeypatch.setattr(binance, "_request", hep_patlayan)

    await binance.start_recovery()
    await asyncio.sleep(0.08)
    calisiyor = binance._recover_task and not binance._recover_task.done()
    denemeler = binance._state["recover_tries"]
    await binance.stop_recovery()

    assert calisiyor, "dongu ilk hatada oldu — tek yonlu kapi geri geldi"
    assert denemeler >= 2, f"tekrar denemedi (deneme={denemeler})"


@pytest.mark.asyncio
async def test_data_mode_demo_ise_kurtarma_denemez(monkeypatch):
    """VORTEX_DATA_MODE=demo bilincli bir tercih — zorla canliya cekilmemeli."""
    monkeypatch.setattr(settings, "data_mode", "demo", raising=False)
    binance._state["mode"] = "demo"
    binance._state["recover_tries"] = 0
    monkeypatch.setattr(binance, "RECOVER_INTERVAL", 0.01)

    async def olmamali(path, params=None, weight=1):
        pytest.fail("data_mode=demo iken istek atilmamaliydi")
    monkeypatch.setattr(binance, "_request", olmamali)

    await binance.start_recovery()
    await asyncio.sleep(0.05)
    await binance.stop_recovery()
    assert binance.is_demo()


# ===================================================================== #
# 2) BILDIRIM DONGUSU — sessiz kalici olum geri gelmesin
# ===================================================================== #
@pytest.mark.asyncio
async def test_bildirim_dongusu_hatada_olmez(monkeypatch):
    """run_due() patlarsa dongu devam etmeli, kalici olarak olmemeli."""
    from app.services import notification_scheduler as ns

    sayac = {"n": 0}

    async def patlayan():
        sayac["n"] += 1
        raise RuntimeError("database is locked")

    # DIKKAT: ns.asyncio GLOBAL asyncio modulunun kendisi — orayi patchlemek
    # testin kendi await'lerini de kisaltir ve olcumu bozar. Onun yerine
    # dongu araligi modul sabiti olarak disari alindi.
    monkeypatch.setattr(ns, "run_due", patlayan)
    monkeypatch.setattr(ns, "POLL_SECONDS", 0.01)

    gorev = asyncio.create_task(ns._loop())
    await asyncio.sleep(0.06)
    olu = gorev.done()
    gorev.cancel()
    try:
        await gorev
    except asyncio.CancelledError:
        pass

    assert not olu, "dongu ilk hatada oldu"
    assert sayac["n"] >= 2, f"tekrar denemedi (cagri={sayac['n']})"


# =================================================================== #
# 09.09 GECISI — backtest kararlarinin KAYITLI ayara uygulanmasi
#
# DEFAULTS'u degistirmek kayitli ayari degistirmez (_merge'te kayitli
# deger kazanir). Bu testler gecisin gercekten calistigini VE bir kez
# calistigini dogruluyor: ayarin sahibi kullanici, her acilista ezmek
# onun kararini gecersiz kilardi.
# =================================================================== #
def test_gecis_otomatik_acilisi_kapatiyor(monkeypatch):
    from app import runtime
    depo = {"runtime_settings": {"engine": {"auto_open_score": 5, "direction": "BOTH"}}}
    monkeypatch.setattr(runtime.db, "get_setting",
                        lambda k, d=None: depo.get(k, d))
    monkeypatch.setattr(runtime.db, "set_setting",
                        lambda k, v: depo.__setitem__(k, v))
    d = runtime.migrate()
    assert depo["runtime_settings"]["engine"]["auto_open_score"] == 0
    assert depo["runtime_settings"]["engine"]["direction"] == "LONG"
    assert "auto_open_score" in d and "direction" in d


def test_gecis_ikinci_kez_calismiyor(monkeypatch):
    """Kullanici ayari elle geri alirsa her acilista ezmemeliyiz."""
    from app import runtime
    depo = {"runtime_settings": {"engine": {"auto_open_score": 5, "direction": "BOTH"}}}
    monkeypatch.setattr(runtime.db, "get_setting", lambda k, d=None: depo.get(k, d))
    monkeypatch.setattr(runtime.db, "set_setting", lambda k, v: depo.__setitem__(k, v))
    runtime.migrate()
    # kullanici bilerek geri aldi
    depo["runtime_settings"]["engine"]["direction"] = "BOTH"
    runtime.migrate()
    assert depo["runtime_settings"]["engine"]["direction"] == "BOTH"


def test_varsayilanlar_guvenli_tarafta():
    """Yeni kurulumda otomatik acilis KAPALI ve yon LONG olmali."""
    from app.runtime import DEFAULTS
    assert DEFAULTS["engine"]["auto_open_score"] == 0
    assert DEFAULTS["engine"]["direction"] == "LONG"
    assert DEFAULTS["mentor_tarama"]["yon"] == "LONG"
