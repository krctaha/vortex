"""PIYASA KUTUPHANESI — siralama ve aday secimi.

Bu katmanin isi tahmin degil DIKKAT DAGITIMI: pahali analiz nereye
gidecek. Yanlis siralarsa para kaybettirmez ama radar yine kor kalir,
ki duzeltmeye calistigimiz sorun tam olarak oydu. O yuzden test edilen
sey su: siralama sabit hacmi degil, sembolun KENDI normalini oduluyor mu.
"""
import pytest
from app.services import market_library as ml


# --------------------------------------------------------------- #
# ILGI PUANI
# --------------------------------------------------------------- #
def test_hacim_genislemesi_puani_artirir():
    sakin = ml._ilgi_puani({"vol_ratio": 1.0, "mom_1h": 0.0, "band_pos": 0.5,
                            "change_pct": 0.0})
    patlayan = ml._ilgi_puani({"vol_ratio": 8.0, "mom_1h": 0.0, "band_pos": 0.5,
                               "change_pct": 0.0})
    assert patlayan > sakin


def test_mutlak_hacim_puana_HIC_girmiyor():
    """Kullanicinin sikayetinin cekirdegi.

    "3M hacim her coinde olmaz... yoksa hep yuksek fiyatli coinleri
    kovalar." Puanda quote_volume alani bilincli olarak YOK; 60 milyon
    dolarlik BTC ile 2 milyon dolarlik kucuk kontrat, ayni goreli
    hareketi yapiyorsa ayni puani alir.
    """
    dev = ml._ilgi_puani({"vol_ratio": 3.0, "mom_1h": 1.5, "band_pos": 0.9,
                          "change_pct": 4.0, "quote_volume": 900e6})
    kucuk = ml._ilgi_puani({"vol_ratio": 3.0, "mom_1h": 1.5, "band_pos": 0.9,
                            "change_pct": 4.0, "quote_volume": 1.2e6})
    assert dev == kucuk


def test_bant_ucu_ortadan_yuksek():
    orta = ml._ilgi_puani({"vol_ratio": None, "mom_1h": None, "band_pos": 0.5,
                           "change_pct": 0.0})
    tepe = ml._ilgi_puani({"vol_ratio": None, "mom_1h": None, "band_pos": 1.0,
                           "change_pct": 0.0})
    dip = ml._ilgi_puani({"vol_ratio": None, "mom_1h": None, "band_pos": 0.0,
                          "change_pct": 0.0})
    assert tepe > orta and dip > orta
    # Iki uc simetrik: dip de tepe kadar ilgi cekici (short/long ayrimi
    # bu katmanda yapilmiyor, o kademe 2'nin isi).
    assert tepe == pytest.approx(dip)


def test_gec_kalinmis_hareket_cezalandirilir():
    """%58 yapmis bir coini listenin basina koymak KOVALAMAKTIR.

    Kullanicinin sordugu soru "bu hareketi neden onceden yakalamadik"
    idi; hareket bittikten sonra yakalamak o sorunun cevabi degil.
    """
    erken = ml._ilgi_puani({"vol_ratio": 4.0, "mom_1h": 2.0, "band_pos": 0.95,
                            "change_pct": 6.0})
    gec = ml._ilgi_puani({"vol_ratio": 4.0, "mom_1h": 2.0, "band_pos": 0.95,
                          "change_pct": 70.0})
    assert erken > gec


def test_veri_yoksa_patlamaz_ve_sifira_yakin():
    p = ml._ilgi_puani({"vol_ratio": None, "mom_1h": None, "band_pos": None,
                        "change_pct": None})
    assert p == 0.0


def test_hacim_orani_logaritmik_dogrusal_degil():
    """4x ile 40x arasindaki fark 10 kat OLMAMALI.

    Dogrusal olsaydi tek bir asiri deger butun listeyi ele gecirirdi;
    ornegin yeni listelenen bir kontratin ilk gunku hacim orani.
    """
    a = ml._ilgi_puani({"vol_ratio": 4.0, "mom_1h": None, "band_pos": None, "change_pct": 0})
    b = ml._ilgi_puani({"vol_ratio": 40.0, "mom_1h": None, "band_pos": None, "change_pct": 0})
    assert b > a
    assert b < a * 3


# --------------------------------------------------------------- #
# ADAY SECIMI — kademe 2'ye ne gidiyor
# --------------------------------------------------------------- #
@pytest.fixture()
def sahte_db(monkeypatch):
    """market_library tablosunu taklit eden minik bir db katmani."""
    tablo = [
        {"symbol": "BTCUSDT", "quote_volume": 900e6, "ilgi": 0.1},
        {"symbol": "ETHUSDT", "quote_volume": 500e6, "ilgi": 0.2},
        {"symbol": "SOLUSDT", "quote_volume": 200e6, "ilgi": 0.3},
        {"symbol": "BNBUSDT", "quote_volume": 90e6, "ilgi": 0.15},
        {"symbol": "XRPUSDT", "quote_volume": 80e6, "ilgi": 0.05},
    ] + [{"symbol": f"ALT{i}USDT", "quote_volume": 2e6, "ilgi": 9.0 - i * 0.1}
         for i in range(40)]

    def query(sql, params=()):
        min_h = params[0] if params else 0
        limit = params[1] if len(params) > 1 else 1000
        satirlar = [r for r in tablo if r["quote_volume"] >= min_h]
        anahtar = "quote_volume" if "quote_volume DESC" in sql else "ilgi"
        satirlar.sort(key=lambda r: -r[anahtar])
        return [{"symbol": r["symbol"]} for r in satirlar[:limit]]

    monkeypatch.setattr(ml.db, "query", query)
    return tablo


def test_adaylar_kucuk_kontratlari_iceriyor(sahte_db):
    """Eski kural (hacme gore ilk N) bunlarin HICBIRINI secmezdi."""
    secim = ml.adaylar(n=20)
    assert any(s.startswith("ALT") for s in secim)


def test_adaylar_cekirdegi_de_tutuyor(sahte_db):
    """BTC ilgi puani dusuk olsa bile listede kalmali.

    Sakin bir BTC ilgi siralamasinda 40 altcoinin altinda kalir; ama
    kullanicinin en cok baktigi sembol o. Cekirdek payi bunu garanti eder.
    """
    secim = ml.adaylar(n=20)
    assert "BTCUSDT" in secim


def test_zorunlu_semboller_her_zaman_iceride(sahte_db):
    """Acik pozisyon taramanin disinda kalamaz — en kotu kor nokta bu olurdu."""
    secim = ml.adaylar(n=10, zorunlu=["PEPEUSDT"])
    assert "PEPEUSDT" in secim
    assert len(secim) <= 10


def test_adaylar_tekrar_etmiyor(sahte_db):
    secim = ml.adaylar(n=25, zorunlu=["BTCUSDT", "ALT0USDT"])
    assert len(secim) == len(set(secim))


def test_adaylar_limiti_asmiyor(sahte_db):
    for n in (5, 12, 30, 60):
        assert len(ml.adaylar(n=n)) <= max(5, n)
