"""PLAN BILDIRIMI — girisleri kacirmamak icin, kovalamayi tesvik etmeden.

Bu testlerin asil derdi TEK BIR SEY: bildirim kanalinin olmemesi.
Filtresiz gonderim gunde yuzlerce bildirim demek; kullanici kanali
susturur ve o andan sonra sistem hicbir sey bildirmemis olur. Uc kapinin
(sembol bekleme suresi, tarama basina, gun basina) her biri ayri ayri
sinaniyor.

Ikinci derdi: bildirimin bir EMIR gibi degil, bir OZET gibi okunmasi ve
icindeki sayilarin planla birebir ayni olmasi.
"""
import time

import pytest

from app.services import plan_bildirim as pb
from app.services.mentor_plan import _tp_merdiveni


# ------------------------------------------------------------------ #
# YARDIMCILAR
# ------------------------------------------------------------------ #
def _plan(symbol="BTCUSDT", yon="LONG", rr=2.4, gz="simdi",
          btc="notr", hedef=True, uyari=None):
    return {
        "var_mi": True, "symbol": symbol, "yon": yon, "rr": rr,
        "giris": 100.0, "stop": 98.0, "stop_r_yuzde": 2.0,
        "hedef_detay": _tp_merdiveni(yon, 100.0, 2.0, [103.0, 106.0]) if hedef else [],
        "boyut": {"riske_edilen": 3.8, "kaldirac": 5},
        "giris_zamani": {"durum": gz},
        "btc": {"durum": btc},
        "uyarilar": [uyari] if uyari else [],
    }


@pytest.fixture(autouse=True)
def temiz_durum(monkeypatch):
    """Her test kendi bos gecmisiyle basliyor; disk durumuna dokunmuyoruz."""
    kutu = {"d": {"son": {}, "gun": "", "gun_sayi": 0}}
    monkeypatch.setattr(pb, "_durum", lambda: dict(kutu["d"], son=dict(kutu["d"]["son"])))
    monkeypatch.setattr(pb, "_kaydet", lambda d: kutu.__setitem__("d", d))
    return kutu


def _ayar(monkeypatch, **kw):
    cfg = {"enabled": True, "yon": "LONG", "min_rr": 1.8, "zamanlama": "simdi",
           "tekrar_saat": 6, "gunluk_azami": 12, "tarama_azami": 3,
           "btc_catismasini_atla": True}
    cfg.update(kw)
    monkeypatch.setattr(pb.runtime, "get", lambda: {"plan_bildirim": cfg})
    return cfg


# ------------------------------------------------------------------ #
# METIN
# ------------------------------------------------------------------ #
def test_metin_bes_hedefi_de_yaziyor():
    m = pb.metin(_plan())
    assert m["baslik"].startswith("🟢 BTC LONG")
    assert "2.4R" in m["baslik"]
    # Bes kademe, bolu isaretiyle ayrilmis -> dort ayirici
    tp = [s for s in m["govde"].split("\n") if s.startswith("TP ")][0]
    assert tp.count(" / ") == 4


def test_metin_stopu_hedeflerden_once_yaziyor():
    """Once ne riske ediyorum, sonra ne kazanabilirim."""
    g = pb.metin(_plan())["govde"]
    assert g.index("Stop") < g.index("TP ")


def test_metin_short_kirmizi():
    assert pb.metin(_plan(yon="SHORT"))["baslik"].startswith("🔴")


def test_metin_uyariyi_gizlemiyor():
    g = pb.metin(_plan(uyari="RSI aşırı bölgede"))["govde"]
    assert "RSI aşırı bölgede" in g


def test_metin_zamanlama_etiketi():
    assert "ŞİMDİ" in pb.metin(_plan())["govde"]


def test_metin_govde_siniri():
    uzun = "x" * 900
    assert len(pb.metin(_plan(uyari=uzun))["govde"]) <= pb.GOVDE_AZAMI


@pytest.mark.parametrize("deger,beklenen", [
    (68420.5, "68.420"), (245.67, "245.67"), (1.2345, "1.234"),
    (0.04321, "0.04321"), (0.00000123, "0.00000123"), (None, "—"),
])
def test_fiyat_buyuklugune_gore_basamak(deger, beklenen):
    """Sabit basamak sayisi PEPE'de bilginin tamamini siler."""
    assert pb._fiyat(deger) == beklenen


# ------------------------------------------------------------------ #
# SECIM KAPILARI
# ------------------------------------------------------------------ #
def test_yon_suzgeci_shortu_eliyor(monkeypatch):
    _ayar(monkeypatch, yon="LONG")
    s = pb.secim([_plan(yon="SHORT")])
    assert s["gonderilecek"] == []
    assert "yön SHORT" in s["elenen"][0]["sebep"]


def test_yon_both_ikisini_de_geciriyor(monkeypatch):
    _ayar(monkeypatch, yon="BOTH")
    s = pb.secim([_plan("A"), _plan("B", yon="SHORT")])
    assert len(s["gonderilecek"]) == 2


def test_dusuk_rr_eleniyor(monkeypatch):
    _ayar(monkeypatch, min_rr=2.5)
    s = pb.secim([_plan(rr=2.0)])
    assert s["gonderilecek"] == []
    assert "rr 2.0" in s["elenen"][0]["sebep"]


def test_gec_kalinmis_kurulum_bildirilmiyor(monkeypatch):
    """Gec kalmis kurulumu bildirmek, tam olarak kacinilmak istenen
    kovalamayi tesvik eder."""
    _ayar(monkeypatch, zamanlama="simdi")
    s = pb.secim([_plan(gz="gec")])
    assert s["gonderilecek"] == []
    assert "zamanlama gec" in s["elenen"][0]["sebep"]


def test_zamanlama_hepsi_ise_bekle_de_gidiyor(monkeypatch):
    _ayar(monkeypatch, zamanlama="hepsi")
    assert len(pb.secim([_plan(gz="bekle")])["gonderilecek"]) == 1


def test_btc_catismasi_atlaniyor(monkeypatch):
    _ayar(monkeypatch)
    s = pb.secim([_plan(btc="carpisiyor")])
    assert "BTC çatışması" in s["elenen"][0]["sebep"]


def test_hedefsiz_plan_bildirilmiyor(monkeypatch):
    _ayar(monkeypatch)
    s = pb.secim([_plan(hedef=False)])
    assert "hedef yok" in s["elenen"][0]["sebep"]


# ------------------------------------------------------------------ #
# TEKRAR / SINIR KAPILARI — kanalin olmemesi buna bagli
# ------------------------------------------------------------------ #
def test_ayni_sembol_bekleme_suresinde_tekrar_gitmiyor(monkeypatch, temiz_durum):
    _ayar(monkeypatch, tekrar_saat=6)
    temiz_durum["d"]["son"]["BTCUSDT"] = int(time.time() * 1000) - 3600_000
    s = pb.secim([_plan("BTCUSDT")])
    assert s["gonderilecek"] == []
    assert "tekrar kapısı" in s["elenen"][0]["sebep"]


def test_bekleme_suresi_dolunca_tekrar_gidiyor(monkeypatch, temiz_durum):
    _ayar(monkeypatch, tekrar_saat=6)
    temiz_durum["d"]["son"]["BTCUSDT"] = int(time.time() * 1000) - 7 * 3600_000
    assert len(pb.secim([_plan("BTCUSDT")])["gonderilecek"]) == 1


def test_tarama_basina_sinir(monkeypatch):
    _ayar(monkeypatch, tarama_azami=2)
    s = pb.secim([_plan("A"), _plan("B"), _plan("C"), _plan("D")])
    assert len(s["gonderilecek"]) == 2
    assert any("tarama başına" in e["sebep"] for e in s["elenen"])


def test_gunluk_sinir(monkeypatch, temiz_durum):
    _ayar(monkeypatch, gunluk_azami=1, tarama_azami=10)
    temiz_durum["d"]["gun"] = pb._bugun()
    temiz_durum["d"]["gun_sayi"] = 0
    s = pb.secim([_plan("A"), _plan("B")])
    assert len(s["gonderilecek"]) == 1
    assert any("günlük sınır" in e["sebep"] for e in s["elenen"])


def test_gun_degisince_sayac_sifirlaniyor(monkeypatch, temiz_durum):
    _ayar(monkeypatch, gunluk_azami=2)
    temiz_durum["d"]["gun"] = "2020-01-01"
    temiz_durum["d"]["gun_sayi"] = 99
    assert len(pb.secim([_plan("A")])["gonderilecek"]) == 1


def test_her_elenen_sebep_tasiyor(monkeypatch):
    """Sessiz eleme, 'neden bildirim gelmiyor' sorusunu cevapsiz birakir."""
    _ayar(monkeypatch)
    s = pb.secim([_plan("A", yon="SHORT"), _plan("B", rr=0.5),
                  _plan("C", gz="gec"), _plan("D", btc="carpisiyor")])
    assert len(s["elenen"]) == 4
    assert all(e["sebep"] for e in s["elenen"])


# ------------------------------------------------------------------ #
# GONDERIM
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_kapaliyken_gondermiyor(monkeypatch):
    _ayar(monkeypatch, enabled=False)
    r = await pb.bildir([_plan()])
    assert r["gonderildi"] == 0 and r["sebep"] == "kapalı"


@pytest.mark.asyncio
async def test_gonderim_basarisizsa_tekrar_kapisi_kapanmiyor(monkeypatch, temiz_durum):
    """Gonderilemeyen bildirim GONDERILMIS sayilirsa, kullanici o kurulumu
    hem kacirir hem de alti saat boyunca bir daha haber alamaz."""
    _ayar(monkeypatch)

    async def basarisiz(*a, **k):
        return {"ok": False, "error": "abonelik yok"}

    monkeypatch.setattr(pb.webpush, "send", basarisiz)
    r = await pb.bildir([_plan("BTCUSDT")])
    assert r["gonderildi"] == 0
    assert "BTCUSDT" not in temiz_durum["d"]["son"]


@pytest.mark.asyncio
async def test_basarili_gonderim_kapiyi_kapatiyor(monkeypatch, temiz_durum):
    _ayar(monkeypatch)

    async def ok(*a, **k):
        return {"ok": True, "sent": 1}

    monkeypatch.setattr(pb.webpush, "send", ok)
    r = await pb.bildir([_plan("BTCUSDT")])
    assert r["gonderildi"] == 1
    assert "BTCUSDT" in temiz_durum["d"]["son"]
    assert temiz_durum["d"]["gun_sayi"] == 1


@pytest.mark.asyncio
async def test_push_patlasa_bile_akis_kirilmiyor(monkeypatch):
    """Bildirim gonderilemedigi icin taramanin sonucu kaybolmamali."""
    _ayar(monkeypatch)

    async def patla(*a, **k):
        raise RuntimeError("ağ gitti")

    monkeypatch.setattr(pb.webpush, "send", patla)
    r = await pb.bildir([_plan()])
    assert r["ok"] is True and r["gonderildi"] == 0


@pytest.mark.asyncio
async def test_her_sembol_kendi_etiketiyle(monkeypatch):
    """Ayni sembolun eski bildirimi yenisiyle degismeli, ust uste yigilmamali."""
    _ayar(monkeypatch)
    etiketler = []

    async def yakala(baslik, govde, **k):
        etiketler.append(k.get("tag"))
        return {"ok": True}

    monkeypatch.setattr(pb.webpush, "send", yakala)
    await pb.bildir([_plan("BTCUSDT"), _plan("ETHUSDT")])
    assert etiketler == ["plan-BTCUSDT", "plan-ETHUSDT"]
