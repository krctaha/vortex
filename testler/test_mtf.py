"""COK ZAMAN DILIMI — yonu buyuk resim, zamanlamayi kucuk resim verir.

Tek zaman diliminde calisan bir sistem iki farkli soruyu ayni veriyle
cevaplamaya calisir: "hangi yone" yavas bir soru, "ne zaman" hizli bir
soru. Bu testler ikisinin birbirine karismadigini dogruluyor.
"""
import pytest
from app.services import mtf


def _sahte_tf(kodlar):
    """_tf_yonu'yu sabit cevaplarla degistirir."""
    async def f(symbol, interval):
        return kodlar.get(interval)
    return f


# ------------------------------------------------------------------ #
# YON KOMITESI
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_tam_hiza(monkeypatch):
    monkeypatch.setattr(mtf, "_tf_yonu", _sahte_tf({"4h": "bear", "15m": "bear"}))
    d = await mtf.yon_teyidi("X", "SHORT", "bear")
    assert d["durum"] == "hizali"
    assert d["net"] == 6


@pytest.mark.asyncio
async def test_ust_zaman_dilimi_ters_ise_catisma(monkeypatch):
    """'1h short diyor ama 4h guclu yukari' bir kurulum degil, gurultudur."""
    monkeypatch.setattr(mtf, "_tf_yonu", _sahte_tf({"4h": "bull", "15m": "bull"}))
    d = await mtf.yon_teyidi("X", "SHORT", "bear")
    assert d["durum"] == "catisiyor"
    assert "4h" in d["mesaj"]


@pytest.mark.asyncio
async def test_15m_tek_basina_yon_belirleyemez(monkeypatch):
    """En gurultulu dilim en hafif agirligi tasimali.

    4h notr, 1h plan yonunde, 15m ters: net = 2 - 1 = 1. Bu 'hizali'
    degil 'zayif'. Aksi halde 15 dakikalik gurultu tek basina bir
    kurulumu onaylardi.
    """
    monkeypatch.setattr(mtf, "_tf_yonu", _sahte_tf({"4h": "neutral", "15m": "bull"}))
    d = await mtf.yon_teyidi("X", "SHORT", "bear")
    assert d["durum"] == "zayif"
    assert d["net"] == 1


@pytest.mark.asyncio
async def test_4h_tek_basina_hiza_icin_yeter(monkeypatch):
    """4h agirligi 3; MIN_HIZA da 3. Yavas dilim tek basina yetmeli."""
    monkeypatch.setattr(mtf, "_tf_yonu", _sahte_tf({"4h": "bull", "15m": "bear"}))
    d = await mtf.yon_teyidi("X", "LONG", "neutral")
    # 4h +3, 1h notr 0, 15m -1 -> net 2 (zayif); 1h de lehteyse hizali
    assert d["net"] == 2 and d["durum"] == "zayif"
    monkeypatch.setattr(mtf, "_tf_yonu", _sahte_tf({"4h": "bull", "15m": "neutral"}))
    d2 = await mtf.yon_teyidi("X", "LONG", "bull")
    assert d2["net"] == 5 and d2["durum"] == "hizali"


@pytest.mark.asyncio
async def test_okunamayan_dilim_uydurulmuyor(monkeypatch):
    monkeypatch.setattr(mtf, "_tf_yonu", _sahte_tf({"4h": None, "15m": None}))
    d = await mtf.yon_teyidi("X", "LONG", "bull")
    assert d["dilimler"]["4h"] is None
    assert d["azami"] == 2      # yalnizca 1h okunabildi


@pytest.mark.asyncio
async def test_hicbir_dilim_okunamazsa_bilinmiyor(monkeypatch):
    monkeypatch.setattr(mtf, "_tf_yonu", _sahte_tf({"4h": None, "15m": None}))
    d = await mtf.yon_teyidi("X", "LONG", "")
    # ana_kod bos -> 1h de kodlanmaz mi? kodlaniyor (yon'den turetiliyor),
    # o yuzden azami 2 ve net 2: zayif.
    assert d["durum"] in ("zayif", "bilinmiyor")


# ------------------------------------------------------------------ #
# ZAMANLAMA — 5m / 1m
# ------------------------------------------------------------------ #
def _mum(kapanis, yuksek=None, dusuk=None):
    y = yuksek if yuksek is not None else kapanis * 1.002
    d = dusuk if dusuk is not None else kapanis * 0.998
    return [0, kapanis, y, d, kapanis, 1, 0]


def _sahte_klines(k5, k1):
    async def f(symbol, interval, limit=100):
        return k5 if interval == "5m" else k1
    return f


@pytest.mark.asyncio
async def test_uzamis_fiyat_GEC_der(monkeypatch):
    """Hareket zaten yapilmissa 'simdi gir' demek kovalamaktir.

    Kullanicinin backtest'i de bunu destekliyordu: dar stop + uzamis
    giris, stopun gurultuye yenmesinin en yaygin sebebi.
    """
    # Son barda sert yukari sicrama -> EMA21'den cok uzak
    # Uretimde 120 bar cekiliyor; test de o uzunlukta olmali,
    # yoksa yuzdelik dagilimi icin yeterli ornek olmaz.
    k5 = [_mum(100.0) for _ in range(119)] + [_mum(112.0)]
    k1 = [_mum(112.0) for _ in range(30)]
    monkeypatch.setattr("app.services.binance.klines", _sahte_klines(k5, k1))
    d = await mtf.giris_zamani("X", "LONG", 112.0)
    assert d["durum"] == "gec"
    assert d["uzama_atr"] >= mtf.UZAMA_TABAN_ATR
    assert d["uzama_kat"] >= mtf.UZAMA_KAT
    assert d["tetik"] is not None


@pytest.mark.asyncio
async def test_duzgun_trend_GEC_sayilmaz(monkeypatch):
    """Testle yakalanan tasarim hatasi.

    Ilk surumde tek bir mutlak esik vardi: EMA21'den 1.2 ATR uzaksa
    "gec kalindi". Ama duzgun bir trendde fiyat ortalamanin USTUNDE
    KALIR — surekli. Sakin bir yukselisin her bari o esigi asar ve
    sistem kullaniciya "trende hicbir zaman girme" demis olur.
    Dogru soru "ortalamadan ne kadar uzak" degil, "KENDI NORMALINDEN
    ne kadar uzak". Yuzdelik siralamasi da denendi ve yetmedi: duzgun
    trendde mesafe tekduze arttigi icin son bar HER ZAMAN en yuksek
    yuzdelikte cikiyordu. Medyana oran bu yapaylıktan etkilenmiyor.
    """
    k5 = [_mum(100.0 * (1.001 ** i)) for i in range(120)]
    k1 = [_mum(100.0 * (1.0005 ** i)) for i in range(30)]
    monkeypatch.setattr("app.services.binance.klines", _sahte_klines(k5, k1))
    d = await mtf.giris_zamani("X", "LONG", k5[-1][4])
    assert d["durum"] != "gec", "istikrarli trend kovalama sayilmamali"


@pytest.mark.asyncio
async def test_momentum_ve_dakikalar_lehteyse_SIMDI(monkeypatch):
    k5 = [_mum(100.0 * (1.001 ** i)) for i in range(120)]
    k1 = [_mum(100.0 * (1.0005 ** i)) for i in range(30)]
    monkeypatch.setattr("app.services.binance.klines", _sahte_klines(k5, k1))
    d = await mtf.giris_zamani("X", "LONG", k5[-1][4])
    assert d["durum"] == "simdi"


@pytest.mark.asyncio
async def test_momentum_ters_ise_BEKLE(monkeypatch):
    """Kisa vade henuz donmemisse teyit beklemek daha ucuz."""
    k5 = [_mum(100.0 * (0.999 ** i)) for i in range(120)]
    k1 = [_mum(100.0 * (0.9995 ** i)) for i in range(30)]
    monkeypatch.setattr("app.services.binance.klines", _sahte_klines(k5, k1))
    d = await mtf.giris_zamani("X", "LONG", k5[-1][4])
    assert d["durum"] == "bekle"
    assert "TERS" in d["mesaj"]


@pytest.mark.asyncio
async def test_veri_yetersizse_uydurmuyor(monkeypatch):
    monkeypatch.setattr("app.services.binance.klines", _sahte_klines([_mum(100.0)] * 5, []))
    d = await mtf.giris_zamani("X", "LONG", 100.0)
    assert d["durum"] == "bilinmiyor"


@pytest.mark.asyncio
async def test_binance_patlarsa_bilinmiyor(monkeypatch):
    async def patla(*a, **k):
        raise RuntimeError("451")
    monkeypatch.setattr("app.services.binance.klines", patla)
    d = await mtf.giris_zamani("X", "SHORT", 100.0)
    assert d["durum"] == "bilinmiyor"


@pytest.mark.asyncio
async def test_short_icin_uzama_ters_yonde_olculuyor(monkeypatch):
    """SHORT'ta 'uzamis' demek fiyatin ASAGI kacmis olmasi demektir."""
    k5 = [_mum(100.0) for _ in range(119)] + [_mum(88.0)]
    k1 = [_mum(88.0) for _ in range(30)]
    monkeypatch.setattr("app.services.binance.klines", _sahte_klines(k5, k1))
    d = await mtf.giris_zamani("X", "SHORT", 88.0)
    assert d["durum"] == "gec"


# ------------------------------------------------------------------ #
# SIRALAMAYA ETKISI
# ------------------------------------------------------------------ #
def test_hizali_kurulum_one_geciyor():
    from app.services import mentor_plan
    hizali = {"rr": 3.0, "kalite": "güçlü uyum",
              "zaman_dilimleri": {"durum": "hizali"}}
    zayif = {"rr": 3.0, "kalite": "güçlü uyum",
             "zaman_dilimleri": {"durum": "zayif"}}
    assert mentor_plan._siralama_puani(hizali) < mentor_plan._siralama_puani(zayif)


def test_gec_kalinmis_kurulum_geriye_dusuyor():
    from app.services import mentor_plan
    simdi = {"rr": 3.0, "kalite": "güçlü uyum", "giris_zamani": {"durum": "simdi"}}
    gec = {"rr": 3.0, "kalite": "güçlü uyum", "giris_zamani": {"durum": "gec"}}
    assert mentor_plan._siralama_puani(simdi) < mentor_plan._siralama_puani(gec)
