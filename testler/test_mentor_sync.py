"""BORSA SENKRONU — gunlugu Binance'ten dolduran katman.

Buradaki testlerin hepsi tek bir sorunun etrafinda: sistem OLCUM
uyduruyor mu? Elle giristen kurtulmanin bedeli, borsadan gelmeyen bir
sayiyi tahminle doldurmak olsaydi, kazanc degil zarar olurdu — cunku
uydurulmus bir stop ya da uydurulmus bir cikis fiyati karnenin TAMAMINI
sessizce yanlislar ve kullanici bunu asla fark etmez.
"""
import pytest
from app.services import mentor_sync as ms


ALGO_STOP = {"symbol": "BTCUSDT", "algoType": "STOP_MARKET", "triggerPrice": "95000"}
ALGO_TP = {"symbol": "BTCUSDT", "algoType": "TAKE_PROFIT_MARKET", "triggerPrice": "110000"}


def test_stop_ve_hedef_borsadan_okunur():
    stop, hedef = ms.borsa_stop_hedef([ALGO_STOP, ALGO_TP], "BTCUSDT", "LONG", 100000)
    assert stop == 95000
    assert hedef == 110000


def test_borsada_stop_yoksa_UYDURULMAZ():
    """En onemli test.

    Girise ya da "yaklasik bir seviyeye" stop yazmak cok kolay olurdu ve
    hicbir hata mesaji vermezdi. Ama stop = giris demek risk = 0 demek,
    risk = 0 demek R = sonsuz demek. Tek bir uydurulmus stop, SQN'i ve
    bootstrap guven araligini kalici olarak bozar.
    """
    stop, hedef = ms.borsa_stop_hedef([ALGO_TP], "BTCUSDT", "LONG", 100000)
    assert stop is None
    assert hedef == 110000


def test_ters_duran_stop_reddedilir_LONG():
    """LONG'ta stop girisin USTUNDE olamaz.

    Borsada boyle bir emir bulunabiliyor (eski hedge kalintisi, yanlis
    kurulmus TP). Onu stop diye kaydetmek R'nin ISARETINI ters cevirir:
    kazanan islem kayip, kaybeden kazanc gorunur. Sessiz ve olumcul.
    """
    ters = {"symbol": "BTCUSDT", "algoType": "STOP_MARKET", "triggerPrice": "105000"}
    stop, _ = ms.borsa_stop_hedef([ters], "BTCUSDT", "LONG", 100000)
    assert stop is None


def test_ters_duran_stop_reddedilir_SHORT():
    ters = {"symbol": "BTCUSDT", "algoType": "STOP_MARKET", "triggerPrice": "95000"}
    stop, _ = ms.borsa_stop_hedef([ters], "BTCUSDT", "SHORT", 100000)
    assert stop is None


def test_dogru_yonlu_short_stopu_kabul_edilir():
    dogru = {"symbol": "BTCUSDT", "algoType": "STOP_MARKET", "triggerPrice": "105000"}
    stop, _ = ms.borsa_stop_hedef([dogru], "BTCUSDT", "SHORT", 100000)
    assert stop == 105000


def test_baska_sembolun_emri_karismaz():
    yabanci = {"symbol": "ETHUSDT", "algoType": "STOP_MARKET", "triggerPrice": "3000"}
    stop, hedef = ms.borsa_stop_hedef([yabanci], "BTCUSDT", "LONG", 100000)
    assert stop is None and hedef is None


def test_stop_alani_bos_gelirse_yok_sayilir():
    bos = {"symbol": "BTCUSDT", "algoType": "STOP_MARKET", "triggerPrice": "0"}
    stop, _ = ms.borsa_stop_hedef([bos], "BTCUSDT", "LONG", 100000)
    assert stop is None


def test_take_profit_stop_diye_okunmaz():
    """'STOP' kelimesi TAKE_PROFIT tipinde de gecebiliyor.

    Onceki sirada 'STOP' kontrolu once geldigi icin bazi TP emirleri
    stop diye kaydedilebilirdi. Kontrol sirasi bilincli: once TP.
    """
    tp = {"symbol": "BTCUSDT", "algoType": "TAKE_PROFIT", "stopPrice": "110000"}
    stop, hedef = ms.borsa_stop_hedef([tp], "BTCUSDT", "LONG", 100000)
    assert stop is None
    assert hedef == 110000


# ------------------------------------------------------------------ #
# CIKIS FIYATI — mark degil, GERCEK dolgular
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_cikis_fiyati_miktar_agirlikli(monkeypatch):
    """Kismi kapanislarda basit ortalama YANLIS olur.

    9 adet 100'den, 1 adet 200'den kapandiysa gercek cikis 110'dur,
    150 degil. Basit ortalama kucuk bir son dolgunun etkisini 10 katina
    cikarir ve R'yi olmadigi kadar iyi gosterir.
    """
    async def sahte(path, params=None):
        return [
            {"realizedPnl": "5", "qty": "9", "price": "100", "time": 1000},
            {"realizedPnl": "1", "qty": "1", "price": "200", "time": 2000},
        ]
    monkeypatch.setattr("app.services.binance_trade.signed", sahte)
    r = await ms._cikis_fiyati("BTCUSDT", 0)
    assert r["price"] == pytest.approx(110.0)
    assert r["ts"] == 2000


@pytest.mark.asyncio
async def test_acilis_dolgulari_cikisa_karismaz(monkeypatch):
    """realizedPnl == 0 olan dolgu ACILIS dolgusudur.

    Onu cikis ortalamasina katmak, cikis fiyatini girise dogru ceker ve
    her islemi oldugundan daha az kazancli/kayipli gosterir.
    """
    async def sahte(path, params=None):
        return [
            {"realizedPnl": "0", "qty": "10", "price": "50", "time": 500},
            {"realizedPnl": "7", "qty": "10", "price": "100", "time": 2000},
        ]
    monkeypatch.setattr("app.services.binance_trade.signed", sahte)
    r = await ms._cikis_fiyati("BTCUSDT", 0)
    assert r["price"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_cikis_okunamazsa_None_doner(monkeypatch):
    """Okunamayan cikis, UYDURULMUS bir cikistan iyidir.

    esitle() bu durumda kaydi acik birakip bir sonraki turda tekrar
    deniyor. Mark fiyatiyla kapatmak kolay olurdu ama pozisyon iki tarama
    arasinda kapandiysa mark o anki fiyattir, gercek cikis degil.
    """
    async def sahte(path, params=None):
        return []
    monkeypatch.setattr("app.services.binance_trade.signed", sahte)
    assert await ms._cikis_fiyati("BTCUSDT", 0) is None


@pytest.mark.asyncio
async def test_binance_patlarsa_None_doner(monkeypatch):
    async def sahte(path, params=None):
        raise RuntimeError("451")
    monkeypatch.setattr("app.services.binance_trade.signed", sahte)
    assert await ms._cikis_fiyati("BTCUSDT", 0) is None


# ------------------------------------------------------------------ #
# ESITLEME AKISI
# ------------------------------------------------------------------ #
class SahteDB:
    def __init__(self):
        self.yazilanlar = []

    def query_one(self, sql, params=()):
        return {"id": 1}

    def query(self, sql, params=()):
        return []

    def execute(self, sql, params=()):
        self.yazilanlar.append((sql, params))
        return 1

    def now_ms(self):
        return 1_700_000_000_000


@pytest.fixture()
def kurulum(monkeypatch):
    sdb = SahteDB()
    monkeypatch.setattr(ms, "db", sdb)
    monkeypatch.setattr("app.services.binance_trade.has_keys", lambda: True)
    monkeypatch.setattr("app.services.binance_trade.open_algo_orders",
                        _coro([]))
    async def baglam(symbol, interval="1h"):
        return {"rsi": 50}
    monkeypatch.setattr("app.services.mentor_journal.baglam_al", baglam)
    monkeypatch.setattr("app.services.mentor_journal.acik_islemler",
                        lambda uid: [])
    return sdb


def _coro(deger):
    async def f(*a, **k):
        return deger
    return f


@pytest.mark.asyncio
async def test_yeni_pozisyon_gunluge_yazilir(kurulum, monkeypatch):
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100000",
         "leverage": "5", "markPrice": "101000"}]))
    r = await ms.esitle(1)
    assert r["ok"] and r["acilan"] == 1
    sql, params = kurulum.yazilanlar[0]
    assert "INSERT INTO mentor_trades" in sql
    assert params[1] == "BTCUSDT" and params[2] == "LONG"
    assert params[4] == 100000.0


@pytest.mark.asyncio
async def test_otomatik_kayitta_kural_uyumu_BOS_kalir(kurulum, monkeypatch):
    """Kontrol listesi self-report'tur; borsa onu bilemez.

    Uydurulmus bir "kurallarima uydum" isareti, karnenin en degerli
    kirilimini — kurallara uydugumda ne oluyor / uymadigimda ne oluyor —
    tamamen anlamsiz yapardi. NULL kalinca o satir kohort kiyasina
    hic girmiyor (karne WHERE kural_uyumu==1 / ==0 ile ayiriyor).
    """
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100000",
         "leverage": "5", "markPrice": "101000"}]))
    await ms.esitle(1)
    _, params = kurulum.yazilanlar[0]
    # ... user_id, symbol, side, opened_at, entry, stop, target, qty,
    #     leverage, gerekce, etiket, kural_uyumu, kontrol_listesi ...
    assert params[11] is None, "kural_uyumu uydurulmamali"
    assert params[12] is None, "kontrol listesi uydurulmamali"


@pytest.mark.asyncio
async def test_stopsuz_pozisyon_stopsuz_kaydedilir(kurulum, monkeypatch):
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100000",
         "leverage": "5", "markPrice": "101000"}]))
    await ms.esitle(1)
    _, params = kurulum.yazilanlar[0]
    assert params[5] is None, "borsada stop yoktu, uydurulmamali"


@pytest.mark.asyncio
async def test_sifir_miktarli_pozisyon_atlanir(kurulum, monkeypatch):
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([
        {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "100000"}]))
    r = await ms.esitle(1)
    assert r["acilan"] == 0


@pytest.mark.asyncio
async def test_anahtar_yoksa_sessizce_durur(monkeypatch):
    monkeypatch.setattr("app.services.binance_trade.has_keys", lambda: False)
    r = await ms.esitle(1)
    assert r["ok"] is False and r["sebep"] == "anahtar_yok"


@pytest.mark.asyncio
async def test_zaten_kayitli_pozisyon_tekrar_yazilmaz(kurulum, monkeypatch):
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100000",
         "leverage": "5", "markPrice": "101000"}]))
    monkeypatch.setattr("app.services.mentor_journal.acik_islemler",
                        lambda uid: [{"id": 7, "symbol": "BTCUSDT",
                                      "side": "LONG", "entry": 100000,
                                      "initial_stop": 95000}])
    r = await ms.esitle(1)
    assert r["acilan"] == 0
    assert not kurulum.yazilanlar


@pytest.mark.asyncio
async def test_var_olan_stop_ASLA_guncellenmez(kurulum, monkeypatch):
    """initial_stop DONDURULMUS olmak zorunda.

    Kullanici stopu kara tasidiginda borsadaki stop degisir. Onu kayda
    yansitmak R'yi yeniden hesaplar ve her islem sihirli bicimde daha iyi
    gorunur — ev yapimi gunluklerin klasik kendini kandirma bicimi.
    """
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100000",
         "leverage": "5", "markPrice": "101000"}]))
    monkeypatch.setattr("app.services.binance_trade.open_algo_orders", _coro([
        {"symbol": "BTCUSDT", "algoType": "STOP_MARKET", "triggerPrice": "99500"}]))
    monkeypatch.setattr("app.services.mentor_journal.acik_islemler",
                        lambda uid: [{"id": 7, "symbol": "BTCUSDT",
                                      "side": "LONG", "entry": 100000,
                                      "initial_stop": 95000}])
    r = await ms.esitle(1)
    assert r["guncellenen"] == 0
    assert not kurulum.yazilanlar


@pytest.mark.asyncio
async def test_sonradan_kurulan_stop_bos_kayda_YAZILIR(kurulum, monkeypatch):
    """Bos olani doldurmak baska sey, dolu olani degistirmek baska sey.

    Kullanici pozisyonu acip stopu bir dakika sonra kuruyorsa, ilk
    senkronda stop bos kalir. Sonradan doldurmak dondurma kuralini
    bozmaz: hala islemin ILK stopu.
    """
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "100000",
         "leverage": "5", "markPrice": "101000"}]))
    monkeypatch.setattr("app.services.binance_trade.open_algo_orders", _coro([
        {"symbol": "BTCUSDT", "algoType": "STOP_MARKET", "triggerPrice": "95000"}]))
    monkeypatch.setattr("app.services.mentor_journal.acik_islemler",
                        lambda uid: [{"id": 7, "symbol": "BTCUSDT",
                                      "side": "LONG", "entry": 100000,
                                      "initial_stop": None}])
    r = await ms.esitle(1)
    assert r["guncellenen"] == 1
    sql, params = kurulum.yazilanlar[0]
    assert "UPDATE mentor_trades SET initial_stop" in sql
    assert params[0] == 95000


@pytest.mark.asyncio
async def test_kapanan_pozisyon_gercek_cikisla_kapatilir(kurulum, monkeypatch):
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([]))
    async def fills(path, params=None):
        return [{"realizedPnl": "12", "qty": "0.5", "price": "103000", "time": 9}]
    monkeypatch.setattr("app.services.binance_trade.signed", fills)
    monkeypatch.setattr("app.services.mentor_journal.acik_islemler",
                        lambda uid: [{"id": 7, "symbol": "BTCUSDT",
                                      "side": "LONG", "entry": 100000,
                                      "initial_stop": 95000, "opened_at": 1}])
    cagri = {}
    async def kapat(uid, tid, px, sebep="elle"):
        cagri.update(tid=tid, px=px, sebep=sebep)
        return {"ok": True}
    monkeypatch.setattr("app.services.mentor_journal.kapat", kapat)
    r = await ms.esitle(1)
    assert r["kapanan"] == 1
    assert cagri["tid"] == 7 and cagri["px"] == pytest.approx(103000.0)


@pytest.mark.asyncio
async def test_cikis_okunamazsa_kayit_ACIK_kalir(kurulum, monkeypatch):
    """Uydurma fiyatla kapatmaktansa acik birakip tekrar dene."""
    monkeypatch.setattr("app.services.binance_trade.position_rows", _coro([]))
    async def fills(path, params=None):
        return []
    monkeypatch.setattr("app.services.binance_trade.signed", fills)
    monkeypatch.setattr("app.services.mentor_journal.acik_islemler",
                        lambda uid: [{"id": 7, "symbol": "BTCUSDT",
                                      "side": "LONG", "entry": 100000,
                                      "initial_stop": 95000, "opened_at": 1}])
    async def kapat(*a, **k):
        raise AssertionError("cikis okunamazken kapatilmamali")
    monkeypatch.setattr("app.services.mentor_journal.kapat", kapat)
    r = await ms.esitle(1)
    assert r["kapanan"] == 0
