"""Plan kurucunun ARITMETIGI. Yanlissa yanlis boyut ve yanlis stop demek."""
import pytest
from app.services import mentor_plan

RCFG = {"max_risk_per_trade": 6.0, "max_position_margin": 60.0, "default_leverage": 4}


def test_boyut_riskten_hesaplanir():
    # giris 100, stop 95 -> birim risk 5. 6$ risk -> 1.2 adet
    b = mentor_plan._boyut(100, 95, RCFG)
    assert b["qty"] == pytest.approx(1.2)
    assert b["riske_edilen"] == pytest.approx(6.0)
    assert b["baglayan_sinir"] == "risk"


def test_boyut_marj_tavani_baglayabilir():
    # cok dar stop -> risk hesabi devasa qty verir, marj tavani kesmeli
    b = mentor_plan._boyut(100, 99.99, RCFG)
    # marj tavani: 60$ * 4x / 100 = 2.4 adet
    assert b["qty"] == pytest.approx(2.4)
    assert b["baglayan_sinir"] == "marj tavani"
    assert b["marj"] <= 60.0 + 1e-6


def test_boyut_marj_tavani_asilmaz():
    for stop in (99.9, 99, 95, 80, 50):
        b = mentor_plan._boyut(100, stop, RCFG)
        assert b["marj"] <= RCFG["max_position_margin"] + 1e-6, f"stop={stop}"


def test_genis_stopta_kaldirac_dusurulur():
    """Stop %30 uzaktaysa 4x'te likidasyon stoptan ONCE gelir."""
    b = mentor_plan._boyut(100, 70, RCFG)          # %30 stop
    assert b["stop_yuzde"] == pytest.approx(30.0)
    assert b["kaldirac"] < b["istenen_kaldirac"]
    assert b["kaldirac_dusuruldu"] is True


def test_dar_stopta_kaldirac_dusurulmez():
    b = mentor_plan._boyut(100, 95, RCFG)          # %5 stop
    assert b["kaldirac"] == RCFG["default_leverage"]
    assert b["kaldirac_dusuruldu"] is False


def test_kaldirac_asla_sifir_olmaz():
    b = mentor_plan._boyut(100, 1, RCFG)           # %99 stop
    assert b["kaldirac"] >= 1


def test_stop_girise_esitse_olculemez():
    b = mentor_plan._boyut(100, 100, RCFG)
    assert "olculemedi" in b


def test_short_tarafinda_da_calisir():
    # short: giris 100, stop 105 -> birim risk 5
    b = mentor_plan._boyut(100, 105, RCFG)
    assert b["qty"] == pytest.approx(1.2)
    assert b["stop_yuzde"] == pytest.approx(5.0)


# ---- gurultu kapisi: cok dar stop kurulum sayilmamali ----
def test_esikler_makul():
    # ATR birincil kapi; yuzde yalnizca ATR okunamadiginda devreye giren
    # emniyet supabi oldugu icin BILEREK dusuk (sabit yuzde esigi BTC gibi
    # dusuk ATR'li kontratlarda gecerli kurulumlari elerdi).
    assert mentor_plan.MIN_STOP_ATR >= 0.5
    assert 0.05 <= mentor_plan.MIN_STOP_PCT <= 0.5

def test_gercek_sol_vakasi_elenir():
    """Canli testte cikan gercek vaka: SOL, stop %0.63, rr 11.5 ve
    siralamada BIRINCI. ATR ~1.5% oldugu icin bu stop 0.42xATR ->
    gurultu bandinda. Esik olmadan en kirilgan kurulum en iyi gorunuyordu."""
    giris, stop = 180.0, 181.13
    risk = stop - giris
    atr = 180.0 * 0.015                      # SOL 1h ATR ~ %1.5
    sebep = mentor_plan.stop_gurultude_mi(risk, giris, atr)
    assert sebep is not None and "gürültü" in sebep


def test_btc_dar_ama_gecerli_stop_elenmez():
    """BTC'nin ATR'si dusuk; %0.35'lik stop ONUN icin normaldir.
    Sabit yuzde esigi kullansaydik bunu yanlislikla elerdik."""
    giris = 100000.0
    atr = giris * 0.004                       # BTC 1h ATR ~ %0.4
    risk = atr * 1.2                          # 1.2 x ATR -> saglikli
    assert mentor_plan.stop_gurultude_mi(risk, giris, atr) is None


def test_atr_yoksa_yuzde_tabani_devreye_girer():
    assert mentor_plan.stop_gurultude_mi(0.05, 100.0, 0) is not None   # %0.05
    assert mentor_plan.stop_gurultude_mi(2.0, 100.0, 0) is None        # %2


def test_gecerli_genis_stop_gecer():
    giris, atr = 100.0, 1.0
    assert mentor_plan.stop_gurultude_mi(1.5, giris, atr) is None      # 1.5xATR


def test_bir_atr_altinda_stop_elenir():
    """Canli vaka: BTC stop %0.41, ATR %0.45 -> 0.91xATR, rr 9.55.
    Hedef %4 uzaktayken 1 ATR'den dar stop oraya varmadan yenir."""
    giris = 79582.0
    atr = giris * 0.0045
    risk = giris * 0.0041
    assert mentor_plan.stop_gurultude_mi(risk, giris, atr) is not None

def test_bir_atr_ustunde_stop_gecer():
    giris = 79582.0
    atr = giris * 0.0045
    risk = atr * 1.1
    assert mentor_plan.stop_gurultude_mi(risk, giris, atr) is None


# =================================================================== #
# LISTE SIRALAMASI — "surekli short buluyor" sikayetinin kok nedeni
# =================================================================== #
def _p(yon, rr, kalite="güçlü uyum", sym="X"):
    return {"symbol": sym, "yon": yon, "rr": rr, "kalite": kalite,
            "ok": True, "var_mi": True}


def test_rr_siralamada_kirpiliyor():
    """12R'lik kurulum 4R'likten uc kat iyi degildir.

    rr = odul / risk; risk paydada oldugu icin stop daraldikca rr patlar.
    Kirpmadan siralarsak liste sistematik olarak EN DAR STOPLU, yani en
    kirilgan kurulumlarla dolar. Kullanicinin kendi backtest'i de ayni
    yone isaret ediyordu: ortalama MAE -0.835R.
    """
    a = mentor_plan._siralama_puani(_p("LONG", 4.0))
    b = mentor_plan._siralama_puani(_p("LONG", 12.0))
    assert a == b, "4R ustunde rr farki siralamayi degistirmemeli"


def test_dusuk_rr_hala_geride():
    assert (mentor_plan._siralama_puani(_p("LONG", 3.5))
            < mentor_plan._siralama_puani(_p("LONG", 1.5)))


def test_kalite_rr_den_once_gelir():
    """Zayif hizali bir 4R, guclu hizali bir 2R'nin onune gecmemeli."""
    zayif = mentor_plan._siralama_puani(_p("LONG", 4.0, "zayıf / çelişkili"))
    guclu = mentor_plan._siralama_puani(_p("LONG", 2.0, "güçlü uyum"))
    assert guclu < zayif




def _yon_ayari(monkeypatch, yon):
    """Yon suzgecini testin istedigi degere sabitler.

    Varsayilan artik LONG (backtest gerekcesi runtime.py'de). Siralama
    mantigini sinayan testler suzgecten BAGIMSIZ olmali, yoksa varsayilan
    her degistiginde alakasiz testler kirilir.
    """
    # DEFAULTS'tan kuruluyor, runtime.get()'ten DEGIL.
    #
    # OLCULMUS HATA (10.09): burasi gercek runtime.get()'i cagiriyordu ve
    # o da app_settings tablosunu okuyor. Temiz bir kurulumda (veritabani
    # henuz yokken) bu bes test "no such table: app_settings" ile
    # dusuyordu. Testin olctugu sey yon suzgeci; veritabaninin varligi
    # onun on sarti olmamali. Disariya bagimli bir birim testi, testin
    # kendisi hakkinda degil ortam hakkinda bilgi verir.
    from copy import deepcopy
    from app.runtime import DEFAULTS

    def sahte():
        cfg = deepcopy(DEFAULTS)
        cfg["mentor_tarama"] = {**cfg.get("mentor_tarama", {}), "yon": yon}
        return cfg

    monkeypatch.setattr(mentor_plan.runtime, "get", sahte)

@pytest.mark.asyncio
async def test_liste_iki_yonden_sirayla_doluyor(monkeypatch):
    """Tek yonun listeyi kapatmasi ENGELLENIYOR.

    Sorun neydi: rr yone gore simetrik degil. Fiyat 24 saatlik araligin
    ustlerindeyse en yakin direnc yakin (short'ta stop dar), destekler
    uzak (short'ta hedef genis) -> short'un rr'si buyuk cikar. Motor
    50/50 bull-bear bulsa bile ilk sekiz HEP short olur ve kullanici
    sistemin "surekli short buldugunu" gorur. Bu bir piyasa okumasi
    degil, siralama kriterinin yan etkisiydi.
    """
    _yon_ayari(monkeypatch, "BOTH")
    planlar = {}
    for i in range(6):
        planlar[f"S{i}USDT"] = _p("SHORT", 9.0 - i * 0.1, sym=f"S{i}USDT")
    for i in range(6):
        planlar[f"L{i}USDT"] = _p("LONG", 2.0 - i * 0.1, sym=f"L{i}USDT")

    async def sahte_plan(sym, interval="1h"):
        return {**planlar[sym], "interval": interval}

    monkeypatch.setattr(mentor_plan, "plan_kur", sahte_plan)
    monkeypatch.setattr("app.services.market_library.adaylar",
                        lambda **k: list(planlar))

    d = await mentor_plan.gunun_planlari(limit=6)
    yonler = [p["yon"] for p in d["planlar"]]
    assert d["short_sayisi"] == 6 and d["long_sayisi"] == 6
    assert yonler.count("LONG") == 3, f"tek taraf listeyi kapatti: {yonler}"
    assert yonler.count("SHORT") == 3


@pytest.mark.asyncio
async def test_tek_yonlu_piyasa_gizlenmiyor(monkeypatch):
    """Piyasa gercekten tek tarafliysa liste tek tarafli KALIR.

    Denge adina olmayan bir long uydurmak, tam da kacinmaya calistigimiz
    seyi yapardi: veriyi kullaniciya hos gorunsun diye egmek.
    """
    _yon_ayari(monkeypatch, "BOTH")
    planlar = {f"S{i}USDT": _p("SHORT", 3.0, sym=f"S{i}USDT") for i in range(4)}

    async def sahte_plan(sym, interval="1h"):
        return {**planlar[sym], "interval": interval}

    monkeypatch.setattr(mentor_plan, "plan_kur", sahte_plan)
    monkeypatch.setattr("app.services.market_library.adaylar",
                        lambda **k: list(planlar))

    d = await mentor_plan.gunun_planlari(limit=6)
    assert d["long_sayisi"] == 0
    assert all(p["yon"] == "SHORT" for p in d["planlar"])
    assert len(d["planlar"]) == 4


# =================================================================== #
# TP MERDIVENI — BES KADEME
#
# Kullanici bes kademe istedi. Buradaki risk, olmayan bir seviyeyi
# UYDURMAK: bes kademe cikarmak icin dayanaksiz sayilar uretmek, olcum
# gibi gorunen bir tahmin uretmek olurdu. Bu yuzden her kademe hangi
# kaynaktan geldigini soyluyor ve yapisal seviyeler HER ZAMAN listede
# kaliyor (rr kapisi onlara gore hesaplandi; listeden dusmeleri
# tutarsizlik olurdu).
# =================================================================== #
from app.services.mentor_plan import _tp_merdiveni


def test_merdiven_bes_kademe_uretiyor():
    k = _tp_merdiveni("LONG", 100.0, 2.0, [103.0, 106.0])
    assert len(k) == 5


def test_merdiven_paylari_yuze_tamamliyor():
    for hedefler in ([103.0, 106.0], [102.0], [110.0, 120.0], []):
        k = _tp_merdiveni("LONG", 100.0, 2.0, hedefler)
        if k:
            assert sum(x["pay"] for x in k) == 100


def test_merdiven_pay_on_agirlikli():
    """Olculen ortalama MFE ~1R; agirlik fiyatin gercekten ulastigi
    kademelerde olmali."""
    k = _tp_merdiveni("LONG", 100.0, 2.0, [103.0, 106.0])
    paylar = [x["pay"] for x in k]
    assert paylar == sorted(paylar, reverse=True)


def test_merdiven_yapisal_hedefleri_kaybetmiyor():
    """Uzaktaki gercek direnc listeden dusmemeli."""
    k = _tp_merdiveni("LONG", 100.0, 2.0, [110.0, 120.0])
    fiyatlar = [x["fiyat"] for x in k]
    assert 110.0 in fiyatlar and 120.0 in fiyatlar


def test_merdiven_kaynagi_isaretliyor():
    k = _tp_merdiveni("LONG", 100.0, 2.0, [103.0, 106.0])
    kaynaklar = {x["kaynak"] for x in k}
    assert kaynaklar == {"yapi", "R"}
    assert [x["kaynak"] for x in k if x["fiyat"] == 103.0] == ["yapi"]


def test_merdiven_artan_sirali():
    k = _tp_merdiveni("LONG", 100.0, 2.0, [110.0, 120.0])
    rler = [x["r"] for x in k]
    assert rler == sorted(rler)
    assert [x["no"] for x in k] == [1, 2, 3, 4, 5]


def test_merdiven_shortta_asagi_gidiyor():
    k = _tp_merdiveni("SHORT", 100.0, 2.0, [97.0, 94.0])
    fiyatlar = [x["fiyat"] for x in k]
    assert all(f < 100.0 for f in fiyatlar)
    assert fiyatlar == sorted(fiyatlar, reverse=True)   # uzaklastikca dusuyor


def test_merdiven_cok_yakin_kademeleri_birlestiriyor():
    """102.0 ve 102.1 ayri kademe degil; ikincisi 0.05R uzakta."""
    k = _tp_merdiveni("LONG", 100.0, 2.0, [102.0, 102.05])
    rler = [x["r"] for x in k]
    assert all(rler[i + 1] - rler[i] >= 0.15 for i in range(len(rler) - 1))


def test_merdiven_ters_yondeki_hedefi_atiyor():
    """LONG'da fiyatin ALTINDAKI bir seviye hedef olamaz."""
    k = _tp_merdiveni("LONG", 100.0, 2.0, [95.0, 104.0])
    assert all(x["fiyat"] > 100.0 for x in k)


def test_merdiven_risk_sifirsa_bos():
    assert _tp_merdiveni("LONG", 100.0, 0.0, [103.0]) == []


def test_merdiven_r_degerleri_dogru():
    """r = hedefe mesafe / stop mesafesi. Bu tanim bildirimde de,
    arayuzde de ayni olmali."""
    k = _tp_merdiveni("LONG", 100.0, 2.0, [104.0])
    yapisal = [x for x in k if x["kaynak"] == "yapi"][0]
    assert yapisal["r"] == 2.0        # 4 birim / 2 birim risk
    assert yapisal["yuzde"] == 4.0
    assert yapisal["fiyat"] == 104.0


# =================================================================== #
# YON SUZGECI
#
# Varsayilan LONG. Gerekce koda degil OLCUME dayaniyor: 27.08
# backtestinde (1.578 islem) SHORT'un ortalamasi -0,1008R ve %95 guven
# araligi [-0,185, -0,016] sifiri ICERMIYORDU. Elenen sayisinin
# raporlanmasi da onemli: sessizce eleyip sayi vermemek, kullaniciyi
# piyasa hakkinda yaniltir.
# =================================================================== #
@pytest.mark.asyncio
async def test_yon_suzgeci_long_shortlari_eliyor(monkeypatch):
    _yon_ayari(monkeypatch, "LONG")
    planlar = {f"S{i}USDT": _p("SHORT", 5.0, sym=f"S{i}USDT") for i in range(3)}
    planlar.update({f"L{i}USDT": _p("LONG", 2.0, sym=f"L{i}USDT") for i in range(2)})

    async def sahte_plan(sym, interval="1h"):
        return {**planlar[sym], "interval": interval}

    monkeypatch.setattr(mentor_plan, "plan_kur", sahte_plan)
    monkeypatch.setattr("app.services.market_library.adaylar",
                        lambda **k: list(planlar))

    d = await mentor_plan.gunun_planlari(limit=10)
    assert all(p["yon"] == "LONG" for p in d["planlar"])
    assert d["short_sayisi"] == 0
    assert d["bulunan"] == 2


@pytest.mark.asyncio
async def test_yon_suzgeci_elenen_sayisini_soyluyor(monkeypatch):
    """Sessiz eleme yasak: kac kurulumun gizlendigi raporlaniyor."""
    _yon_ayari(monkeypatch, "LONG")
    planlar = {f"S{i}USDT": _p("SHORT", 5.0, sym=f"S{i}USDT") for i in range(3)}
    planlar["L0USDT"] = _p("LONG", 2.0, sym="L0USDT")

    async def sahte_plan(sym, interval="1h"):
        return {**planlar[sym], "interval": interval}

    monkeypatch.setattr(mentor_plan, "plan_kur", sahte_plan)
    monkeypatch.setattr("app.services.market_library.adaylar",
                        lambda **k: list(planlar))

    d = await mentor_plan.gunun_planlari(limit=10)
    assert d["yon_elenen"] == 3
    assert d["yon_suzgec"] == "LONG"


@pytest.mark.asyncio
async def test_yon_both_hicbir_seyi_elemiyor(monkeypatch):
    _yon_ayari(monkeypatch, "BOTH")
    planlar = {"S0USDT": _p("SHORT", 5.0, sym="S0USDT"),
               "L0USDT": _p("LONG", 2.0, sym="L0USDT")}

    async def sahte_plan(sym, interval="1h"):
        return {**planlar[sym], "interval": interval}

    monkeypatch.setattr(mentor_plan, "plan_kur", sahte_plan)
    monkeypatch.setattr("app.services.market_library.adaylar",
                        lambda **k: list(planlar))

    d = await mentor_plan.gunun_planlari(limit=10)
    assert d["yon_elenen"] == 0 and d["bulunan"] == 2


# =================================================================== #
# MALIYET KAPISI
#
# Kullanicinin cumlesi: "maliyeti islemleri geciyor". Bu bir his degil,
# aritmetik: maliyet_R = %0,14 x (giris / stop_mesafesi). Stop yarilanirsa
# maliyet R olarak ikiye katlanir.
#
# 27.08 backtesti: brut +0,0576R, maliyet -0,0858R, net -0,0282R.
# Maliyet brut edge'den buyuk. Bu kapi tam olarak bunu engelliyor.
# =================================================================== #
from app.services.mentor_plan import maliyet_r, maliyet_kapisi, GIDIS_DONUS


def test_maliyet_stop_mesafesine_ters_orantili():
    """Tanim geregi: stop yarilanirsa maliyet ikiye katlanir."""
    genis = maliyet_r(100.0, 4.0)      # stop %4
    dar = maliyet_r(100.0, 2.0)        # stop %2
    assert abs(dar - 2 * genis) < 1e-9


def test_maliyet_gercekci_degerler():
    # stop %2 -> giris/risk = 50 -> 0.0014 * 50 = 0.07R
    assert abs(maliyet_r(100.0, 2.0) - 0.07) < 1e-9
    # stop %0.83 (olculen SHORT medyani) -> ~0.169R
    assert 0.16 < maliyet_r(100.0, 0.83) < 0.18


def test_short_medyani_kapiya_takiliyor():
    """Olculen SHORT stop medyani (%0,83) elenmeli."""
    assert maliyet_kapisi(100.0, 0.83, 0.08) is not None


def test_long_medyani_gecebiliyor():
    """Olculen LONG stop medyani (%2,25) gecmeli — kapi her seyi elemiyor."""
    assert maliyet_kapisi(100.0, 2.25, 0.08) is None


def test_kapi_sebebi_gerekli_stopu_soyluyor():
    """Sadece 'olmaz' demek yetmez; ne gerekirdi yazmali."""
    sebep = maliyet_kapisi(100.0, 0.5, 0.08)
    assert sebep and "%" in sebep
    # tavan 0.08 icin gerekli stop = 0.0014/0.08 = %1.75
    assert "1.75" in sebep


def test_kapi_kapatilabiliyor():
    """tavan 0 = kapali. Fonksiyonun kendisi bunu bilmeli; cagri
    yerindeki korumaya guvenmek ikinci bir cagri yazildigi gun
    sifira bolme hatasi verirdi."""
    assert maliyet_kapisi(100.0, 0.1, 0.0) is None
    assert maliyet_kapisi(100.0, 0.1, None) is None
    assert maliyet_kapisi(100.0, 0.1, 99.0) is None


def test_maliyet_bozuk_girdide_patlamiyor():
    assert maliyet_r(0.0, 1.0) is None
    assert maliyet_r(100.0, 0.0) is None
    assert maliyet_r(100.0, -1.0) is None
    assert maliyet_kapisi(100.0, 0.0, 0.08) is None


def test_gidis_donus_orani_backtestle_ayni():
    """Backtest %0,13 varsayiyordu (komisyon %0,045 + slipaj %0,02).
    Canli taraf VIP 0 taker (%0,05) kullaniyor; ikisi ayni mertebede
    olmali yoksa backtest canli sistemi olcmemis olur."""
    assert 0.0012 <= GIDIS_DONUS <= 0.0016
