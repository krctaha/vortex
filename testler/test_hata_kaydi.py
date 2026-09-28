"""SUNUCU HATA KAYDI — "Internal Server Error" cumlesini bir sebebe cevirir.

Kullanici arayuzde yalnizca o cumleyi goruyordu: hangi uc, hangi istisna,
hangi satir — hicbiri yok. Sunucu logunda duruyordu ama oraya bakmak icin
SSH gerekiyordu ve bu, bir arayuz hatasini bildirmenin makul yolu degil.
"""
from app import hata_kaydi


def _temiz():
    hata_kaydi.temizle()


def test_kaydediyor_ve_ters_sirada_donduruyor():
    _temiz()
    for i in range(3):
        try:
            raise ValueError(f"hata {i}")
        except ValueError as e:
            hata_kaydi.kaydet(f"/api/x{i}", e)
    son = hata_kaydi.son(10)
    assert len(son) == 3
    # En yeni once: kullanici "az once ne kirildi" diye bakiyor.
    assert son[0]["mesaj"] == "hata 2"
    assert son[0]["tip"] == "ValueError"
    assert son[0]["yol"] == "/api/x2"


def test_kapasite_asilmiyor():
    """Bellekte sinirsiz hata biriktirmek sizinti olurdu."""
    _temiz()
    for i in range(hata_kaydi.KAPASITE + 20):
        try:
            raise RuntimeError(str(i))
        except RuntimeError as e:
            hata_kaydi.kaydet("/api/y", e)
    assert len(hata_kaydi.son(999)) == hata_kaydi.KAPASITE


def test_traceback_kuyrugu_var_ama_sinirli():
    """Iz kaydin en bilgilendirici kismi ama sinirsiz olamaz."""
    _temiz()
    try:
        raise KeyError("derin")
    except KeyError as e:
        hata_kaydi.kaydet("/api/z", e)
    iz = hata_kaydi.son(1)[0]["iz"]
    assert "KeyError" in iz
    assert len(iz) <= 1200


def test_kaydet_asla_patlamaz():
    """Hata kaydetmenin kendisi hata uretirse ariza iki katina cikardi."""
    _temiz()
    hata_kaydi.kaydet(None, None)          # type: ignore[arg-type]
    hata_kaydi.kaydet("/api/q", Exception("normal"))
    assert len(hata_kaydi.son(10)) >= 1


def test_uzun_mesaj_kirpiliyor():
    _temiz()
    try:
        raise ValueError("x" * 5000)
    except ValueError as e:
        hata_kaydi.kaydet("/a" * 500, e)
    k = hata_kaydi.son(1)[0]
    assert len(k["mesaj"]) <= 300
    assert len(k["yol"]) <= 120
