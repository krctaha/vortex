"""Mentor hesaplarinin dogrulugu.

Bu testler "sistem kar ediyor mu" sorusuyla ilgilenmez. Yalnizca olcum
aritmetiginin dogru oldugunu dogrular — yanlis olcum, yanlis ogut demektir.
"""
import json
import math
import pytest

from app.services import mentor_journal as mentor


# ---------------- R hesabi ---------------- #
def test_r_long_kazanc():
    # giris 100, stop 90 -> risk 10. cikis 120 -> +2R
    assert mentor.r_multiple("LONG", 100, 90, 120) == pytest.approx(2.0)

def test_r_long_stop():
    assert mentor.r_multiple("LONG", 100, 90, 90) == pytest.approx(-1.0)

def test_r_short_kazanc():
    # short 100, stop 110 -> risk 10. cikis 80 -> +2R
    assert mentor.r_multiple("SHORT", 100, 110, 80) == pytest.approx(2.0)

def test_r_short_stop():
    assert mentor.r_multiple("SHORT", 100, 110, 110) == pytest.approx(-1.0)

def test_r_sifir_risk_none():
    assert mentor.r_multiple("LONG", 100, 100, 120) is None


# ---------------- MFE / MAE / updraw ---------------- #
def _bar(hi, lo):
    return [0, 0, hi, lo, 0, 0]

def test_yol_long():
    # giris 100, stop 90 (risk 10), hedef 130 (3R)
    # fiyat en yuksek 115 (+1.5R), en dusuk 95 (-0.5R)
    m = mentor.yol_metrikleri("LONG", 100, 90, 130, [_bar(115, 95)])
    assert m["mfe_r"] == pytest.approx(1.5)
    assert m["mae_r"] == pytest.approx(-0.5)
    # updraw: 1.5R / 3R = %50
    assert m["updraw_pct"] == pytest.approx(50.0)
    # drawdown: 0.5R dusus, stop 1R -> %50
    assert m["drawdown_pct"] == pytest.approx(50.0)

def test_yol_short():
    # short 100, stop 110 (risk 10), hedef 70 (3R)
    # fiyat en dusuk 85 -> short lehine +1.5R ; en yuksek 105 -> -0.5R
    m = mentor.yol_metrikleri("SHORT", 100, 110, 70, [_bar(105, 85)])
    assert m["mfe_r"] == pytest.approx(1.5)
    assert m["mae_r"] == pytest.approx(-0.5)
    assert m["updraw_pct"] == pytest.approx(50.0)
    assert m["drawdown_pct"] == pytest.approx(50.0)

def test_stop_olan_islem_drawdown_100():
    m = mentor.yol_metrikleri("LONG", 100, 90, 130, [_bar(101, 90)])
    assert m["drawdown_pct"] == pytest.approx(100.0)


# ---------------- cikis verimliligi ---------------- #
def test_verim_erken_cikis():
    # MFE 2R idi, 1R'de cikildi -> %50
    assert mentor.cikis_verimliligi("LONG", 100, 90, 110, 2.0) == pytest.approx(50.0)

def test_verim_kaybedende_none():
    assert mentor.cikis_verimliligi("LONG", 100, 90, 95, 0.5) is None

def test_verim_tavan_100():
    # cikis MFE'yi asamaz; assagi yuvarlanmali
    assert mentor.cikis_verimliligi("LONG", 100, 90, 120, 2.0) == pytest.approx(100.0)


# ---------------- SQN ---------------- #
def test_sqn_n_100de_kirpilir():
    """Cok islem yaparak skoru sisirmek mumkun olmamali."""
    rs = [0.5, -0.5] * 100          # n=200
    s = mentor.sqn(rs)
    assert s["kirpilan_n"] == 100

def test_sqn_sabit_seride_none():
    # varyans sifir -> bolme hatasi yerine None
    assert mentor.sqn([1.0] * 30) is None

def test_sqn_gurultu_yorumu():
    rs = [0.1, -0.1] * 15
    s = mentor.sqn(rs)
    assert s["deger"] < 1.6 and "gurultu" in s["yorum"]


# ---------------- bootstrap ---------------- #
def test_bootstrap_sifiri_iceriyor():
    """Simetrik, ortalamasi sifira yakin seri -> aralik sifiri icermeli."""
    rs = [1.0, -1.0] * 25
    ci = mentor.bootstrap_ci(rs, tekrar=2000)
    assert ci["sifiri_iceriyor"] is True

def test_bootstrap_net_pozitif():
    """Hepsi pozitif seri -> alt sinir sifirin ustunde olmali."""
    rs = [0.8, 1.2, 0.9, 1.1, 1.0] * 10
    ci = mentor.bootstrap_ci(rs, tekrar=2000)
    assert ci["alt"] > 0 and ci["sifiri_iceriyor"] is False

def test_bootstrap_kucuk_ornekte_none():
    assert mentor.bootstrap_ci([1.0, -1.0]) is None

def test_bootstrap_deterministik():
    rs = [0.5, -1.0, 2.0, -0.3, 1.4] * 8
    assert mentor.bootstrap_ci(rs) == mentor.bootstrap_ci(rs)


# ---------------- davranis bayraklari ---------------- #
def _islem(opened_at, closed_at=None, result_r=None, qty=None):
    return {"opened_at": opened_at, "closed_at": closed_at,
            "result_r": result_r, "qty": qty}

DK = 60_000

def test_bayrak_zarardan_sonra_hizli_giris():
    gecmis = [_islem(0, 100 * DK, -1.0, 10)]
    b = mentor.bayraklari_hesapla(_islem(105 * DK, qty=10), gecmis)
    assert any("15 dk" in x for x in b)

def test_bayrak_uzun_arada_tetiklenmez():
    gecmis = [_islem(0, 100 * DK, -1.0, 10)]
    b = mentor.bayraklari_hesapla(_islem(200 * DK, qty=10), gecmis)
    assert not any("15 dk" in x for x in b)

def test_bayrak_kazanctan_sonra_tetiklenmez():
    gecmis = [_islem(0, 100 * DK, +1.0, 10)]
    b = mentor.bayraklari_hesapla(_islem(105 * DK, qty=10), gecmis)
    assert not any("15 dk" in x for x in b)

def test_bayrak_zarardan_sonra_boyut_buyutme():
    gecmis = [_islem(i * DK, (i + 10) * DK, -1.0, 10) for i in range(5)]
    b = mentor.bayraklari_hesapla(_islem(500 * DK, qty=30), gecmis)
    assert any("buyutuldu" in x for x in b)

def test_bayrak_kayip_serisi():
    gecmis = [_islem(i * DK, (i + 1) * DK, -1.0, 10) for i in range(4)]
    b = mentor.bayraklari_hesapla(_islem(500 * DK, qty=10), gecmis)
    assert any("kaybediyorsun" in x for x in b)

def test_bayrak_bos_gecmiste_patlamaz():
    assert mentor.bayraklari_hesapla(_islem(0, qty=1), []) == []


# ---------------- ogutler: yetersiz ornekte iddia YOK ---------------- #
def test_az_ornekte_sadece_bilgi():
    k = {"genel": {"n": 5, "ortalama_r": 0.9}, "guven_araligi": None,
         "sqn": None, "kural": {}, "kirilim": {}, "verimlilik": {},
         "bayrak_sayimi": {}}
    o = mentor.ogutler(k)
    assert len(o) == 1 and o[0]["tip"] == "bilgi"

def test_ci_sifiri_iceriyorsa_uyari():
    k = {"genel": {"n": 40, "ortalama_r": 0.3},
         "guven_araligi": {"alt": -0.2, "ust": 0.8, "sifiri_iceriyor": True},
         "sqn": None,
         "kural": {"uydugumda": {"n": 0}, "uymadigimda": {"n": 0}},
         "kirilim": {"yon": [], "etiket": []},
         "verimlilik": {"ort_updraw": None, "ort_cikis_verimi": None,
                        "kazananlarda_ort_drawdown": None},
         "bayrak_sayimi": {}}
    o = mentor.ogutler(k)
    assert any(x["tip"] == "uyari" and "YOK" in x["metin"] for x in o)

def test_yetersiz_hucre_ogut_uretmez():
    """3 islemlik bir kirilim 'bu tarafi kes' dedirtmemeli."""
    k = {"genel": {"n": 40, "ortalama_r": 0.1},
         "guven_araligi": {"alt": -0.3, "ust": 0.5, "sifiri_iceriyor": True},
         "sqn": None,
         "kural": {"uydugumda": {"n": 0}, "uymadigimda": {"n": 0}},
         "kirilim": {"yon": [{"anahtar": "SHORT", "n": 3, "ortalama_r": -1.0,
                              "yeterli": False}],
                     "etiket": []},
         "verimlilik": {"ort_updraw": None, "ort_cikis_verimi": None,
                        "kazananlarda_ort_drawdown": None},
         "bayrak_sayimi": {}}
    o = mentor.ogutler(k)
    assert not any("kes" in x["metin"] for x in o)


# ---------------- akil sagligi kapisi ---------------- #
def test_uyusmayan_fiyat_olceginde_none_doner():
    """Giris fiyati mumlarla alakasizsa metrik uretilmemeli — cop sayi
    yanlis ogute donusur."""
    bars = [_bar(100_500, 99_500)]          # BTC olcegi
    m = mentor.yol_metrikleri("LONG", 100, 90, 130, bars)   # giris 100
    assert m["mfe_r"] is None
    assert m.get("olculemedi")

def test_uyusan_fiyat_olceginde_hesaplar():
    bars = [_bar(115, 95)]
    m = mentor.yol_metrikleri("LONG", 100, 90, 130, bars)
    assert m["mfe_r"] == pytest.approx(1.5)

def test_sinirdaki_giris_kabul_edilir():
    """Giris mum araliginin hemen disinda ama makul mesafedeyse (gap)
    olcum yapilmali."""
    bars = [_bar(110, 105)]
    m = mentor.yol_metrikleri("LONG", 104, 99, 120, bars)
    assert m["mfe_r"] is not None


# ==================================================================== #
# BINANCE GECMISI: tam tur ayiklama
# Bu algoritma yanlissa AKTARILAN TUM SICIL yanlis olur — giris/cikis
# fiyatlari ve yon buradan cikiyor.
# ==================================================================== #
from app.services import mentor_import


def _f(side, price, qty, ts, pnl=0.0, com=0.0):
    return {"side": side, "price": str(price), "qty": str(qty), "time": ts,
            "realizedPnl": str(pnl), "commission": str(com)}


def test_tur_basit_long():
    # 100'den al, 120'den sat -> LONG, giris 100, cikis 120
    t = mentor_import._tur_ayikla([_f("BUY",100,1,1000), _f("SELL",120,1,2000,pnl=20)])
    assert len(t) == 1
    assert t[0]["side"] == "LONG"
    assert t[0]["entry"] == pytest.approx(100)
    assert t[0]["exit_price"] == pytest.approx(120)
    assert t[0]["qty"] == pytest.approx(1)
    assert t[0]["opened_at"] == 1000 and t[0]["closed_at"] == 2000


def test_tur_basit_short():
    # 100'den sat, 80'den al -> SHORT
    t = mentor_import._tur_ayikla([_f("SELL",100,1,1000), _f("BUY",80,1,2000,pnl=20)])
    assert len(t) == 1 and t[0]["side"] == "SHORT"
    assert t[0]["entry"] == pytest.approx(100)
    assert t[0]["exit_price"] == pytest.approx(80)


def test_tur_kademeli_giris_agirlikli_ortalama():
    # 100'den 1, 200'den 1 al -> ortalama giris 150
    t = mentor_import._tur_ayikla([
        _f("BUY",100,1,1000), _f("BUY",200,1,1500), _f("SELL",300,2,2000,pnl=300)])
    assert len(t) == 1
    assert t[0]["entry"] == pytest.approx(150)
    assert t[0]["qty"] == pytest.approx(2)


def test_tur_kismi_cikis_tek_tur_sayilir():
    # 2 al, once 1 sat sonra 1 sat -> TEK tur, cikis ortalamasi 150
    t = mentor_import._tur_ayikla([
        _f("BUY",100,2,1000), _f("SELL",100,1,1500), _f("SELL",200,1,2000)])
    assert len(t) == 1
    assert t[0]["exit_price"] == pytest.approx(150)
    assert t[0]["closed_at"] == 2000


def test_tur_iki_ayri_tur():
    t = mentor_import._tur_ayikla([
        _f("BUY",100,1,1000), _f("SELL",110,1,2000,pnl=10),
        _f("SELL",200,1,3000), _f("BUY",180,1,4000,pnl=20)])
    assert len(t) == 2
    assert t[0]["side"] == "LONG" and t[1]["side"] == "SHORT"


def test_tur_komisyon_dusuluyor():
    t = mentor_import._tur_ayikla([
        _f("BUY",100,1,1000,com=0.5), _f("SELL",120,1,2000,pnl=20,com=0.5)])
    assert t[0]["brut_pnl"] == pytest.approx(20)
    assert t[0]["pnl_usdt"] == pytest.approx(19.0)   # 20 - 1.0 komisyon


def test_tur_acik_pozisyon_dahil_edilmez():
    """Henuz kapanmamis pozisyon TUR SAYILMAZ — yoksa uydurma cikis olurdu."""
    t = mentor_import._tur_ayikla([_f("BUY",100,1,1000)])
    assert t == []


def test_tur_bozuk_dolum_atlanir():
    t = mentor_import._tur_ayikla([
        {"side":"BUY","price":"abc","qty":"1","time":1},
        _f("BUY",100,1,1000), _f("SELL",120,1,2000)])
    assert len(t) == 1 and t[0]["entry"] == pytest.approx(100)


def test_tur_sirasiz_gelen_dolumlar_zamana_gore_islenir():
    t = mentor_import._tur_ayikla([
        _f("SELL",120,1,2000), _f("BUY",100,1,1000)])
    assert len(t) == 1 and t[0]["side"] == "LONG"
