"""BTC REJIMI — altcoin planlarinin ucuncu boyutu.

Kullanicinin tespiti: "genel hareketler BTC uzerinden donuyor; acik short
islemlerimiz var, BTC yukari gidiyor ve zarar ediyoruz."

Buradaki testler iki soruyu ayri ayri olcuyor:
  1. Korelasyon ve beta ARITMETIGI dogru mu? (yanlissa uyari da yanlis)
  2. Uyari mantigi hangi durumda konusuyor, hangi durumda susuyor?
"""
import math
import pytest
from app.services import btc_rejim as br


def _seri(getiriler, baslangic=100.0):
    """Verilen log getirilerden kapanis serisi uretir."""
    out = [baslangic]
    for g in getiriler:
        out.append(out[-1] * math.exp(g))
    return out


# ------------------------------------------------------------------ #
# KORELASYON VE BETA ARITMETIGI
# ------------------------------------------------------------------ #
def test_tam_korelasyon():
    """Ayni getiriler -> r = 1, beta = 1."""
    g = [0.01, -0.02, 0.015, -0.005] * 20
    s = _seri(g)
    k = br.korelasyon_beta(s, s)
    assert k["r"] == pytest.approx(1.0, abs=0.01)
    assert k["beta"] == pytest.approx(1.0, abs=0.01)


def test_iki_kat_beta():
    """Coin BTC'nin iki kati oynuyorsa beta 2, korelasyon hala 1.

    Bu ayrimin pratik karsiligi: yuksek korelasyonlu yuksek beta,
    'kaldirac li BTC' demektir. BTC %2 giderse bu kontrat %4 gider ve
    coinin kendi gostergeleri bunu durdurmaz.
    """
    g = [0.01, -0.02, 0.015, -0.005] * 20
    btc = _seri(g)
    coin = _seri([x * 2 for x in g])
    k = br.korelasyon_beta(coin, btc)
    assert k["r"] == pytest.approx(1.0, abs=0.01)
    assert k["beta"] == pytest.approx(2.0, abs=0.05)


def test_ters_korelasyon():
    g = [0.01, -0.02, 0.015, -0.005] * 20
    btc = _seri(g)
    coin = _seri([-x for x in g])
    k = br.korelasyon_beta(coin, btc)
    assert k["r"] == pytest.approx(-1.0, abs=0.01)
    assert k["beta"] == pytest.approx(-1.0, abs=0.05)


def test_yetersiz_bar_None_doner():
    """Kisa seride korelasyon gurultuden ibarettir.

    Uydurulmus bir korelasyon rakami, korelasyon olmamasindan kotudur:
    kullanici ona bakip karar verir.
    """
    assert br.korelasyon_beta([100, 101, 102], [100, 101, 102]) is None


def test_sabit_seri_None_doner():
    """Varyansi sifir olan seride korelasyon tanimsizdir — sifira bolme."""
    sabit = [100.0] * 200
    g = [0.01, -0.01] * 100
    assert br.korelasyon_beta(sabit, _seri(g)) is None


def test_korelasyon_sinirlari_asmiyor():
    import random
    random.seed(7)
    a = _seri([random.gauss(0, 0.01) for _ in range(200)])
    b = _seri([random.gauss(0, 0.01) for _ in range(200)])
    k = br.korelasyon_beta(a, b)
    assert -1.0 <= k["r"] <= 1.0


# ------------------------------------------------------------------ #
# UYARI MANTIGI
# ------------------------------------------------------------------ #
HIGH = {"r": 0.85, "beta": 1.7}
LOW = {"r": 0.15, "beta": 0.3}


def test_btc_yukari_short_yuksek_korelasyon_UYARIR():
    """Kullanicinin yasadigi durumun ta kendisi."""
    u = br.uyum("SHORT", "yukari", HIGH)
    assert u["durum"] == "carpisiyor"
    assert "akıntıya karşı" in u["mesaj"]


def test_btc_asagi_long_yuksek_korelasyon_UYARIR():
    assert br.uyum("LONG", "guclu_asagi", HIGH)["durum"] == "carpisiyor"


def test_dusuk_korelasyonda_uyari_YUMUSAK():
    """Korelasyon dusukse kontrat kendi hikayesini yasiyor olabilir.

    Her ters yonlu plani ayni sertlikte uyarmak, uyariyi degersizlestirir:
    kullanici bir sure sonra hepsini gormezden gelir.
    """
    u = br.uyum("SHORT", "yukari", LOW)
    assert u["durum"] == "zayif_carpisma"


def test_ayni_yon_destek_ama_UYARISIZ_DEGIL():
    """Akinti arkandayken bile kurulumun kendi gecerliligi kanitlanmaz.

    'BTC ayni yonde' bir onay degil; BTC donerse yuksek betali kontrat
    daha sert doner. Mesaj bunu soylemek zorunda.
    """
    u = br.uyum("LONG", "yukari", HIGH)
    assert u["durum"] == "destekliyor"
    assert "kanıtlamaz" in u["mesaj"]


def test_yatay_rejimde_catisma_yok():
    for yon in ("LONG", "SHORT"):
        assert br.uyum(yon, "yatay", HIGH)["durum"] == "notr"


def test_korelasyon_yoksa_uydurulmuyor():
    u = br.uyum("SHORT", "yukari", None)
    assert u["durum"] == "bilinmiyor"
    assert u["r"] is None


def test_genislik_mesaja_ekleniyor():
    """Evrenin %85'i yesilse bu coine ozel bir hikaye degildir."""
    u = br.uyum("SHORT", "yukari", HIGH, {"yukselen_yuzde": 85.0})
    assert "piyasa geneli" in u["mesaj"]


def test_genislik_dusukse_eklenmiyor():
    u = br.uyum("SHORT", "yukari", HIGH, {"yukselen_yuzde": 52.0})
    assert "piyasa geneli" not in u["mesaj"]


# ------------------------------------------------------------------ #
# REJIM SINIFLAMASI
# ------------------------------------------------------------------ #
def test_yukselen_seri_yukari_rejim():
    kapanislar = [100.0 * (1.004 ** i) for i in range(220)]
    kod, etiket = br._rejim_kodu(kapanislar, 0.8, 6.0)
    assert kod in ("yukari", "guclu_yukari")


def test_dusen_seri_asagi_rejim():
    kapanislar = [100.0 * (0.996 ** i) for i in range(220)]
    kod, _ = br._rejim_kodu(kapanislar, -0.8, -6.0)
    assert kod in ("asagi", "guclu_asagi")


def test_yatay_seri_yatay_rejim():
    kapanislar = [100.0 + (1 if i % 2 else -1) * 0.05 for i in range(220)]
    kod, _ = br._rejim_kodu(kapanislar, 0.0, 0.0)
    assert kod == "yatay"


# ------------------------------------------------------------------ #
# SIRALAMAYA ETKISI
# ------------------------------------------------------------------ #
def test_btc_ye_karsi_plan_siralamada_geriye_duser():
    """Ceza var ama VETO yok.

    Korelasyon nedensellik degil, beta sabit degil. 'BTC yukari, butun
    short'lari ele' demek yanlis olurdu — bazen dogru islem tam odur.
    Ama esit sartlarda akintiya karsi duran kurulumu basa koymak da
    yanlis. Test ikisini birden dogruluyor: geriye duser ama listede
    kalir.
    """
    from app.services import mentor_plan
    temiz = {"rr": 3.0, "kalite": "güçlü uyum", "btc": {"durum": "notr"}}
    ters = {"rr": 3.0, "kalite": "güçlü uyum", "btc": {"durum": "carpisiyor"}}
    assert mentor_plan._siralama_puani(temiz) < mentor_plan._siralama_puani(ters)
    # Ama cok daha iyi bir rr hala one gecebilir: eleme degil, agirlik.
    ters_iyi = {"rr": 4.0, "kalite": "güçlü uyum", "btc": {"durum": "carpisiyor"}}
    zayif_temiz = {"rr": 1.3, "kalite": "zayıf / çelişkili", "btc": {"durum": "notr"}}
    assert mentor_plan._siralama_puani(ters_iyi) < mentor_plan._siralama_puani(zayif_temiz)


def test_gurultuden_ibaret_ayrim_rejim_uretmiyor():
    """Testle yakalanan gercek hata.

    Ilk surumde EMA20 > EMA50 karsilastirmasinin esigi yoktu. Dumduz bir
    piyasada bu iki ortalama baz puanin kesri kadar ayrilir ve isareti
    tamamen gurultudur; buna ragmen tam bir oy sayiliyordu. Sonuc: hicbir
    yere gitmeyen BTC 'yukari egilimli' ilan ediliyor, sistem her short'a
    uyari basiyor ve kullanici bir sure sonra uyarilarin hepsini
    gormezden geliyor. Isaretsiz bir uyari, uyari olmamasindan kotudur.
    """
    # %0.05'lik salinim: hicbir yone gitmeyen bir piyasa
    kapanislar = [100.0 + (0.05 if i % 2 else -0.05) for i in range(220)]
    kod, _ = br._rejim_kodu(kapanislar, 0.02, 0.03, 0.12)
    assert kod == "yatay"


def test_olu_bolge_oynakliga_gore_olcekleniyor():
    """ATR %3 olan bir gunde %0.4'luk EMA ayrimi da anlamsizdir."""
    # Hafif yukari egimli ama cok oynak bir seri
    kapanislar = [100.0 * (1.0002 ** i) + (2.0 if i % 2 else -2.0) for i in range(220)]
    sakin = br._rejim_kodu(kapanislar, 0.1, 0.4, 0.15)[0]
    oynak = br._rejim_kodu(kapanislar, 0.1, 0.4, 4.0)[0]
    # Ayni seri, yuksek oynaklik varsayiminda daha temkinli siniflanmali.
    siralama = {"guclu_asagi": -2, "asagi": -1, "yatay": 0, "yukari": 1, "guclu_yukari": 2}
    assert abs(siralama[oynak]) <= abs(siralama[sakin])
